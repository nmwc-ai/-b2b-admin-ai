"""DB-backed 런타임 설정 + .env 폴백.

get(key)는 settings 테이블 → os.getenv → default 순으로 조회.
set_many()는 EDITABLE 화이트리스트 키만 받아 upsert.
"""

import os

from app import db

# (key, label, group)
EDITABLE = [
    ('ANTIEGG_CEO',    '대표자명',       'company'),
    ('ANTIEGG_BIZ_NO', '사업자등록번호', 'company'),
    ('ANTIEGG_PHONE',  '대표 전화',      'company'),
    ('ANTIEGG_EMAIL',  '대표 이메일',    'company'),
    ('ANTIEGG_ADDR',   '주소',           'company'),
    ('DIRECTOR_NAME',  '디렉터명',       'director'),
    ('DIRECTOR_EMAIL', '디렉터 이메일',  'director'),
]
EDITABLE_KEYS = {k for k, *_ in EDITABLE}

GROUP_LABELS = {
    'company':  '회사 정보',
    'director': '디렉터',
}


def get(key: str, default: str = '') -> str:
    val = db.settings_get(key)
    if val:
        return val
    return os.getenv(key, default)


def set_many(items: dict):
    filtered = {k: v for k, v in items.items() if k in EDITABLE_KEYS}
    if filtered:
        db.settings_set_many(filtered)


def get_all_editable() -> list:
    return [
        {'key': k, 'label': label, 'group': group, 'value': get(k)}
        for k, label, group in EDITABLE
    ]


def grouped_editable() -> list:
    out = []
    seen = []
    for item in get_all_editable():
        g = item['group']
        if g not in seen:
            seen.append(g)
            out.append({'group': g, 'label': GROUP_LABELS.get(g, g), 'items': []})
        out[-1]['items'].append(item)
    return out
