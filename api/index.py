import os
import sys

# 프로젝트 루트를 import 경로에 추가 (api/ 의 부모) — Vercel 정적 분석/런타임 대응
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.main import app  # noqa: E402 — Vercel은 최상위 'app' 심볼을 요구
