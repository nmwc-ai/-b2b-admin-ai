"""ANTIEGG B2B 어드민 — 클라우드(Vercel + Supabase + Claude) FastAPI 앱.

MVP 범위: 인박스/파이프라인/회사/세팅, 딜 액션, AI 회신 재생성, Gmail 초안 저장(동기),
일1회 인박스 폴링(cron) + 수동 폴링. 견적/계약/서명/노크는 v2.
"""

import os
import json
import base64
import logging
import secrets as _secrets
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from dotenv import load_dotenv

from app import db, ai, settings, inbox, email_client
from app import examples as ex_svc
from app.error_log import install_db_handler

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
install_db_handler()

STAGE_OPTIONS = [
    'INQUIRY', 'REVIEWING', 'REPLIED', 'NEGOTIATING',
    'QUOTED', 'CONTRACTING', 'SIGNED',
    'CLOSED_WON', 'KNOCK_REPLY', 'KNOCK_QUOTE', 'CLOSED_LOST',
]
ACTIVE_STAGES = {'REVIEWING', 'REPLIED', 'NEGOTIATING', 'QUOTED', 'CONTRACTING', 'SIGNED'}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 서버리스: init_db는 런타임에서 호출하지 않음 (scripts/init_db.py로 1회 실행).
    yield


app = FastAPI(lifespan=lifespan)

TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
templates = Jinja2Templates(directory=TEMPLATE_DIR)


# ── Basic Auth ──────────────────────────────────────────────────────────────
_ADMIN_USER = os.getenv('ADMIN_USER', 'antiegg')
_ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD', '')
_PUBLIC_PREFIXES = ('/sign', '/cron')  # 고객 서명 / Vercel Cron(CRON_SECRET로 보호)


@app.middleware('http')
async def basic_auth(request: Request, call_next):
    path = request.url.path
    if _ADMIN_PASSWORD and not path.startswith(_PUBLIC_PREFIXES):
        auth = request.headers.get('Authorization', '')
        ok = False
        if auth.startswith('Basic '):
            try:
                u, _, p = base64.b64decode(auth[6:]).decode('utf-8').partition(':')
                ok = (_secrets.compare_digest(u, _ADMIN_USER)
                      and _secrets.compare_digest(p, _ADMIN_PASSWORD))
            except Exception:
                ok = False
        if not ok:
            return HTMLResponse(
                '인증이 필요합니다', status_code=401,
                headers={'WWW-Authenticate': 'Basic realm="ANTIEGG B2B"'},
            )
    return await call_next(request)


# ── Jinja 필터 ──────────────────────────────────────────────────────────────
def _relative_time(value):
    if not value:
        return '—'
    try:
        dt = datetime.fromisoformat(value) if isinstance(value, str) else value
    except Exception:
        return str(value)[:10]
    now = datetime.now()
    diff = now - dt
    secs = diff.total_seconds()
    if secs < 0:
        return dt.strftime('%m/%d')
    if secs < 60:
        return '방금 전'
    if secs < 3600:
        return f'{int(secs // 60)}분 전'
    if dt.date() == now.date():
        return f'오늘 {dt.strftime("%H:%M")}'
    if (now.date() - dt.date()).days == 1:
        return '어제'
    if diff.days < 7:
        return f'{diff.days}일 전'
    return dt.strftime('%m/%d')


templates.env.filters['relative_time'] = _relative_time


def _format_activity(activity):
    t = activity.get('type', '')
    p = activity.get('payload') or {}
    if t == 'stage_changed':
        return f"Stage {p.get('from','?')} → {p.get('to','?')}"
    if t == 'trigger_fired':
        labels = {'reply_send': '회신', 'quote_gen': '견적서', 'contract_gen': '계약서',
                  'contract_send': '전자계약', 'knock_send': '노크'}
        label = labels.get(p.get('trigger', ''), p.get('trigger', '?'))
        return f"{label} {p.get('from','?')} → {p.get('to','?')}"
    if t == 'inbound_received':
        return f"문의 수신: {p.get('subject', '')}"
    if t == 'signed':
        return "전자서명 완료"
    if t == 'note_added':
        return f"노트: {p.get('note', p.get('text', ''))}"
    return t


