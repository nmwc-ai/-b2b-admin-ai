"""보낸메일 학습 — editor 발신 회신을 분류해 few-shot 사례로 적재.

흐름: Gmail 보낸편지함(ANTIEGG 회신) → AI 분류(B2B 1차 회신 여부) → examples 적재.
서버리스/무료 AI 티어를 아끼려고 **증분**으로 돈다:
  - 이미 본 메일은 settings의 since_uid 이상만 새로 가져와 AI 호출을 줄임
  - 그래도 dedup은 source_uid(examples 테이블)로 한 번 더 막음(중복 적재 방지)
키 없거나 자격증명 없으면 graceful 빈 결과.
"""

import logging

from app import db, ai, email_client

logger = logging.getLogger('ingest')

SINCE_UID_KEY = 'ingest_sent_since_uid'


def ingest_sent_examples(limit: int = 20) -> dict:
    """보낸메일에서 B2B 1차 회신을 분류해 사례로 적재. 증분(since_uid) 처리."""
    since = db.settings_get(SINCE_UID_KEY)

    try:
        sent = email_client.fetch_sent_emails(limit=limit, since_uid=since)
    except Exception as e:
        logger.error(f'[ingest] 보낸메일 조회 실패: {e}')
        return {'error': str(e), 'fetched': 0, 'b2b': 0, 'inserted': 0}

    if not sent:
        return {'fetched': 0, 'b2b': 0, 'inserted': 0, 'skipped': 0, 'since_uid': since}

    existing = db.example_source_uids()
    b2b = inserted = skipped = 0
    max_uid = int(since) if since else 0

    for mail in sent:
        uid = mail['uid']
        try:
            max_uid = max(max_uid, int(uid))
        except ValueError:
            pass

        # 안정적 dedup 키: Message-ID 우선, 없으면 UID
        source_uid = (mail.get('message_id') or '').strip() or uid
        if source_uid in existing:
            skipped += 1
            continue

        try:
            cls = ai.classify_sent_reply(mail['body'])
        except Exception as e:
            logger.error(f'[ingest] 분류 실패(uid={uid}): {e}')
            continue

        if not cls.get('is_b2b_reply'):
            continue
        b2b += 1

        try:
            db.insert_example({
                'inquiry_type': cls.get('inquiry_type') or '기타',
                'summary':      cls.get('summary') or '',
                'contact_name': cls.get('contact_name') or '미상',
                'reply':        mail['body'],
                'source_uid':   source_uid,
            })
            existing.add(source_uid)
            inserted += 1
        except Exception as e:
            logger.error(f'[ingest] 사례 적재 실패(uid={uid}): {e}')

    # 다음 실행은 이번에 본 가장 큰 UID 이후만 — 비B2B 메일 재분류 방지
    if max_uid and str(max_uid) != (since or ''):
        try:
            db.settings_set_many({SINCE_UID_KEY: str(max_uid)})
        except Exception as e:
            logger.error(f'[ingest] since_uid 저장 실패: {e}')

    return {'fetched': len(sent), 'b2b': b2b, 'inserted': inserted,
            'skipped': skipped, 'since_uid': str(max_uid) if max_uid else since}
