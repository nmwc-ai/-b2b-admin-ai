"""few-shot 사례 CRUD — Postgres(examples 테이블) 위의 얇은 래퍼.

기존 reply_examples.json 파일 조작을 DB로 대체. 서버리스에서 파일은 휘발성이라
사례는 반드시 DB에 저장한다.
"""

from app import db

INQUIRY_TYPES = ['도입문의', '가격문의', '파트너십', '기술문의', '기타']


def list_all() -> list:
    return db.list_examples()


def get_by_id(ex_id: str) -> dict:
    return db.get_example(ex_id)


def add_manual(inquiry_type: str, summary: str, contact_name: str, reply: str) -> dict:
    ex = {
        'inquiry_type': (inquiry_type or '').strip() or '기타',
        'summary':      (summary or '').strip(),
        'contact_name': (contact_name or '').strip() or '미상',
        'reply':        (reply or '').strip(),
    }
    ex['id'] = db.insert_example(ex)
    return ex


def update_reply(ex_id: str, reply: str) -> bool:
    return db.update_example_reply(ex_id, reply)


def delete_by_id(ex_id: str) -> bool:
    return db.delete_example(ex_id)
