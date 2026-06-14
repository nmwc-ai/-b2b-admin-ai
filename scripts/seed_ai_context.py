#!/usr/bin/env python3
"""ai_context/ 의 few-shot 사례·스타일 가이드를 DB로 적재. 로컬에서 1회 실행.

    python scripts/seed_ai_context.py

기존 reply_examples.json → examples 테이블, antiegg_style_guide.md → style_guide 테이블.
이미 있는 사례(id 충돌)는 건너뛴다.
"""
import os
import sys
import json
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app import db  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CTX = os.path.join(ROOT, 'ai_context')


def seed_examples():
    path = os.path.join(CTX, 'reply_examples.json')
    if not os.path.exists(path):
        print(f'사례 파일 없음: {path}')
        return 0
    with open(path, encoding='utf-8') as f:
        data = json.load(f)
    added = 0
    for e in data.get('examples', []):
        ex_id = e.get('id') or (
            f'auto_{e["source_uid"]}' if e.get('source_uid') else f'manual_{uuid.uuid4().hex[:12]}'
        )
        db.insert_example({
            'id': ex_id,
            'inquiry_type': e.get('inquiry_type', '기타'),
            'summary': e.get('summary', ''),
            'contact_name': e.get('contact_name', '미상'),
            'reply': e.get('reply', ''),
            'source_uid': e.get('source_uid'),
            'created_at': e.get('created_at') or e.get('source_date'),
        })
        added += 1
    return added


def seed_style_guide():
    path = os.path.join(CTX, 'antiegg_style_guide.md')
    if not os.path.exists(path):
        print(f'스타일 가이드 없음: {path}')
        return False
    with open(path, encoding='utf-8') as f:
        db.set_style_guide(f.read())
    return True


if __name__ == '__main__':
    if not os.getenv('DATABASE_URL'):
        print('DATABASE_URL 미설정 (.env 확인)')
        sys.exit(1)
    n = seed_examples()
    sg = seed_style_guide()
    print(f'사례 {n}건 적재(중복 제외), 스타일 가이드 {"적재" if sg else "건너뜀"}')
    print(f'현재 사례 총 {len(db.list_examples())}건')