templates.env.filters['format_activity'] = _format_activity


# ── 인박스 분류 ───────────────────────────────────────────────────────────────
def _classify_inbox_now(deal: dict) -> dict:
    if deal.get('trigger_reply_send') == 'ERROR':
        return {'dot': 'critical', 'label': '회신 Gmail 저장 실패 — 재시도 필요'}
    if deal.get('trigger_reply_send') == 'DRAFT':
        return {'dot': 'attention', 'label': '회신 초안 Gmail에 저장됨 — 검토·발송 필요'}
    if deal.get('stage') == 'REVIEWING':
        if (deal.get('reply_draft') or '').strip():
            return {'dot': 'attention', 'label': '회신 초안 작성됨 — 검토 후 Gmail 저장'}
        return {'dot': 'action', 'label': '새 문의 — 회신 초안 만들기'}
    if deal.get('stage') in ('KNOCK_REPLY', 'KNOCK_QUOTE'):
        return {'dot': 'action', 'label': '노크 메일 발송 필요'}
    return {'dot': 'action', 'label': '확인 필요'}


def _classify_inbox_upcoming(deal: dict) -> dict:
    return {'dot': 'upcoming', 'label': f'{deal.get("stage", "")} 후 6일 — 노크 임박'}


# ── 파이프라인 ────────────────────────────────────────────────────────────────
PHASE_MAP = {
    'REVIEWING': '새 문의', 'REPLIED': '응답·협상', 'NEGOTIATING': '응답·협상',
    'KNOCK_REPLY': '응답·협상', 'QUOTED': '견적·계약', 'CONTRACTING': '견적·계약',
    'KNOCK_QUOTE': '견적·계약', 'SIGNED': '체결', 'CLOSED_WON': '종료', 'CLOSED_LOST': '종료',
}
PHASE_ORDER = ['새 문의', '응답·협상', '견적·계약', '체결']
STAGE_ABBR = {
    'REVIEWING': 'REVIEW.', 'REPLIED': 'REPLIED', 'NEGOTIATING': 'NEGOT.',
    'KNOCK_REPLY': 'KNOCK_R.', 'QUOTED': 'QUOTED', 'CONTRACTING': 'CONTR.',
    'KNOCK_QUOTE': 'KNOCK_Q.', 'SIGNED': 'SIGNED', 'CLOSED_WON': 'CLOSED_W', 'CLOSED_LOST': 'CLOSED_L',
}


def _pipeline_marker(deal: dict):
    if deal.get('trigger_reply_send') == 'ERROR':
        return 'critical'
    if deal.get('trigger_reply_send') == 'DRAFT':
        return 'attention'
    if deal.get('stage') in ('REPLIED', 'QUOTED'):
        try:
            updated = datetime.fromisoformat(deal.get('updated_at', ''))
            if 6 <= (datetime.now() - updated).days < 7:
                return 'upcoming'
        except Exception:
            pass
    return None


# ── Deal 패널 컨텍스트 ────────────────────────────────────────────────────────
def _extract_docs(deal: dict) -> dict:
    """노션 임포트가 summary에 보존한 견적서/계약서 URL을 추출.
    형식: 한 줄당 "견적서: <url>" / "계약서: <url>"."""
    out = {'quote_url': None, 'contract_url': None}
    for line in (deal.get('summary') or '').splitlines():
        s = line.strip()
        if s.startswith('견적서:'):
            v = s.split(':', 1)[1].strip()
            if v.startswith('http'):
                out['quote_url'] = v
        elif s.startswith('계약서:'):
            v = s.split(':', 1)[1].strip()
            if v.startswith('http'):
                out['contract_url'] = v
    return out


def _amount_display(deal: dict) -> str:
    digits = ''.join(c for c in str(deal.get('cond_unit_price') or '') if c.isdigit())
    return f'₩{int(digits):,}' if digits else ''


def _panel_context(deal: dict) -> dict:
    company = deal.get('company') or '(회사명 미상)'
    docs = _extract_docs(deal)
    return {
        'deal': deal,
        'history': db.get_deals_by_company(company, exclude_deal_id=deal['deal_id']),
        'activities': db.get_activities(deal['deal_id']),
        'has_draft': deal.get('trigger_reply_send') == 'DRAFT',
        'stage_options': STAGE_OPTIONS,
        'quote_url': docs['quote_url'],
        'contract_url': docs['contract_url'],
        'amount_display': _amount_display(deal),
    }


