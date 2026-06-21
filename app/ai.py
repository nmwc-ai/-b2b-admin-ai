"""AI 레이어 — 무료(Google Gemini) 우선, Claude 폴백.

provider 선택:
  - GEMINI_API_KEY 있으면 → Gemini (무료 티어, urllib REST, 의존성 없음)
  - 없고 ANTHROPIC_API_KEY 있으면 → Claude (anthropic SDK)
  - 둘 다 없으면 → RuntimeError (호출부가 try/except로 graceful 처리, 초안 빈칸)

스타일가이드·few-shot 사례는 Postgres(db)에서 로드. 함수 시그니처는 provider 무관하게 동일.
"""

import os
import json
import re
import urllib.request

from app import db

GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-2.5-flash')
ANTHROPIC_MODEL = os.getenv('ANTHROPIC_MODEL', 'claude-opus-4-8')


def _provider() -> str:
    if os.getenv('GEMINI_API_KEY'):
        return 'gemini'
    if os.getenv('ANTHROPIC_API_KEY'):
        return 'anthropic'
    return ''


# ── Gemini (무료) ─────────────────────────────────────────────────────────────
def _gemini(system: str, user: str, max_tokens: int, json_mode: bool) -> str:
    key = os.environ['GEMINI_API_KEY']
    url = (f'https://generativelanguage.googleapis.com/v1beta/models/'
           f'{GEMINI_MODEL}:generateContent?key={key}')
    body = {
        'contents': [{'parts': [{'text': user}]}],
        'generationConfig': {
            'maxOutputTokens': max_tokens,
            'temperature': 0.7,
            # 2.5 계열은 thinking 모델 — thinking 비활성화해 토큰을 본문에 쓰고 빈 응답 방지
            'thinkingConfig': {'thinkingBudget': 0},
        },
    }
    if system:
        body['systemInstruction'] = {'parts': [{'text': system}]}
    if json_mode:
        body['generationConfig']['responseMimeType'] = 'application/json'
    req = urllib.request.Request(url, data=json.dumps(body).encode('utf-8'),
                                 headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    cand = (data.get('candidates') or [{}])[0]
    parts = (cand.get('content') or {}).get('parts') or []
    text = ''.join(p.get('text', '') for p in parts).strip()
    if not text:
        raise RuntimeError(f"빈 응답 (finishReason={cand.get('finishReason', '?')})")
    return text


# ── Claude (폴백) ─────────────────────────────────────────────────────────────
_client = None


def _claude(system: str, user: str, max_tokens: int, json_mode: bool) -> str:
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic()
    kwargs = {'model': ANTHROPIC_MODEL, 'max_tokens': max_tokens,
              'system': system, 'messages': [{'role': 'user', 'content': user}]}
    resp = _client.messages.create(**kwargs)
    return ''.join(b.text for b in resp.content if getattr(b, 'type', None) == 'text').strip()


def _complete(system: str, user: str, max_tokens: int = 2000, json_mode: bool = False) -> str:
    p = _provider()
    try:
        if p == 'gemini':
            return _gemini(system, user, max_tokens, json_mode)
        if p == 'anthropic':
            return _claude(system, user, max_tokens, json_mode)
    except Exception as e:
        raise RuntimeError(f'AI({p}) 오류: {e}')
    raise RuntimeError('AI 제공자 미설정 (GEMINI_API_KEY 또는 ANTHROPIC_API_KEY)')


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


# ── 공개 함수 ──────────────────────────────────────────────────────────────────
_PARSE_SYSTEM = """당신은 ANTIEGG B2B 문의 메일에서 핵심 정보를 추출하는 도우미입니다.
없는 정보는 "미상"으로 채우고, summary는 한국어 1~2문장으로 작성하세요.
inquiry_type은 도입문의/가격문의/파트너십/기술문의/기타 중 하나입니다.
반드시 아래 키만 가진 JSON 하나로만 응답: company, contact_name, contact_title, contact_phone, email, inquiry_type, service_interest, summary"""


def parse_email(email_body: str) -> dict:
    fallback = {
        'company': '미상', 'contact_name': '미상', 'contact_title': '미상',
        'contact_phone': '미상', 'email': '미상', 'inquiry_type': '기타',
        'service_interest': '미상', 'summary': email_body[:200],
    }
    try:
        raw = _complete(_PARSE_SYSTEM, f'[이메일]\n{email_body}', max_tokens=1024, json_mode=True)
        data = json.loads(_extract_json_obj(raw))
        return {**fallback, **{k: v for k, v in data.items() if v}}
    except Exception:
        return fallback


def generate_reply_draft(summary: str, contact_name: str = '', inquiry_type: str = '') -> str:
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

    return _complete(system, user, max_tokens=2000)


def generate_knock_draft(company: str, contact_name: str, stage: str) -> str:
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
    return _complete(system, user, max_tokens=1500)


def classify_sent_reply(body: str) -> dict:
    body = body[:2000]
    system = """ANTIEGG가 과거 발송한 회신이 "B2B 신규 문의 1차 회신"인지 판별합니다.
조건: ①새 잠재고객/파트너 문의 답신(운영성/정산/내부공유 X) ②인사→추가질문→마무리 구조.
JSON 하나로만 응답: is_b2b_reply(boolean), inquiry_type(도입문의/가격문의/파트너십/기술문의/기타), summary, contact_name"""
    try:
        raw = _complete(system, f'[이메일]\n{body}', max_tokens=1024, json_mode=True)
        return json.loads(_extract_json_obj(raw))
    except Exception:
        return {'is_b2b_reply': False, 'inquiry_type': '기타', 'summary': '', 'contact_name': '미상'}
