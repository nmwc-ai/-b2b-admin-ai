"""logger.error/exception를 errors 테이블에 적재하는 logging 핸들러.

emit는 절대 raise하지 않는다(로깅 루프 방지). main.py 임포트 시 1회 설치.
"""

import logging
import traceback

from app import db


class DBErrorHandler(logging.Handler):
    def emit(self, record):
        try:
            tb = None
            if record.exc_info:
                tb = ''.join(traceback.format_exception(*record.exc_info))
            db.insert_error(
                level=record.levelname,
                logger_name=record.name,
                message=record.getMessage(),
                tb=tb,
            )
        except Exception:
            pass


_installed = False


def install_db_handler():
    global _installed
    if _installed:
        return
    handler = DBErrorHandler()
    handler.setLevel(logging.ERROR)
    logging.getLogger().addHandler(handler)
    _installed = True
