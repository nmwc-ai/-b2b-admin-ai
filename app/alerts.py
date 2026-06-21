"""에러 알림 — cron 실패나 24h 내 에러를 운영자 Gmail로 능동 통지.

자동 cron이 조용히 실패하는 걸 방지. 문제 있을 때만 메일을 보낸다(정상이면 침묵).
AI 키 미설정으로 인한 인증 에러는 '의도된 보류' 상태라 알림에서 제외.
"""

import os
import logging
from datetime import datetime, timedelta

from app import db, email_client, slack

logger = logging.getLogger('alerts')

# AI 키 보류 중 발생하는 예상 에러 — 알림 노이즈 제외
_IGNORE_SUBSTRINGS = ['Could not resolve authentication', 'ANTHROPIC_API_KEY']


def _recipient() -> str:
    return (os.getenv('ALERT_EMAIL') or os.getenv('BACKUP_EMAIL')
            or os.getenv('DIRECTOR_EMAIL') or os.getenv('GMAIL_ADDRESS') or '')


def notify(subject: str, body: str) -> bool:
    """문제 알림을 이메일 + Slack(설정 시) 양쪽으로. 한 채널이라도 성공하면 True."""
    slack.post(f'⚠️ *[ANTIEGG B2B] {subject}*\n{body}')
    to = _recipient()
    if not to:
        logger.error('[alerts] 수신 이메일 미설정')
        return slack.enabled()
    try:
        email_client.send_mail(to, f'[ANTIEGG B2B ⚠️] {subject}', body)
        return True
    except Exception as e:
        logger.error(f'[alerts] 발송 실패: {e}')
        return slack.enabled()


def _recent_errors(hours: int = 24) -> list:
    cutoff = (datetime.now() - timedelta(hours=hours)).isoformat()
    out = []
    for e in db.get_recent_errors(limit=100):
        if (e.get('created_at') or '') < cutoff:
            continue
        msg = e.get('message') or ''
        if any(s in msg for s in _IGNORE_SUBSTRINGS):
            continue
        out.append(e)
    return out


def check_and_alert(cron_results: dict) -> dict:
    """cron 결과 + 최근 에러를 점검해 문제 있으면 알림 1통. 반환: 요약."""
    issues = []
    sync = cron_results.get('sync') or {}
    bk = cron_results.get('backup') or {}
    if sync.get('error'):
        issues.append(f"• Notion 동기화 실패: {sync['error']}")
    if bk.get('error'):
        issues.append(f"• 백업 실패: {bk['error']}")

    errs = _recent_errors(24)
    if errs:
        issues.append(f"• 최근 24시간 에러 {len(errs)}건:")
        for e in errs[:5]:
            issues.append(f"    - {e['created_at'][:19]} {e.get('logger_name','')}: {(e.get('message') or '')[:90]}")

    if not issues:
        return {'alerted': False, 'issues': 0}

    body = ('ANTIEGG B2B 어드민에서 문제가 감지되었습니다.\n'
            f'점검 시각: {datetime.now().isoformat()[:19]}\n\n'
            + '\n'.join(issues)
            + '\n\n어드민: https://antiegg-b2b-cloud.vercel.app/errors')
    sent = notify(f'{len(issues)}건 문제 감지', body)
    return {'alerted': sent, 'issues': len(issues)}
