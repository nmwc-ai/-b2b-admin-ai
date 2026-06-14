# ANTIEGG B2B 어드민 — 클라우드 (antiegg-b2b-cloud)

B2B 문의 운영을 1인 디렉터가 처리하도록 돕는 어드민. **판단은 사람, 실행은 AI.**
메일 발송은 항상 사람이 Gmail 임시보관함에서 확인하고 누른다 — 어드민은 초안만 만든다.

기존 로컬 버전(`antiegg-b2b`, llama-server + APScheduler + Tailscale)을 **Vercel 서버리스 +
Supabase Postgres + Claude API**로 처음부터 재구축한 것.

## 스택

- **Vercel** (Hobby) — FastAPI 서버리스 (`api/index.py` → `app.main:app`)
- **Supabase Postgres** — 단일 DB. 서버리스는 **transaction pooler(:6543)** 연결 사용
- **Claude Opus 4.8** (`anthropic` SDK) — 문의 파싱 + 회신 초안 생성
- **Gmail IMAP** — 수신 + 임시보관함 초안 저장 (발송 안 함)
- FastAPI + Jinja2 + HTMX — 어드민 UI
- **Vercel Cron** — 매일 1회 인박스 폴링 (`/cron/daily`)

## 구조

```
api/index.py          Vercel 진입점
app/
  main.py             라우트 + Basic Auth 미들웨어
  db.py               Postgres 데이터 레이어 (init_db는 런타임 미호출)
  ai.py               Claude (parse_email, generate_reply_draft)
  email_client.py     Gmail IMAP (fetch_new_emails, create_draft)
  inbox.py            poll_inbox (수신→파싱→회신생성→딜)
  examples.py         few-shot 사례 CRUD (Postgres)
  settings.py         회사/디렉터 설정 (DB→env 폴백)
  error_log.py        logger.error → errors 테이블
  templates/          Jinja2 화면
scripts/
  init_db.py          스키마 1회 생성
  seed_ai_context.py  사례+스타일가이드 DB 적재
  notion_import.py    노션 243건 적재 (--apply / --backfill)
ai_context/           시드용 reply_examples.json, antiegg_style_guide.md
vercel.json           빌드 + 라우트 + cron
```

## 로컬 개발

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # 값 채우기 (아래)
.venv/bin/python scripts/init_db.py          # 스키마 생성 (1회)
.venv/bin/python scripts/seed_ai_context.py  # 사례/스타일가이드 적재 (1회)
.venv/bin/python scripts/notion_import.py            # 진단(읽기전용)
.venv/bin/python scripts/notion_import.py --apply    # 노션 243건 적재 (기존 deals wipe!)
.venv/bin/uvicorn app.main:app --reload      # http://localhost:8000
```

## 환경변수 (.env / Vercel 프로젝트 env)

| 키 | 용도 |
|---|---|
| `DATABASE_URL` | **Supabase transaction pooler(:6543)** 연결문자열. 서버리스 필수 |
| `ANTHROPIC_API_KEY` | Claude API 키 (console.anthropic.com) |
| `ANTHROPIC_MODEL` | 기본 `claude-opus-4-8` |
| `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` | Gmail 앱 비밀번호 |
| `B2B_LABEL` | 수신 라벨 (기본 `B2B_INQUIRY`) |
| `ADMIN_USER` / `ADMIN_PASSWORD` | 어드민 Basic Auth. 미설정 시 인증 비활성 |
| `CRON_SECRET` | Vercel Cron 보호. `/cron/daily`는 `Authorization: Bearer <CRON_SECRET>` 검증 |
| `APP_BASE_URL` | 공개 주소 |
| `ANTIEGG_*` / `DIRECTOR_*` | 회사/디렉터 정보 (settings 폴백) |
| `NOTION_TOKEN` / `NOTION_DB_ID` | 노션 임포트용 (scripts 전용) |

> Supabase 연결: 서버리스는 직접연결(:5432, 현재 IPv6 전용)이 아닌 **pooler(:6543)** 를 써야 한다.
> Supabase 대시보드 → Project Settings → Database → Connection string → **Transaction pooler**.

## 배포 (Vercel)

```bash
npm i -g vercel          # 최초 1회
vercel login
vercel link              # 새 프로젝트 생성/연결
# Vercel 대시보드 또는 `vercel env add` 로 위 환경변수 설정
vercel --prod
```

`vercel.json`의 cron이 매일 00:00(UTC)에 `/cron/daily`를 호출해 인박스를 폴링한다.
Vercel은 cron 요청에 `Authorization: Bearer $CRON_SECRET` 헤더를 붙인다.

## 운영 흐름

1. 새 B2B 메일 → (일1회 cron 또는 세팅의 "지금 인박스 확인") → Claude 파싱 + 회신 초안 자동 생성 → 인박스에 딜 추가
2. 인박스에서 딜 열기 → 회신 초안 검토/수정 (필요시 "AI 재생성") → **"Gmail 초안으로 저장"**
3. Gmail 임시보관함에서 사람이 확인 후 직접 발송 → 어드민에서 Stage 수동 변경

## MVP 범위 / v2

**MVP (현재):** 인박스·파이프라인·회사·세팅, 딜 CRUD, AI 회신 생성, Gmail 초안 저장, 일1회 폴링, Basic Auth, few-shot 사례 관리.

**v2 (예정):** 견적/계약 docx 생성·다운로드, 고객 전자서명(`/sign`), 노크 자동화(무응답 7일), 보낸메일 few-shot 자동학습, Slack 알림.
