"""Slack 알림 — 신규 문의·문제를 통지. 기존 봇(에그몽) 재사용 지원.

두 가지 전송 방식 (우선순위 순):
  1) Bot Token  : SLACK_BOT_TOKEN(xoxb-) + SLACK_CHANNEL — 기존 봇의 토큰으로
                  chat.postMessage 호출. 앱 추가 한도 영향 없음(봇을 채널에 초대해야 함).
  2) Incoming Webhook : SLACK_WEBHOOK — 기존 웹훅 URL 재사용.
  둘 다 없으면 조용히 비활성(graceful no-op). 절대 raise 하지 않는다.
추가 의존성 없이 urllib만 사용.
"""

import os
import json
import logging
import urllib.request

logger = logging.getLogger('slack')


def _bot_token() -> str:
    return (os.getenv('SLACK_BOT_TOKEN') or '').strip()


def _channel() -> str:
    return (os.getenv('SLACK_CHANNEL') or '').strip()


def _webhook() -> str:
    return (os.getenv('SLACK_WEBHOOK') or '').strip()


def enabled() -> bool:
    return bool((_bot_token() and _channel()) or _webhook())


def _post_bot(text: str) -> bool:
    body = json.dumps({'channel': _channel(), 'text': text}).encode('utf-8')
    req = urllib.request.Request(
        'https://slack.com/api/chat.postMessage', data=body, method='POST',
        headers={'Content-Type': 'application/json; charset=utf-8',
                 'Authorization': f'Bearer {_bot_token()}'})
    with urllib.request.urlopen(req, timeout=10) as r:
        data = json.load(r)
    if not data.get('ok'):
        logger.error(f"[slack] chat.postMessage 실패: {data.get('error')}")
    return bool(data.get('ok'))


def _post_webhook(text: str) -> bool:
    body = json.dumps({'text': text}).encode('utf-8')
    req = urllib.request.Request(
        _webhook(), data=body, method='POST',
        headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return 200 <= r.status < 300


def post(text: str) -> bool:
    """Slack에 메시지 전송. 미설정/실패 시 False(예외 삼킴)."""
    try:
        if _bot_token() and _channel():
            return _post_bot(text)
        if _webhook():
            return _post_webhook(text)
        return False
    except Exception as e:
        logger.error(f'[slack] 발송 실패: {e}')
        return False
