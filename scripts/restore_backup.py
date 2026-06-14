#!/usr/bin/env python3
"""백업 JSON 스냅샷 → Supabase 복구. 로컬에서 실행.

    python scripts/restore_backup.py <백업파일.json>          # 진단(읽기전용)
    python scripts/restore_backup.py <백업파일.json> --apply  # 실제 복구(해당 테이블 wipe 후 적재)

⚠️ --apply는 스냅샷에 있는 테이블을 비우고 다시 채운다. 실행 전 현재 상태를 한 번 더 백업할 것.
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app import db  # noqa: E402

# 의존성 순서 (activities가 deals 참조 X지만 일관성 위해 deals 먼저)
ORDER = ['counter', 'settings', 'style_guide', 'examples', 'deals', 'activities']


def restore(path, apply):
    with open(path, encoding='utf-8') as f:
        snap = json.load(f)
    tables = snap.get('tables', {})
    print(f"스냅샷 생성시각: {snap.get('generated_at')}")
    for t in ORDER:
        rows = tables.get(t, [])
        print(f"  {t}: {len(rows)}행")
    if not apply:
        print("\n[진단] 복구 안 함. 실제 복구는 --apply")
        return

    with db.get_conn() as conn:
        cur = conn.cursor()
        for t in ORDER:
            rows = tables.get(t)
            if rows is None:
                continue
            cur.execute(f'DELETE FROM {t}')
            for row in rows:
                cols = list(row.keys())
                # SERIAL id 충돌 방지: activities.id 등은 그대로 넣되 시퀀스 재설정은 생략(append-only)
                placeholders = ', '.join(['%s'] * len(cols))
                collist = ', '.join(cols)
                cur.execute(f'INSERT INTO {t} ({collist}) VALUES ({placeholders})',
                            [row[c] for c in cols])
            print(f"  복구: {t} {len(rows)}행")
    print("\n복구 완료.")


if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    if not args:
        print("사용법: python scripts/restore_backup.py <백업파일.json> [--apply]")
        sys.exit(1)
    if not os.getenv('DATABASE_URL'):
        print('DATABASE_URL 미설정 (.env 확인)')
        sys.exit(1)
    restore(args[0], '--apply' in sys.argv)
