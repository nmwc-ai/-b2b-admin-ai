"""DB 백업 — 전체 테이블을 JSON 스냅샷으로 떠서 Gmail로 발송(오프사이트).

Supabase 무료 플랜은 자동백업이 제한적이고 DB가 단일 장애점이라, 앱 레벨에서
매일 스냅샷을 만들어 운영자 Gmail로 보낸다(추가 자격증명 불필요, DB가 죽어도 생존).
복구는 scripts/restore_backup.py 참고.
"""

import io
import os
import json
import logging
from datetime import datetime

from app import db, email_client

logger = logging.getLogger('backup')

# 핵심 데이터만 (errors는 재생성 가능/노이즈라 제외)
TABLES = ['deals', 'activities', 'settings', 'examples', 'style_guide', 'counter']


def export_snapshot() -> dict:
    """모든 핵심 테이블을 dict로 추출."""
    snap = {'generated_at': datetime.now().isoformat(), 'tables': {}}
    with db.get_conn() as conn:
        cur = db._cur(conn)
        for t in TABLES:
            try:
                cur.execute(f'SELECT * FROM {t}')
                snap['tables'][t] = [dict(r) for r in cur.fetchall()]
            except Exception as e:
                logger.error(f'[backup] {t} 추출 실패: {e}')
                snap['tables'][t] = []
    return snap


def run_backup(to: str = None) -> dict:
    """스냅샷을 만들어 JSON 첨부로 Gmail 발송."""
    to = to or os.getenv('BACKUP_EMAIL') or os.getenv('DIRECTOR_EMAIL') or os.getenv('GMAIL_ADDRESS')
    if not to:
        return {'error': '백업 수신 이메일 미설정 (BACKUP_EMAIL/DIRECTOR_EMAIL/GMAIL_ADDRESS)'}

    snap = export_snapshot()
    counts = {t: len(rows) for t, rows in snap['tables'].items()}
    total = sum(counts.values())
    data = json.dumps(snap, ensure_ascii=False, indent=2).encode('utf-8')
    date = datetime.now().strftime('%Y%m%d_%H%M')
    fname = f'antiegg_b2b_backup_{date}.json'
    body = (f'ANTIEGG B2B 어드민 DB 백업\n생성: {snap["generated_at"]}\n\n'
            + '\n'.join(f'  {t}: {c}행' for t, c in counts.items())
            + f'\n  합계: {total}행\n\n복구: scripts/restore_backup.py')
    try:
        email_client.send_with_attachment(
            to, f'[ANTIEGG B2B 백업] {date} · {total}행', body, fname, data)
    except Exception as e:
        logger.error(f'[backup] 발송 실패: {e}')
        return {'error': str(e), 'counts': counts, 'total': total}
    return {'ok': True, 'to': to, 'file': fname, 'counts': counts, 'total': total,
            'size_kb': round(len(data) / 1024, 1)}
