"""Supabase Postgres 데이터 레이어.

서버리스 환경: 연결은 요청당 open/close (get_conn 컨텍스트 매니저).
DATABASE_URL은 반드시 Supabase transaction pooler(:6543, pgbouncer)를 사용한다.

init_db()는 런타임에서 호출하지 않는다 — scripts/init_db.py로 1회만 실행한다
(콜드스타트마다 DDL이 도는 것을 방지).
"""

import os
import json
import uuid
from datetime import datetime, timedelta
from contextlib import contextmanager

import psycopg2
import psycopg2.extras


@contextmanager
def get_conn():
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _cur(conn):
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


# ──────────────────────────────────────────────
# 스키마 (scripts/init_db.py 에서만 호출)
# ──────────────────────────────────────────────

def init_db():
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS deals (
            deal_id             TEXT PRIMARY KEY,
            company             TEXT,
            contact_name        TEXT,
            contact_title       TEXT,
            contact_phone       TEXT,
            email               TEXT,
            inquiry_type        TEXT,
            service_interest    TEXT,
            stage               TEXT DEFAULT 'REVIEWING',
            summary             TEXT,
            reply_draft         TEXT,
            knock_draft         TEXT,
            cond_service_name   TEXT,
            cond_service_desc   TEXT,
            cond_unit_price     TEXT,
            cond_quantity       TEXT,
            cond_payment_terms  TEXT,
            cond_delivery_scope TEXT,
            cond_notes          TEXT,
            quote_path_v1       TEXT,
            quote_path_v2       TEXT,
            quote_path_v3       TEXT,
            contract_path_v1    TEXT,
            contract_path_v2    TEXT,
            contract_path_v3    TEXT,
            modusign_doc_id     TEXT,
            cond_company_addr   TEXT,
            cond_company_ceo    TEXT,
            cond_company_biz_no TEXT,
            cond_contract_start TEXT,
            cond_contract_end   TEXT,
            sign_token          TEXT,
            signed_at           TEXT,
            signed_ip           TEXT,
            trigger_reply_send      TEXT DEFAULT 'IDLE',
            trigger_quote_gen       TEXT DEFAULT 'IDLE',
            trigger_contract_gen    TEXT DEFAULT 'IDLE',
            trigger_contract_send   TEXT DEFAULT 'IDLE',
            trigger_knock_send      TEXT DEFAULT 'IDLE',
            notion_page_id      TEXT,
            created_at          TEXT,
            updated_at          TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS counter (
            year_month   TEXT PRIMARY KEY,
            last_number  INTEGER DEFAULT 0
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS activities (
            id           SERIAL PRIMARY KEY,
            deal_id      TEXT NOT NULL,
            type         TEXT NOT NULL,
            payload      TEXT,
            created_at   TEXT NOT NULL
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_activities_deal_id ON activities(deal_id)")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_activities_created_at ON activities(created_at DESC)")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key         TEXT PRIMARY KEY,
            value       TEXT,
            updated_at  TEXT
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS errors (
            id           SERIAL PRIMARY KEY,
            level        TEXT NOT NULL,
            logger_name  TEXT,
            message      TEXT,
            traceback    TEXT,
            created_at   TEXT NOT NULL
        )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_errors_created_at ON errors(created_at DESC)")
        # ── few-shot 사례 (기존 reply_examples.json 대체) ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS examples (
            id            TEXT PRIMARY KEY,
            inquiry_type  TEXT,
            summary       TEXT,
            contact_name  TEXT,
            reply         TEXT,
            source_uid    TEXT,
            created_at    TEXT,
            updated_at    TEXT
        )
        """)
        # ── 스타일 가이드 (기존 antiegg_style_guide.md 대체, 단일 행) ──
        cur.execute("""
        CREATE TABLE IF NOT EXISTS style_guide (
            id          INTEGER PRIMARY KEY DEFAULT 1,
            content     TEXT,
            updated_at  TEXT
        )
        """)


# ──────────────────────────────────────────────
# 딜 (deals)
# ──────────────────────────────────────────────

def generate_deal_id() -> str:
    ym = datetime.now().strftime('%Y%m')
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute(
            'SELECT last_number FROM counter WHERE year_month = %s FOR UPDATE', (ym,)
        )
        row = cur.fetchone()
        if row:
            n = row['last_number'] + 1
            cur.execute(
                'UPDATE counter SET last_number = %s WHERE year_month = %s', (n, ym)
            )
        else:
            n = 1
            cur.execute(
                'INSERT INTO counter (year_month, last_number) VALUES (%s, %s)', (ym, n)
            )
    return f'AE-{ym}-{n:03d}'


def insert_deal(deal: dict) -> str:
    deal_id = generate_deal_id()
    now = datetime.now().isoformat()
    payload = {
        'company': None, 'contact_name': None, 'contact_title': None,
        'contact_phone': None, 'email': None, 'inquiry_type': None,
        'service_interest': None, 'summary': None, 'reply_draft': None,
        **deal,
        'deal_id': deal_id, 'created_at': now, 'updated_at': now,
    }
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
        INSERT INTO deals (
            deal_id, company, contact_name, contact_title, contact_phone, email,
            inquiry_type, service_interest,
            summary, reply_draft, created_at, updated_at
        ) VALUES (
            %(deal_id)s, %(company)s, %(contact_name)s, %(contact_title)s, %(contact_phone)s, %(email)s,
            %(inquiry_type)s, %(service_interest)s,
            %(summary)s, %(reply_draft)s, %(created_at)s, %(updated_at)s
        )
        """, payload)
    return deal_id


