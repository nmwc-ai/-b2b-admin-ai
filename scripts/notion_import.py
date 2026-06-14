#!/usr/bin/env python3
"""Notion 'B2B 대시보드 상세' → Supabase deals 임포터 (Postgres 버전).

기본은 inspect (읽기 전용 진단). 실제 적재는 --apply, 비파괴 백필은 --backfill.
토큰/DB ID는 .env의 NOTION_TOKEN / NOTION_DB_ID, DB는 DATABASE_URL(Supabase) 사용.
urllib만 사용(추가 의존성 없음).
"""
import os
import sys
import json
import urllib.request
import urllib.error
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app import db  # noqa: E402

NOTION_VERSION = '2022-06-28'


def api(path, token, method='GET', body=None):
    url = 'https://api.notion.com/v1/' + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header('Authorization', 'Bearer ' + token)
    req.add_header('Notion-Version', NOTION_VERSION)
    req.add_header('Content-Type', 'application/json')
    with urllib.request.urlopen(req) as r:
        return json.load(r)


def fetch_all(db_id, token):
    rows, cursor = [], None
    while True:
        body = {'page_size': 100}
        if cursor:
            body['start_cursor'] = cursor
        d = api(f'databases/{db_id}/query', token, 'POST', body)
        rows.extend(d['results'])
        if not d.get('has_more'):
            break
        cursor = d['next_cursor']
    return rows


def txt(arr):
    return ''.join(t.get('plain_text', '') for t in (arr or [])).strip()


def prop(p):
    if p is None:
        return None
    t = p['type']
    v = p[t]
    if t in ('rich_text', 'title'):
        return txt(v)
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


def get(page, name):
    return prop(page['properties'].get(name))


STAGE_MAP = {
    '문의 인입': 'REVIEWING',
    '회신 완료': 'REPLIED',
    '의견 조율': 'NEGOTIATING',
    '제안 예정': 'REVIEWING',
    '제안 완료': 'QUOTED',
    '담당 에디터 연계': 'NEGOTIATING',
    '제휴 진행 중': 'CONTRACTING',
    '진행 완료': 'CLOSED_WON',
    '제안 거절': 'CLOSED_LOST',
    None: 'CLOSED_LOST',
}


def build_summary(g):
    lines = []
    pairs = [
        ('발행일', g('발행일')),
        ('공급가액(VAT별도)', g('공급가액(vat별도)')),
        ('입금', '완료' if g('입금 여부') else None),
        ('정산', '완료' if g('정산 여부') else None),
        ('세금계산서', g('세금계산서(승인번호)')),
        ('EDITOR', g('EDITOR')),
        ('PM', g('PM')),
        ('BD', g('BD')),
        ('견적서', g('견적서')),
        ('계약서', g('계약서')),
        ('Submission ID', g('Submission ID')),
    ]
    for k, v in pairs:
        if v not in (None, '', 0):
            lines.append(f'{k}: {v}')
    return '\n'.join(lines)


def yyyymm(date_str):
    if date_str and len(date_str) >= 7:
        return date_str[:4] + date_str[5:7]
    return '000000'