def _hx_or_redirect(request: Request, deal_id: str, toast: dict = None):
    if request.headers.get('HX-Request'):
        deal = db.get_deal(deal_id)
        if not deal:
            return HTMLResponse('<div class="panel-body"><div class="empty">딜을 찾을 수 없습니다</div></div>', status_code=404)
        ctx = _panel_context(deal)
        ctx['request'] = request
        response = templates.TemplateResponse('deal_panel.html', ctx)
        if toast:
            response.headers['HX-Trigger'] = json.dumps({'toast': toast})
        return response
    return RedirectResponse(f'/deals/{deal_id}', status_code=303)


def _hx_toast_only(request: Request, toast: dict):
    if request.headers.get('HX-Request'):
        response = HTMLResponse('', status_code=200)
        response.headers['HX-Trigger'] = json.dumps({'toast': toast})
        return response
    return None


# ── 페이지 ────────────────────────────────────────────────────────────────────
@app.get('/', response_class=HTMLResponse)
async def inbox_page(request: Request):
    stage_counts = db.get_stage_counts()
    active_count = sum(stage_counts.get(s, 0) for s in ACTIVE_STAGES)
    now_items = [{'deal': d, 'classify': _classify_inbox_now(d)} for d in db.get_inbox_now()]
    upcoming_items = [{'deal': d, 'classify': _classify_inbox_upcoming(d)} for d in db.get_inbox_upcoming()]

    WEEKDAYS_KO = ['월', '화', '수', '목', '금', '토', '일']
    today_dt = datetime.now()
    today_str = today_dt.strftime('%-m월 %-d일') + f' {WEEKDAYS_KO[today_dt.weekday()]}요일'
    recent_error_count = db.count_errors_since((today_dt - timedelta(hours=24)).isoformat())

    return templates.TemplateResponse('inbox.html', {
        'request': request,
        'recent_error_count': recent_error_count,
        'today': today_str,
        'active_count': active_count,
        'now_items': now_items,
        'upcoming_items': upcoming_items,
    })