def get_all_deals() -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT * FROM deals ORDER BY created_at DESC')
        return [dict(r) for r in cur.fetchall()]


def get_deal(deal_id: str) -> dict:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT * FROM deals WHERE deal_id = %s', (deal_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def update_deal(deal_id: str, fields: dict):
    fields = {**fields, 'updated_at': datetime.now().isoformat()}
    set_clause = ', '.join(f'{k} = %({k})s' for k in fields)
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute(
            f'UPDATE deals SET {set_clause} WHERE deal_id = %(deal_id)s',
            {**fields, 'deal_id': deal_id}
        )


def delete_deal(deal_id: str) -> bool:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('DELETE FROM deals WHERE deal_id = %s', (deal_id,))
        rowcount = cur.rowcount
        cur.execute('DELETE FROM activities WHERE deal_id = %s', (deal_id,))
        return rowcount > 0


def get_deals_by_trigger(trigger_col: str, status: str = 'PENDING') -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute(f'SELECT * FROM deals WHERE {trigger_col} = %s', (status,))
        return [dict(r) for r in cur.fetchall()]


def get_stage_counts() -> dict:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT stage, COUNT(*) as cnt FROM deals GROUP BY stage')
        return {r['stage']: r['cnt'] for r in cur.fetchall()}


def set_sign_token(deal_id: str) -> str:
    token = str(uuid.uuid4())
    update_deal(deal_id, {'sign_token': token})
    return token


def get_deal_by_sign_token(token: str) -> dict:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT * FROM deals WHERE sign_token = %s', (token,))
        row = cur.fetchone()
    return dict(row) if row else None


# ──────────────────────────────────────────────
# 인박스 / 파이프라인 / 회사 뷰
# ──────────────────────────────────────────────

def get_inbox_now() -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            SELECT * FROM deals
            WHERE (stage = 'REVIEWING' AND (reply_draft IS NULL OR reply_draft = ''))
               OR stage IN ('KNOCK_REPLY', 'KNOCK_QUOTE')
               OR trigger_reply_send    IN ('DRAFT', 'ERROR')
               OR trigger_quote_gen     = 'ERROR'
               OR trigger_contract_gen  = 'ERROR'
               OR trigger_contract_send IN ('DRAFT', 'ERROR')
               OR trigger_knock_send    IN ('DRAFT', 'ERROR')
            ORDER BY updated_at DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def get_inbox_upcoming() -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            SELECT * FROM deals
            WHERE stage IN ('REPLIED', 'QUOTED')
            AND trigger_knock_send = 'IDLE'
            AND NOW() - updated_at::timestamp >= INTERVAL '6 days'
            AND NOW() - updated_at::timestamp < INTERVAL '7 days'
            ORDER BY updated_at ASC
        """)
        return [dict(r) for r in cur.fetchall()]


def get_deals_for_knock_check() -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            SELECT * FROM deals
            WHERE stage IN ('REPLIED', 'QUOTED')
            AND trigger_knock_send = 'IDLE'
            AND NOW() - updated_at::timestamp >= INTERVAL '7 days'
        """)
        return [dict(r) for r in cur.fetchall()]


