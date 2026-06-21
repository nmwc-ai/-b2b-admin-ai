"""노크(무응답 후속) 자동화.

- REPLIED/QUOTED 단계에서 7일 무응답 → KNOCK_REPLY/KNOCK_QUOTE로 전환 + AI 노크 초안 생성
  (초안은 사람이 검토 후 Gmail로 발송 — 판단은 사람)
- KNOCK_* 에서 추가 7일 무응답 → CLOSED_LOST 자동 종료

cron(/cron/daily) 또는 세팅의 '노크 점검' 버튼으로 실행.
"""

import logging

from app import db, ai

logger = logging.getLogger('knock')

_NEXT_STAGE = {'REPLIED': 'KNOCK_REPLY', 'QUOTED': 'KNOCK_QUOTE'}


def run_knock_checks() -> dict:
    knocked, closed = 0, 0

    # ① 무응답 7일 → 노크 단계 전환 + AI 노크 초안
    for d in db.get_deals_for_knock_check():
        old = d.get('stage')
        new_stage = _NEXT_STAGE.get(old, 'KNOCK_REPLY')
        draft = ''
        try:
            draft = ai.generate_knock_draft(d.get('company') or '', d.get('contact_name') or '', new_stage)
        except Exception as e:
            logger.error(f"[knock] 노크 초안 생성 실패({d['deal_id']}): {e}")
        try:
            db.update_deal(d['deal_id'], {'stage': new_stage, 'knock_draft': draft})
            db.log_activity(d['deal_id'], 'stage_changed', {'from': old, 'to': new_stage})
            db.log_activity(d['deal_id'], 'note_added', {'note': '무응답 7일 — 노크 단계 자동 전환'})
            knocked += 1
        except Exception as e:
            logger.error(f"[knock] 노크 전환 실패({d['deal_id']}): {e}")

    # ② 노크 후 추가 7일 무응답 → 자동 종료
    for d in db.get_deals_for_closed_lost():
        try:
            db.update_deal(d['deal_id'], {'stage': 'CLOSED_LOST'})
            db.log_activity(d['deal_id'], 'stage_changed', {'from': d.get('stage'), 'to': 'CLOSED_LOST'})
            db.log_activity(d['deal_id'], 'note_added', {'note': '노크 후 7일 무응답 — 자동 종료'})
            closed += 1
        except Exception as e:
            logger.error(f"[knock] 자동 종료 실패({d['deal_id']}): {e}")

    return {'knocked': knocked, 'closed_lost': closed}
