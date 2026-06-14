#!/usr/bin/env python3
"""Supabase Postgres 스키마 1회 생성. 로컬에서 실행.

    python scripts/init_db.py

.env의 DATABASE_URL(Supabase pooler)을 사용한다.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

load_dotenv()

from app import db  # noqa: E402

if __name__ == '__main__':
    if not os.getenv('DATABASE_URL'):
        print('DATABASE_URL 미설정 (.env 확인)')
        sys.exit(1)
    db.init_db()
    print('스키마 생성 완료 (deals/counter/activities/settings/errors/examples/style_guide)')
