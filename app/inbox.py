"""인박스 수신 처리 — Gmail UNSEEN → Claude 파싱+회신생성 → 딜 생성.

기존 scheduler.poll_inbox를 동기 함수로 옮김. 호출 경로:
  - GET /cron/daily (Vercel Cron, 일1회)
  - POST /admin/poll-inbox ("지금 확인" 버튼)
"""

import logging

from app import db, ai, email_client

logger = logging.getLogger('inbox')


def poll_inbox() -> dict:
    """새 B2B 메일을 받아 파싱·회신초안 생성 후 딜로 적재."""
    created = 0
    errors = 0
    try:
        emails = email_client.fetch_new_emails()
    except Exception as e:
        logger.error(f'[poll_inbox] IMAP 수신 실패: {e}')
        return {'fetched': 0, 'created': 0, 'errors': 1, 'error': str(e)}

    for em in emails:
        try:
            parsed = ai.parse_email(em['body'])
            # 보낸 사람 이메일이 파싱값보다 신뢰도 높음 — 미상이면 헤더값 사용
            if parsed.get('email') in (None, '', '미상') and em.get('sender_email'):
                parsed['email'] = em['sender_email']

            reply = ''
            try:
                reply = ai.generate_reply_draft(
                    parsed.get('summary', ''),
                    parsed.get('contact_name', ''),
                    parsed.get('inquiry_type', ''),
                )
            except Exception as e:
                logger.error(f'[poll_inbox] 회신 생성 실패: {e}')

            deal_id = db.insert_deal({
                'company':          parsed.get('company'),
                'contact_name':     parsed.get('contact_name'),
                'contact_title':    parsed.get('contact_title'),
                'contact_phone':    parsed.get('contact_phone'),
                'email':            parsed.get('email'),
                'inquiry_type':     parsed.get('inquiry_type'),
                'service_interest': parsed.get('service_interest'),
                'summary':          parsed.get('summary'),
                'reply_draft':      reply,
            })
            db.log_activity(deal_id, 'inbound_received', {
                'subject': em.get('subject', ''),
                'from':    em.get('sender', ''),
            })
            created += 1
        except Exception as e:
            errors += 1
            logger.error(f'[poll_inbox] 딜 생성 실패: {e}')

    return {'fetched': len(emails), 'created': created, 'errors': errors}
