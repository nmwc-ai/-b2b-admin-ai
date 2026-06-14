"""AI 레이어 — Claude Opus 4.8 (Anthropic SDK).

기존 로컬 llama-server(127.0.0.1:8080)를 대체. 프롬프트 본문·few-shot 조립 로직은
기존 ai.py를 그대로 계승하되, 스타일가이드·사례는 Postgres(db)에서 로드한다
(서버리스라 파일시스템/모듈 캐시에 의존하지 않음).
"""

import os
import json
import re

import anthropic

from app import db

MODEL = os.getenv('ANTHROPIC_MODEL', 'claude-opus-4-8')

# ANTHROPIC_API_KEY 환경변수에서 자동 로드 (하드코딩 금지)
_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _text_of(resp) -> str:
    """응답 content 블록들 중 text 블록만 이어붙여 반환 (thinking 블록 제외)."""
    parts = [b.text for b in resp.content if getattr(b, 'type', None) == 'text']
    return ''.join(parts).strip()


def _complete(system: str, user: str, max_tokens: int = 2000, thinking: bool = True) -> str:
    """일반 텍스트 생성. adaptive thinking 기본 on (품질용)."""
    kwargs = {
        'model': MODEL,
        'max_tokens': max_tokens,
        'system': system,
        'messages': [{'role': 'user', 'content': user}],
    }
    if thinking:
        kwargs['thinking'] = {'type': 'adaptive'}
    try:
        resp = _get_client().messages.create(**kwargs)
    except Exception as e:
        raise RuntimeError(f'Claude API 오류: {e}')
    return _text_of(resp)


def _strip_code_block(text: str) -> str:
    return re.sub(r'```(?:json)?', '', text).strip()


def _extract_json_obj(text: str) -> str:
    text = _strip_code_block(text)
    start = text.find('{')
    if start == -1:
        return text
    depth = 0
    for i in range(start, len(text)):
        if text[i] == '{':
            depth += 1
        elif text[i] == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def _build_examples_block(examples: list) -> str:
    if not examples:
        return '(사례 없음)'
    lines = []
    for i, ex in enumerate(examples, 1):
        lines.append(f'[사례 {i}]')
        lines.append(f'문의 유형: {ex.get("inquiry_type", "")}')
        lines.append(f'문의 요약: {ex.get("summary", "")}')
        lines.append('회신:')
        lines.append(ex.get('reply', ''))
        lines.append('')
    return '\n'.join(lines).strip()


# ──────────────────────────────────────────────
# 공개 함수
# ──────────────────────────────────────────────

_PARSE_SCHEMA = {
    'type': 'object',
    'properties': {
        'company':          {'type': 'string'},
        'contact_name':     {'type': 'string'},
        'contact_title':    {'type': 'string'},
        'contact_phone':    {'type': 'string'},
        'email':            {'type': 'string'},
        'inquiry_type':     {'type': 'string', 'enum': ['도입문의', '가격문의', '파트너십', '기술문의', '기타']},
        'service_interest': {'type': 'string'},
        'summary':          {'type': 'string'},
    },
    'required': [
        'company', 'contact_name', 'contact_title', 'contact_phone',
        'email', 'inquiry_type', 'service_interest', 'summary',
    ],
    'additionalProperties': False,
}

_PARSE_SYSTEM = """당신은 ANTIEGG B2B 문의 메일에서 핵심 정보를 추출하는 도우미입니다.
주어진 이메일에서 회사명·담당자·연락처·문의유형·관심서비스·요약을 추출하세요.
없는 정보는 "미상"으로 채우고, summary는 한국어 1~2문장으로 작성하세요.
inquiry_type은 도입문의/가격문의/파트너십/기술문의/기타 중 하나입니다."""


def parse_email(email_body: str) -> dict:
    """수신 이메일에서 구조화 정보 추출 (structured outputs)."""
    fallback = {
        'company': '미상', 'contact_name': '미상', 'contact_title': '미상',
        'contact_phone': '미상', 'email': '미상', 'inquiry_type': '기타',
        'service_interest': '미상', 'summary': email_body[:200],
    }
    try:
        resp = _get_client().messages.create(
            model=MODEL,
            max_tokens=1024,
            system=_PARSE_SYSTEM,
            messages=[{'role': 'user', 'content': f'[이메일]\n{email_body}'}],
            output_config={'format': {'type': 'json_schema', 'schema': _PARSE_SCHEMA}},
        )
        data = json.loads(_extract_json_obj(_text_of(resp)))
        return {**fallback, **{k: v for k, v in data.items() if v}}
    except Exception:
        return fallback