def main():
    apply = '--apply' in sys.argv
    backfill = '--backfill' in sys.argv

    token = os.getenv('NOTION_TOKEN')
    db_id = os.getenv('NOTION_DB_ID')
    if not token or not db_id:
        print('NOTION_TOKEN / NOTION_DB_ID 누락 (.env 확인)')
        sys.exit(1)
    if not os.getenv('DATABASE_URL'):
        print('DATABASE_URL 미설정 (.env 확인)')
        sys.exit(1)

    rows = fetch_all(db_id, token)
    print(f'총 {len(rows)}행 수신\n')

    status_dist = Counter()
    for pg in rows:
        status_dist[get(pg, '현황')] += 1
    print('=== 현황(노션) 분포 → 매핑된 stage ===')
    for s, c in status_dist.most_common():
        print(f'  {c:3d}  {s!r:24s} → {STAGE_MAP.get(s, "REVIEWING")}')

    # ── 백필 ──
    if backfill:
        filled = 0
        with db.get_conn() as conn:
            cur = conn.cursor()
            for pg in rows:
                prod = get(pg, '상품')
                opt = get(pg, '상품 옵션')
                if prod and opt:
                    name, desc = prod, opt
                elif prod:
                    name, desc = prod, ''
                elif opt:
                    name, desc = opt, ''
                else:
                    continue
                cur.execute(
                    "UPDATE deals SET cond_service_name=%s, cond_service_desc=%s "
                    "WHERE notion_page_id=%s "
                    "AND (cond_service_name IS NULL OR cond_service_name='')",
                    (name, desc, pg['id']),
                )
                filled += cur.rowcount
        print(f'\n[backfill] cond_service_name/desc 신규 채움: {filled}건')
        return

    if not apply:
        print('\n[inspect 모드] 적재 안 함. 실제 적재는 --apply (백필만 하려면 --backfill)')
        return

    # ── 적재 (기존 deals/activities/counter wipe 후) ──
    now = datetime.now().isoformat()

    def keyfn(pg):
        g = lambda n: prop(pg['properties'].get(n))
        return g('문의일') or g('발행일') or ''

    rows_sorted = sorted(rows, key=keyfn)
    seq, ym_max, records = {}, {}, []
    for pg in rows_sorted:
        g = lambda n: prop(pg['properties'].get(n))
        inq = g('문의일') or g('발행일')
        ym = yyyymm(inq)
        seq[ym] = seq.get(ym, 0) + 1
        ym_max[ym] = seq[ym]
        deal_id = f'AE-{ym}-{seq[ym]:03d}'
        svc = g('상품') or ''
        opt = g('상품 옵션')
        if opt:
            svc = (svc + ' / ' + opt).strip(' /')
        amount = g('공급가액(vat별도)')
        records.append({
            'deal_id': deal_id,
            'company': g('제휴처') or '(미상)',
            'contact_name': g('담당자명'),
            'contact_phone': g('담당자 연락처'),
            'email': g('담당자 이메일'),
            'inquiry_type': '아웃바운드' if g('아웃바운드') else '인바운드',
            'service_interest': svc or None,
            'stage': STAGE_MAP.get(g('현황'), 'REVIEWING'),
            'summary': build_summary(g) or None,
            'cond_unit_price': str(amount) if amount not in (None, '') else None,
            'created_at': inq or now,
            'updated_at': now,
            'notion_page_id': pg['id'],
        })

    with db.get_conn() as conn:
        cur = conn.cursor()
        cur.execute('SELECT COUNT(*) FROM deals')
        before = cur.fetchone()[0]
        cur.execute('DELETE FROM activities')
        cur.execute('DELETE FROM deals')
        cur.execute('DELETE FROM counter')
        print(f'\n기존 deals {before}건 삭제 + activities/counter 초기화')

        for rec in records:
            cur.execute("""
                INSERT INTO deals (deal_id, company, contact_name, contact_phone, email,
                    inquiry_type, service_interest, stage, summary, cond_unit_price,
                    created_at, updated_at, notion_page_id)
                VALUES (%(deal_id)s, %(company)s, %(contact_name)s, %(contact_phone)s, %(email)s,
                    %(inquiry_type)s, %(service_interest)s, %(stage)s, %(summary)s, %(cond_unit_price)s,
                    %(created_at)s, %(updated_at)s, %(notion_page_id)s)
            """, rec)

        for ym, n in ym_max.items():
            cur.execute('INSERT INTO counter (year_month, last_number) VALUES (%s, %s)', (ym, n))

        cur.execute('SELECT stage, COUNT(*) c FROM deals GROUP BY stage ORDER BY c DESC')
        dist = cur.fetchall()

    print(f'\n적재 완료: {len(records)}건')
    print('=== 적재 후 stage 분포 ===')
    for stage, c in dist:
        print(f'  {c:3d}  {stage}')


if __name__ == '__main__':
    main()