@app.get('/dashboard', response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """내부 팀 공유용 개요 — 영업 현황 한눈에."""
    import re
    from collections import Counter

    deals = db.get_all_deals()
    total = len(deals)
    won = sum(1 for d in deals if d.get('stage') == 'CLOSED_WON')
    lost = sum(1 for d in deals if d.get('stage') == 'CLOSED_LOST')
    active = sum(1 for d in deals if d.get('stage') not in ('CLOSED_WON', 'CLOSED_LOST'))
    win_rate = round(won / (won + lost) * 100) if (won + lost) else 0

    # 공급가액(원) — 숫자만 추출, 입력된 건만
    def _won_amount(v):
        digits = re.sub(r'[^0-9]', '', str(v or ''))
        return int(digits) if digits else 0
    priced = [_won_amount(d.get('cond_unit_price')) for d in deals if _won_amount(d.get('cond_unit_price'))]
    total_value = sum(priced)
    value_count = len(priced)

    # 월별 문의 추이 — 최근 13개월
    ym_counts = Counter((d.get('created_at') or '')[:7] for d in deals if (d.get('created_at') or '')[:7])
    today = datetime.now()
    months = []
    y, m = today.year, today.month
    keys = []
    for _ in range(13):
        keys.append(f'{y:04d}-{m:02d}')
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    keys = list(reversed(keys))
    max_cnt = max((ym_counts.get(k, 0) for k in keys), default=0) or 1
    for k in keys:
        c = ym_counts.get(k, 0)
        months.append({'label': k[2:].replace('-', '.'), 'count': c, 'pct': round(c / max_cnt * 100)})

    # 유형(인바운드/아웃바운드)
    type_counts = Counter((d.get('inquiry_type') or '(없음)') for d in deals)
    types = [{'name': n, 'count': c} for n, c in type_counts.most_common()]

    # 상품 상위 (입력된 것만)
    prod_counts = Counter(
        (d.get('service_interest') or '').strip()
        for d in deals if (d.get('service_interest') or '').strip()
    )
    top_products = [{'name': n, 'count': c} for n, c in prod_counts.most_common(6)]

    # 파이프라인 단계 분포 (진행중 + 종료)
    stage_counts = Counter(d.get('stage') or 'REVIEWING' for d in deals)
    stage_rows = [{'stage': s, 'count': stage_counts.get(s, 0)}
                  for s in STAGE_OPTIONS if stage_counts.get(s, 0)]

    return templates.TemplateResponse('dashboard.html', {
        'request': request,
        'total': total, 'won': won, 'lost': lost, 'active': active, 'win_rate': win_rate,
        'total_value': f'{total_value:,}', 'value_count': value_count,
        'months': months,
        'types': types, 'top_products': top_products, 'stage_rows': stage_rows,
        'generated_at': today.strftime('%Y-%m-%d %H:%M'),
    })


@app.get('/pipeline', response_class=HTMLResponse)
async def pipeline_page(request: Request, show_closed: bool = False):
    all_deals = db.get_all_deals()
    phases = {p: [] for p in PHASE_ORDER}
    closed_lost = []
    for d in all_deals:
        stage = d.get('stage') or 'REVIEWING'
        phase = PHASE_MAP.get(stage, '새 문의')
        item = {'deal': d, 'sub_stage': STAGE_ABBR.get(stage, stage), 'marker': _pipeline_marker(d)}
        (closed_lost if phase == '종료' else phases[phase]).append(item)
    for p in PHASE_ORDER:
        phases[p].sort(key=lambda x: x['deal'].get('updated_at') or '', reverse=True)
    closed_lost.sort(key=lambda x: x['deal'].get('updated_at') or '', reverse=True)

    return templates.TemplateResponse('pipeline.html', {
        'request': request,
        'phase_order': PHASE_ORDER,
        'phases': phases,
        'closed_lost': closed_lost,
        'active_count': sum(len(phases[p]) for p in PHASE_ORDER),
        'closed_count': len(closed_lost),
        'show_closed': show_closed,
    })


@app.get('/companies', response_class=HTMLResponse)
async def companies_page(request: Request):
    companies = db.get_companies_summary()
    return templates.TemplateResponse('companies.html', {
        'request': request, 'companies': companies, 'total': len(companies),
    })


@app.get('/companies/{company:path}', response_class=HTMLResponse)
async def company_detail_page(request: Request, company: str):
    deals = db.get_deals_by_company(company)
    active = [d for d in deals if d.get('stage') not in ('CLOSED_WON', 'CLOSED_LOST')]
    closed = [d for d in deals if d.get('stage') in ('CLOSED_WON', 'CLOSED_LOST')]
    first_at = min((d.get('created_at') for d in deals if d.get('created_at')), default=None)
    last_at = max((d.get('updated_at') for d in deals if d.get('updated_at')), default=None)
    return templates.TemplateResponse('company_detail.html', {
        'request': request, 'company': company, 'deals': deals,
        'active': active, 'closed': closed,
        'first_at': first_at, 'last_at': last_at, 'stage_abbr': STAGE_ABBR,
    })


@app.get('/search', response_class=HTMLResponse)
async def search(request: Request, q: str = ''):
    q = (q or '').strip()
    if not q:
        return templates.TemplateResponse('search_results.html', {
            'request': request, 'q': '', 'exact': None, 'deals': [], 'stage_abbr': STAGE_ABBR,
        })
    result = db.search_deals(q, limit=20)
    exact = result['exact_deal']
    deals = result['deals']
    if exact:
        deals = [d for d in deals if d['deal_id'] != exact['deal_id']]
    return templates.TemplateResponse('search_results.html', {
        'request': request, 'q': q, 'exact': exact, 'deals': deals, 'stage_abbr': STAGE_ABBR,
    })


@app.get('/errors', response_class=HTMLResponse)
async def errors_page(request: Request):
    return templates.TemplateResponse('errors.html', {
        'request': request, 'errors': db.get_recent_errors(limit=100),
    })


# ── 세팅 ──────────────────────────────────────────────────────────────────────
@app.get('/settings', response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse('settings.html', {
        'request': request,
        'groups': settings.grouped_editable(),
        'saved': request.query_params.get('saved') == '1',
    })


@app.post('/settings')
async def settings_save(request: Request):
    form = await request.form()
    settings.set_many({k: form.get(k, '') for k in settings.EDITABLE_KEYS})
    return RedirectResponse('/settings?saved=1', status_code=303)


# ── 사례 (few-shot) ──────────────────────────────────────────────────────────
@app.get('/examples', response_class=HTMLResponse)
async def examples_page(request: Request):
    items = ex_svc.list_all()
    items.sort(key=lambda e: e.get('created_at') or '', reverse=True)
    return templates.TemplateResponse('examples.html', {'request': request, 'items': items})


@app.post('/examples/delete/{ex_id}')
async def examples_delete(request: Request, ex_id: str):
    if not ex_svc.delete_by_id(ex_id):
        return HTMLResponse('Not found', status_code=404)
    if request.headers.get('HX-Request'):
        resp = HTMLResponse('', status_code=200)
        resp.headers['HX-Trigger'] = json.dumps({'toast': {'message': '사례를 삭제했습니다', 'type': 'success'}})
        return resp
    return RedirectResponse('/examples', status_code=303)


@app.post('/examples/{ex_id}/update')
async def examples_update(request: Request, ex_id: str, reply: str = Form('')):
    if not ex_svc.update_reply(ex_id, reply):
        return HTMLResponse('Not found', status_code=404)
    if request.headers.get('HX-Request'):
        resp = templates.TemplateResponse('_examples_card.html', {'request': request, 'e': ex_svc.get_by_id(ex_id)})
        resp.headers['HX-Trigger'] = json.dumps({'toast': {'message': '사례를 수정했습니다', 'type': 'success'}})
        return resp
    return RedirectResponse('/examples', status_code=303)


@app.get('/examples/new', response_class=HTMLResponse)
async def examples_new_form(request: Request):
    return templates.TemplateResponse('examples_new.html', {
        'request': request, 'inquiry_types': ex_svc.INQUIRY_TYPES,
    })


@app.post('/examples/new')
async def examples_new_submit(
    request: Request,
    inquiry_type: str = Form('기타'),
    summary: str = Form(''),
    contact_name: str = Form(''),
    reply: str = Form(...),
):
    ex_svc.add_manual(inquiry_type, summary, contact_name, reply)
    return RedirectResponse('/examples', status_code=303)


# ── 딜 수동 추가 ──────────────────────────────────────────────────────────────
@app.get('/deals/new', response_class=HTMLResponse)
async def deal_new_form(request: Request):
    return templates.TemplateResponse('deal_new.html', {'request': request})


@app.post('/deals/new')
async def deal_new_submit(
    request: Request,
    company: str = Form(...),
    contact_name: str = Form(...),
    email: str = Form(...),
    contact_title: str = Form(''),
    contact_phone: str = Form(''),
    inquiry_type: str = Form(''),
    service_interest: str = Form(''),
    summary: str = Form(''),
):
    deal_id = db.insert_deal({
        'company': company.strip(), 'contact_name': contact_name.strip(),
        'contact_title': contact_title.strip(), 'contact_phone': contact_phone.strip(),
        'email': email.strip(), 'inquiry_type': inquiry_type.strip(),
        'service_interest': service_interest.strip(), 'summary': summary.strip(),
        'reply_draft': None,
    })
    db.log_activity(deal_id, 'note_added', {'note': '수동으로 딜 추가됨'})
    return RedirectResponse(f'/?open={deal_id}', status_code=303)


# ── 딜 상세/패널 ──────────────────────────────────────────────────────────────
@app.get('/deals/{deal_id}/panel', response_class=HTMLResponse)
async def deal_panel(request: Request, deal_id: str):
    deal = db.get_deal(deal_id)
    if not deal:
        return HTMLResponse('<div class="panel-body"><div class="empty">딜을 찾을 수 없습니다</div></div>', status_code=404)
    ctx = _panel_context(deal)
    ctx['request'] = request
    return templates.TemplateResponse('deal_panel.html', ctx)


@app.get('/deals/{deal_id}')
async def deal_detail(deal_id: str):
    if not db.get_deal(deal_id):
        return HTMLResponse('딜을 찾을 수 없습니다', status_code=404)
    return RedirectResponse(url=f'/?open={deal_id}', status_code=302)


@app.post('/deals/{deal_id}/delete')
async def delete_deal(request: Request, deal_id: str):
    deal = db.get_deal(deal_id)
    if not deal:
        return HTMLResponse('딜을 찾을 수 없습니다', status_code=404)
    db.delete_deal(deal_id)
    if request.headers.get('HX-Request'):
        resp = HTMLResponse('', status_code=200)
        resp.headers['HX-Trigger'] = json.dumps({
            'dealDeleted': {'deal_id': deal_id},
            'toast': {'message': f'{deal.get("company") or deal_id} 딜을 삭제했습니다', 'type': 'success'},
        })
        return resp
    return RedirectResponse('/', status_code=303)


@app.post('/deals/{deal_id}/stage')
async def update_stage(request: Request, deal_id: str, stage: str = Form(...)):
    old = db.get_deal(deal_id)
    old_stage = (old or {}).get('stage') or 'REVIEWING'
    if old_stage == stage:
        return _hx_or_redirect(request, deal_id)
    db.update_deal(deal_id, {'stage': stage})
    db.log_activity(deal_id, 'stage_changed', {'from': old_stage, 'to': stage})
    return _hx_or_redirect(request, deal_id, toast={
        'message': f'Stage {old_stage} → {stage}', 'type': 'info',
        'undo': {'deal_id': deal_id, 'stage': old_stage},
    })


@app.post('/deals/{deal_id}/info')
async def update_deal_info(
    request: Request,
    deal_id: str,
    company: str = Form(''),
    contact_name: str = Form(''),
    contact_title: str = Form(''),
    contact_phone: str = Form(''),
    email: str = Form(''),
    inquiry_type: str = Form(''),
    service_interest: str = Form(''),
    summary: str = Form(''),
):
    db.update_deal(deal_id, {
        'company': company.strip(), 'contact_name': contact_name.strip(),
        'contact_title': contact_title.strip(), 'contact_phone': contact_phone.strip(),
        'email': email.strip(), 'inquiry_type': inquiry_type.strip(),
        'service_interest': service_interest.strip(), 'summary': summary.strip(),
    })
    db.log_activity(deal_id, 'note_added', {'note': '기본 정보 수정'})
    return _hx_or_redirect(request, deal_id, toast={'message': '기본 정보가 저장되었습니다', 'type': 'success'})


@app.post('/deals/{deal_id}/reply-draft')
async def update_reply_draft(request: Request, deal_id: str, reply_draft: str = Form('')):
    db.update_deal(deal_id, {'reply_draft': reply_draft})
    toast_resp = _hx_toast_only(request, {'message': '회신 초안 저장됨', 'type': 'success'})
    return toast_resp if toast_resp is not None else RedirectResponse(f'/deals/{deal_id}', status_code=303)


@app.post('/deals/{deal_id}/regenerate-reply')
async def regenerate_reply(request: Request, deal_id: str):
    deal = db.get_deal(deal_id)
    if not deal:
        return HTMLResponse('딜을 찾을 수 없습니다', status_code=404)
    try:
        reply = ai.generate_reply_draft(
            deal.get('summary') or '', deal.get('contact_name') or '', deal.get('inquiry_type') or '',
        )
        db.update_deal(deal_id, {'reply_draft': reply})
        db.log_activity(deal_id, 'note_added', {'note': 'AI 회신 재생성'})
        toast = {'message': 'AI 회신 초안을 생성했습니다', 'type': 'success'}
    except Exception as e:
        logging.error(f'[regenerate-reply] {deal_id}: {e}')
        toast = {'message': f'AI 회신 생성 실패: {e}', 'type': 'error'}
    return _hx_or_redirect(request, deal_id, toast=toast)


@app.post('/deals/{deal_id}/reply-send')
async def reply_send(request: Request, deal_id: str):
    """회신 초안을 Gmail 임시보관함에 저장(동기). 발송은 사람이 Gmail에서."""
    deal = db.get_deal(deal_id)
    if not deal:
        return HTMLResponse('딜을 찾을 수 없습니다', status_code=404)
    body = (deal.get('reply_draft') or '').strip()
    to = (deal.get('email') or '').strip()
    if not body:
        return _hx_or_redirect(request, deal_id, toast={'message': '회신 초안이 비어있습니다', 'type': 'error'})
    if not to or to == '미상':
        return _hx_or_redirect(request, deal_id, toast={'message': '수신 이메일이 없습니다 (기본 정보에서 입력)', 'type': 'error'})

    subject = f"[ANTIEGG] {deal.get('company') or ''} 문의 회신".strip()
    old = deal.get('trigger_reply_send') or 'IDLE'
    try:
        email_client.create_draft(to, subject, body)
        db.update_deal(deal_id, {'trigger_reply_send': 'DRAFT'})
        db.log_activity(deal_id, 'trigger_fired', {'trigger': 'reply_send', 'from': old, 'to': 'DRAFT'})
        toast = {'message': 'Gmail 임시보관함에 회신 초안을 저장했습니다', 'type': 'success'}
    except Exception as e:
        logging.error(f'[reply-send] {deal_id}: {e}')
        db.update_deal(deal_id, {'trigger_reply_send': 'ERROR'})
        toast = {'message': f'Gmail 초안 저장 실패: {e}', 'type': 'error'}
    return _hx_or_redirect(request, deal_id, toast=toast)


@app.post('/deals/{deal_id}/conditions')
async def update_conditions(
    request: Request,
    deal_id: str,
    cond_service_name: str = Form(''),
    cond_service_desc: str = Form(''),
    cond_unit_price: str = Form(''),
    cond_quantity: str = Form(''),
    cond_payment_terms: str = Form(''),
    cond_delivery_scope: str = Form(''),
    cond_notes: str = Form(''),
    cond_company_addr: str = Form(''),
    cond_company_ceo: str = Form(''),
    cond_company_biz_no: str = Form(''),
    cond_contract_start: str = Form(''),
    cond_contract_end: str = Form(''),
):
    db.update_deal(deal_id, {
        'cond_service_name': cond_service_name, 'cond_service_desc': cond_service_desc,
        'cond_unit_price': cond_unit_price, 'cond_quantity': cond_quantity,
        'cond_payment_terms': cond_payment_terms, 'cond_delivery_scope': cond_delivery_scope,
        'cond_notes': cond_notes, 'cond_company_addr': cond_company_addr,
        'cond_company_ceo': cond_company_ceo, 'cond_company_biz_no': cond_company_biz_no,
        'cond_contract_start': cond_contract_start, 'cond_contract_end': cond_contract_end,
    })
    toast_resp = _hx_toast_only(request, {'message': '딜 조건 저장됨', 'type': 'success'})
    return toast_resp if toast_resp is not None else RedirectResponse(f'/deals/{deal_id}', status_code=303)


# ── 인박스 폴링 (cron + 수동) ─────────────────────────────────────────────────
@app.get('/cron/daily')
async def cron_daily(request: Request):
    """Vercel Cron 일1회. Authorization: Bearer <CRON_SECRET> 검증."""
    secret = os.getenv('CRON_SECRET', '')
    if secret:
        auth = request.headers.get('Authorization', '')
        if auth != f'Bearer {secret}':
            return JSONResponse({'error': 'unauthorized'}, status_code=401)
    result = inbox.poll_inbox()
    logging.info(f'[cron/daily] {result}')
    return JSONResponse({'ok': True, **result})


@app.post('/admin/poll-inbox')
async def admin_poll_inbox(request: Request):
    """'지금 인박스 확인' 버튼 — 동기 폴링."""
    result = inbox.poll_inbox()
    msg = f"수신 {result['fetched']}건 · 새 딜 {result['created']}건"
    if result.get('errors'):
        msg += f" · 오류 {result['errors']}건"
    if request.headers.get('HX-Request'):
        resp = HTMLResponse('', status_code=200)
        resp.headers['HX-Trigger'] = json.dumps({'toast': {'message': msg, 'type': 'info'}})
        return resp
    return JSONResponse({'ok': True, **result})