def generate_reply_draft(summary: str, contact_name: str = '', inquiry_type: str = '') -> str:
    """1차 회신 초안 생성. 스타일가이드 system + few-shot user."""
    style_guide = db.get_style_guide()
    examples = db.find_examples(inquiry_type or '기타')
    examples_block = _build_examples_block(examples)
    name_str = f'{contact_name}님' if contact_name and contact_name != '미상' else '담당자님'

    system = f"""[ANTIEGG 운영 스타일 가이드]
{style_guide}

당신은 ANTIEGG의 B2B 회신 초안을 작성합니다. 위 스타일 가이드를 따르세요.
출력은 회신 본문만, 다른 설명·머리말 없이 작성합니다."""

    user = f"""[유사 회신 사례]
{examples_block}

[작성 지침]
- 250~350자 한국어
- 인사 + 감사 → 추가 질문 2~3개 → 마무리 구조
- 위 사례의 말투와 구조를 참고하되, 내용은 아래 문의에 맞게 작성
- 담당자 호칭: "{name_str}"

[실제 문의]
문의 유형: {inquiry_type or '기타'}
문의 요약: {summary}

회신 초안:"""

    return _complete(system, user, max_tokens=2000, thinking=True)


def generate_knock_draft(company: str, contact_name: str, stage: str) -> str:
    """무응답 노크 메일 초안 (v2). 7일 무응답 고객 대상."""
    style_guide = db.get_style_guide()
    name_str = f'{contact_name}님' if contact_name and contact_name != '미상' else '담당자님'

    system = f"""[ANTIEGG 운영 스타일 가이드]
{style_guide}

당신은 ANTIEGG의 노크(후속) 메일 초안을 작성합니다. 출력은 본문만 작성하세요."""

    if stage == 'KNOCK_REPLY':
        user = f"""[작성 지침]
- 1차 회신 후 7일간 응답 없는 고객에게 보내는 노크 메일
- 150~200자 한국어, 부담 없이 확인 요청, 재촉 금지
- 추가 문의 있으면 편히 연락달라는 내용 포함

[실제 정보]
회사명: {company}
담당자: {name_str}

노크 초안:"""
    else:
        user = f"""[작성 지침]
- 견적서 발송 후 7일간 응답 없는 고객에게 보내는 노크 메일
- 150~200자 한국어, 견적 검토 결과 확인·조건 조율 가능 언급, 재촉 금지

[실제 정보]
회사명: {company}
담당자: {name_str}

노크 초안:"""

    return _complete(system, user, max_tokens=1500, thinking=True)


_CLASSIFY_SCHEMA = {
    'type': 'object',
    'properties': {
        'is_b2b_reply': {'type': 'boolean'},
        'inquiry_type': {'type': 'string', 'enum': ['도입문의', '가격문의', '파트너십', '기술문의', '기타']},
        'summary':      {'type': 'string'},
        'contact_name': {'type': 'string'},
    },
    'required': ['is_b2b_reply', 'inquiry_type', 'summary', 'contact_name'],
    'additionalProperties': False,
}


def classify_sent_reply(body: str) -> dict:
    """과거 발신 메일이 B2B 1차 회신인지 판별 + 메타 추출 (v2 보낸메일 학습용)."""
    body = body[:2000]
    system = """당신은 ANTIEGG가 과거에 발송한 회신이 "B2B 신규 문의에 대한 1차 회신"인지 판별합니다.
1차 회신 조건: ①새 잠재 고객/파트너 문의에 답한 회신(운영성/정산/내부공유 X) ②인사→추가질문→마무리 구조.
1차 회신이면 inquiry_type/summary(원래 문의 역추론)/contact_name(수신자)도 채우세요."""
    try:
        resp = _get_client().messages.create(
            model=MODEL,
            max_tokens=1024,
            system=system,
            messages=[{'role': 'user', 'content': f'[이메일]\n{body}'}],
            output_config={'format': {'type': 'json_schema', 'schema': _CLASSIFY_SCHEMA}},
        )
        return json.loads(_extract_json_obj(_text_of(resp)))
    except Exception:
        return {'is_b2b_reply': False, 'inquiry_type': '기타', 'summary': '', 'contact_name': '미상'}