def get_deals_for_closed_lost() -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            SELECT * FROM deals
            WHERE stage IN ('KNOCK_REPLY', 'KNOCK_QUOTE')
            AND NOW() - updated_at::timestamp >= INTERVAL '7 days'
        """)
        return [dict(r) for r in cur.fetchall()]


def get_companies_summary() -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            SELECT
                COALESCE(NULLIF(company, ''), '(회사명 미상)') AS company,
                COUNT(*) AS total,
                SUM(CASE WHEN stage NOT IN ('CLOSED_WON','CLOSED_LOST') THEN 1 ELSE 0 END) AS active,
                MAX(updated_at) AS last_activity
            FROM deals
            GROUP BY COALESCE(NULLIF(company, ''), '(회사명 미상)')
            ORDER BY last_activity DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def get_deals_by_company(company: str, exclude_deal_id: str = None) -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        if exclude_deal_id:
            cur.execute("""
                SELECT * FROM deals
                WHERE COALESCE(NULLIF(company, ''), '(회사명 미상)') = %s
                  AND deal_id != %s
                ORDER BY created_at DESC
            """, (company, exclude_deal_id))
        else:
            cur.execute("""
                SELECT * FROM deals
                WHERE COALESCE(NULLIF(company, ''), '(회사명 미상)') = %s
                ORDER BY created_at DESC
            """, (company,))
        return [dict(r) for r in cur.fetchall()]


def search_deals(query: str, limit: int = 20) -> dict:
    q = f'%{query}%'
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT * FROM deals WHERE deal_id ILIKE %s LIMIT 1', (query,))
        exact = cur.fetchone()
        cur.execute("""
            SELECT * FROM deals
            WHERE deal_id ILIKE %s
               OR company ILIKE %s
               OR contact_name ILIKE %s
               OR email ILIKE %s
               OR summary ILIKE %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (q, q, q, q, q, limit))
        deal_rows = cur.fetchall()
    return {
        'exact_deal': dict(exact) if exact else None,
        'deals': [dict(r) for r in deal_rows],
    }


# ──────────────────────────────────────────────
# 활동 로그 (activities)
# ──────────────────────────────────────────────

