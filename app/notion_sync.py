"""Notion 증분 동기화 — 폼→Notion으로 들어온 신규 문의를 딜로 적재.

실제 B2B 문의는 홈페이지 폼 → (Make 등) → Notion으로 들어온다(Gmail 아님).
notion_page_id로 dedup하여 **기존 딜은 건드리지 않고 신규 행만** 추가한다.
신규 딜은 AI 회신 초안을 생성한다(키 없으면 빈칸, graceful).
urllib만 사용(추가 의존성 없음).
"""

import os
import json
import logging
import urllib.request
from datetime import datetime

from app import db, ai

logger = logging.getLogger('notion_sync')
NOTION_VERSION = '2022-06-28'


def _api(path, token, method='GET', body=None):
    url = 'https://api.notion.com/v1/' + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Notion-Version', NOTION_VERSION)
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _fetch_all(db_id, token):
    rows, cursor = [], None
    while True:
        body = {'page_size': 100}
        if cursor:
            body['start_cursor'] = cursor
        d = _api(f'databases/{db_id}/query', token, 'POST', body)
        rows.extend(d['results'])
        if not d.get('has_more'):
            break
        cursor = d['next_cursor']
    return rows


def _prop(p):
    if p is None:
        return None
    t = p['type']
    v = p[t]
    if t in ('rich_text', 'title'):
        return ''.join(x.get('plain_text', '') for x in (v or [])).strip()
    if t in ('select', 'status'):
        return v['name'] if v else None
    if t == 'multi_select':
        return ', '.join(o['name'] for o in v)
    if t in ('phone_number', 'number', 'checkbox', 'url'):
        return v
    if t == 'date':
        return v['start'] if v else None
    if t == 'people':
        names = [x.get('name', '').strip() for x in v if x.get('name', '').strip()]
        return ', '.join(names) if names else None
    if t == 'files':
        return len(v)
    if t == 'formula':
        return v[v['type']]
    return None


def _g(pg, name):
    return _prop(pg['properties'].get(name))


STAGE_MAP = {
    '문의 인입': 'REVIEWING', '회신 완료': 'REPLIED', '의견 조율': 'NEGOTIATING',
    '제안 예정': 'REVIEWING', '제안 완료': 'QUOTED', '담당 에디터 연계': 'NEGOTIATING',
    '제휴 진행 중': 'CONTRACTING', '진행 완료': 'CLOSED_WON', '제안 거절': 'CLOSED_LOST',
    None: 'REVIEWING',  # 미지정 신규는 검토 대상으로
}


def _build_summary(pg):
    pairs = [
        ('공급가액(VAT별도)', _g(pg, '공급가액(vat별도)')),
        ('견적서', _g(pg, '견적서')),
        ('계약서', _g(pg, '계약서')),
        ('Submission ID', _g(pg, 'Submission ID')),
    ]
    lines = [f'{k}: {v}' for k, v in pairs if v not in (None, '', 0)]
    return '\n'.join(lines)


def _map_row(pg):
    svc = _g(pg, '상품') or ''
    opt = _g(pg, '상품 옵션')
    if opt:
        svc = (svc + ' / ' + opt).strip(' /')
    amount = _g(pg, '공급가액(vat별도)')
    return {
        'company': _g(pg, '제휴처') or '(미상)',
        'contact_name': _g(pg, '담당자명'),
        'contact_phone': _g(pg, '담당자 연락처'),
        'email': _g(pg, '담당자 이메일'),
        'inquiry_type': '아웃바운드' if _g(pg, '아웃바운드') else '인바운드',
        'service_interest': svc or None,
        'stage': STAGE_MAP.get(_g(pg, '현황'), 'REVIEWING'),
        'summary': _build_summary(pg) or None,
        'cond_unit_price': str(amount) if amount not in (None, '') else None,
        'cond_service_name': _g(pg, '상품') or None,
        'cond_service_desc': opt or None,
        'created_at_notion': _g(pg, '문의일'),
        'notion_page_id': pg['id'],
    }


def sync_new(dry_run: bool = False, with_ai: bool = True) -> dict:
    """Notion 신규 행만 딜로 추가. notion_page_id로 dedup."""
    token = os.getenv('NOTION_TOKEN')
    db_id = os.getenv('NOTION_DB_ID')
    if not token or not db_id:
        return {'error': 'NOTION_TOKEN/NOTION_DB_ID 미설정', 'new': 0, 'inserted': 0}

    try:
        rows = _fetch_all(db_id, token)
    except Exception as e:
        logger.error(f'[notion_sync] Notion 조회 실패: {e}')
        return {'error': str(e), 'new': 0, 'inserted': 0}

    existing = {pid for pid in _existing_notion_ids()}
    new_rows = [pg for pg in rows if pg['id'] not in existing]

    if dry_run:
        return {'total': len(rows), 'existing': len(existing), 'new': len(new_rows),
                'sample': [_map_row(pg)['company'] for pg in new_rows[:5]]}

    inserted = 0
    for pg in new_rows:
        try:
            m = _map_row(pg)
            reply = ''
            if with_ai and m['stage'] == 'REVIEWING':
                try:
                    reply = ai.generate_reply_draft(
                        m.get('summary') or m.get('service_interest') or '',
                        m.get('contact_name') or '', m.get('inquiry_type') or '')
                except Exception as e:
                    logger.error(f"[notion_sync] AI 회신 생성 실패({m['company']}): {e}")
            deal_id = db.insert_deal({
                'company': m['company'], 'contact_name': m['contact_name'],
                'contact_phone': m['contact_phone'], 'email': m['email'],
                'inquiry_type': m['inquiry_type'], 'service_interest': m['service_interest'],
                'summary': m['summary'], 'reply_draft': reply,
            })
            fields = {
                'stage': m['stage'], 'cond_unit_price': m['cond_unit_price'],
                'cond_service_name': m['cond_service_name'], 'cond_service_desc': m['cond_service_desc'],
                'notion_page_id': m['notion_page_id'],
            }
            if m.get('created_at_notion'):
                fields['created_at'] = m['created_at_notion']
            db.update_deal(deal_id, fields)
            db.log_activity(deal_id, 'inbound_received', {'source': 'notion', 'company': m['company']})
            inserted += 1
        except Exception as e:
            logger.error(f"[notion_sync] 딜 적재 실패({pg.get('id')}): {e}")

    return {'total': len(rows), 'existing': len(existing), 'new': len(new_rows), 'inserted': inserted}


def _existing_notion_ids():
    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT notion_page_id FROM deals WHERE notion_page_id IS NOT NULL')
        return [r[0] for r in cur.fetchall()]
