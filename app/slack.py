"""Slack 알림 — Incoming Webhook 1개로 신규 문의·문제를 통지.

SLACK_WEBHOOK 미설정이면 조용히 비활성(graceful). 절대 raise 하지 않는다
(알림 실패가 본 작업을 막으면 안 됨). 추가 의존성 없이 urllib만 사용.
"""

import os
import json
import logging
import urllib.request

logger = logging.getLogger('slack')


def _webhook() -> str:
    return (os.getenv('SLACK_WEBHOOK') or '').strip()


def enabled() -> bool:
    return bool(_webhook())


def post(text: str) -> bool:
    """Slack 채널에 메시지 전송. 미설정/실패 시 False(예외 삼킴)."""
    url = _webhook()
    if not url:
        return False
    try:
        data = json.dumps({'text': text}).encode('utf-8')
        req = urllib.request.Request(
            url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except Exception as e:
        logger.error(f'[slack] 발송 실패: {e}')
        return False