def log_activity(deal_id: str, type_: str, payload: dict = None):
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            INSERT INTO activities (deal_id, type, payload, created_at)
            VALUES (%s, %s, %s, %s)
        """, (
            deal_id,
            type_,
            json.dumps(payload, ensure_ascii=False) if payload else None,
            datetime.now().isoformat(),
        ))


def get_activities(deal_id: str, limit: int = 50) -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            SELECT * FROM activities
            WHERE deal_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (deal_id, limit))
        out = []
        for r in cur.fetchall():
            d = dict(r)
            if d.get('payload'):
                try:
                    d['payload'] = json.loads(d['payload'])
                except Exception:
                    pass
            out.append(d)
    return out


# ──────────────────────────────────────────────
# few-shot 사례 (examples)
# ──────────────────────────────────────────────

def list_examples() -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT * FROM examples ORDER BY created_at DESC')
        return [dict(r) for r in cur.fetchall()]


def find_examples(inquiry_type: str, n: int = 2) -> list:
    """inquiry_type 일치 사례 우선, 부족하면 다른 유형으로 채움."""
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute(
            'SELECT * FROM examples WHERE inquiry_type = %s ORDER BY created_at DESC LIMIT %s',
            (inquiry_type, n),
        )
        matched = [dict(r) for r in cur.fetchall()]
        if len(matched) < n:
            cur.execute(
                'SELECT * FROM examples WHERE inquiry_type IS DISTINCT FROM %s '
                'ORDER BY created_at DESC LIMIT %s',
                (inquiry_type, n - len(matched)),
            )
            matched += [dict(r) for r in cur.fetchall()]
    return matched[:n]


def get_example(ex_id: str) -> dict:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT * FROM examples WHERE id = %s', (ex_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def insert_example(ex: dict) -> str:
    now = datetime.now().isoformat()
    ex_id = ex.get('id') or (
        f'auto_{ex["source_uid"]}' if ex.get('source_uid') else f'manual_{uuid.uuid4().hex[:12]}'
    )
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            INSERT INTO examples (id, inquiry_type, summary, contact_name, reply, source_uid, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
        """, (
            ex_id,
            ex.get('inquiry_type', '기타'),
            ex.get('summary', ''),
            ex.get('contact_name', '미상'),
            ex.get('reply', ''),
            ex.get('source_uid'),
            ex.get('created_at', now),
            now,
        ))
    return ex_id


def update_example_reply(ex_id: str, reply: str) -> bool:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute(
            'UPDATE examples SET reply = %s, updated_at = %s WHERE id = %s',
            (reply.strip(), datetime.now().isoformat(), ex_id),
        )
        return cur.rowcount > 0


def delete_example(ex_id: str) -> bool:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('DELETE FROM examples WHERE id = %s', (ex_id,))
        return cur.rowcount > 0


def example_source_uids() -> set:
    """이미 적재된 source_uid 집합 (보낸메일 증분 수집 dedup용)."""
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT source_uid FROM examples WHERE source_uid IS NOT NULL')
        return {r['source_uid'] for r in cur.fetchall()}


# ──────────────────────────────────────────────
# 스타일 가이드 (style_guide)
# ──────────────────────────────────────────────

def get_style_guide() -> str:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT content FROM style_guide WHERE id = 1')
        row = cur.fetchone()
    return row['content'] if row and row['content'] else ''


def set_style_guide(content: str):
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute("""
            INSERT INTO style_guide (id, content, updated_at)
            VALUES (1, %s, %s)
            ON CONFLICT (id) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at
        """, (content, datetime.now().isoformat()))


# ──────────────────────────────────────────────
# 설정 (settings)
# ──────────────────────────────────────────────

def settings_get(key: str) -> str:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT value FROM settings WHERE key = %s', (key,))
        row = cur.fetchone()
    return row['value'] if row and row['value'] else None


def settings_set_many(items: dict):
    now = datetime.now().isoformat()
    with get_conn() as conn:
        cur = _cur(conn)
        for k, v in items.items():
            cur.execute("""
                INSERT INTO settings (key, value, updated_at)
                VALUES (%s, %s, %s)
                ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """, (k, (v or '').strip(), now))


# ──────────────────────────────────────────────
# 에러 로그 (errors)
# ──────────────────────────────────────────────

def insert_error(level: str, logger_name: str, message: str, tb: str = None):
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute(
            'INSERT INTO errors (level, logger_name, message, traceback, created_at) '
            'VALUES (%s, %s, %s, %s, %s)',
            (level, logger_name, message, tb, datetime.now().isoformat()),
        )


def get_recent_errors(limit: int = 100) -> list:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT * FROM errors ORDER BY created_at DESC LIMIT %s', (limit,))
        return [dict(r) for r in cur.fetchall()]


def count_errors_since(iso_ts: str) -> int:
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('SELECT COUNT(*) AS cnt FROM errors WHERE created_at >= %s', (iso_ts,))
        row = cur.fetchone()
        return row['cnt'] if row else 0


def purge_old_errors(days: int = 30) -> int:
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        cur = _cur(conn)
        cur.execute('DELETE FROM errors WHERE created_at < %s', (cutoff,))
        return cur.rowcount
