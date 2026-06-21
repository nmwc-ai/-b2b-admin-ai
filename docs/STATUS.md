# 현황 (STATUS) — 2026-06-21

ANTIEGG B2B 어드민 클라우드 재구축의 현재 상태 요약.

## 🌐 라이브
- **공개 URL**: https://antiegg-b2b-cloud.vercel.app
- **로그인**: Basic Auth (`ADMIN_USER` / `ADMIN_PASSWORD`, Vercel env)
- **레포**: github.com/ruruzene-del/antiegg-b2b-cloud (private)
- **배포**: `git push origin main` → Vercel 자동 배포 (git connect 완료)
- **DB**: Supabase Postgres (transaction pooler), 실데이터 244건
- **AI**: Google Gemini 무료 티어 (`gemini-2.5-flash`) — **활성**, 유료 키 불필요

## ✅ 작동 중 (라이브 검증 완료)
| 기능 | 비고 |
|---|---|
| 로그인 / 전 페이지 | 인박스·개요·파이프라인·회사·검색·세팅·사례·에러·딜추가 |
| 딜 관리 | 생성·수정·조건·단계변경·삭제·검색 (전체 라이프사이클) |
| 개요 대시보드 | KPI(전체/승률/계약액) + 월별추이 + 분포 |
| **AI 회신 초안** | **무료 Google Gemini로 자동 생성** (파싱 + 회신 초안). 키 없으면 빈칸 폴백 |
| 견적/계약 문서 | `/preview` HTML → ⌘P PDF + **DOCX 다운로드** |
| 고객 전자서명 | 공개 `/sign/{token}` (고객 로그인 불필요) → 서명 시 SIGNED |
| 노크(후속) | 무응답 7일 → 노크 단계 + AI 노크 초안, 추가 7일 → 자동 종료 |
| **자동수신** | 홈페이지 폼 → Notion(자동) → **Notion 동기화**로 딜 적재 (일1회 cron + "지금 동기화" 버튼) |
| **회신 초안 → 브랜드 계정** | 회신·노크 초안을 **editor@antiegg.kr** 임시보관함에 저장, 사람이 검토 후 발송 |
| **보낸메일 학습** | 브랜드 계정 보낸함의 B2B 회신을 AI가 분류 → 회신 사례(few-shot) 자동 학습 (일1회 + "사례 학습" 버튼) |
| DB 백업 / 에러 알림 | 매일 백업 Gmail 발송 + 실패/에러 시 운영자 통지 |
| 에러 로깅 / 자동배포 | |

## 📧 메일 계정 분리 (2026-06-21 정합)
| 용도 | 계정 | env |
|---|---|---|
| 고객 대면 — 회신·노크 초안 저장, 보낸메일 학습 | **editor@antiegg.kr** (브랜드) | `BRAND_GMAIL_ADDRESS` / `BRAND_GMAIL_APP_PASSWORD` |
| 시스템 운영 — DB 백업, 에러 알림 | 운영 계정 | `GMAIL_ADDRESS` / `GMAIL_APP_PASSWORD` |

- `BRAND_GMAIL_*`가 **둘 다 설정됐을 때만** 브랜드 계정 사용, 아니면 운영 계정으로 폴백(하위 호환).
- 보낸메일 학습의 증분 커서(`since_uid`)는 계정별 키라, 계정 전환 시 처음부터 재학습.

## ⚠️ 남은 것
- **Slack 알림** — *코드 완료·배포됨, 연결만 대기*.
  - `app/slack.py`가 **봇토큰**(`SLACK_BOT_TOKEN`+`SLACK_CHANNEL`) 또는 **웹훅**(`SLACK_WEBHOOK`) 지원. 미설정 시 graceful no-op.
  - 알림 지점: 신규 B2B 문의 인입(🆕) + 동기화/백업 실패·에러(⚠️).
  - 막힌 지점: 알림봇 **에그몽**(소유자=멤버 "형운", 봇이 알림 채널들에 이미 상주)에 붙일 예정.
    워크스페이스 앱 10개 한도 도달 → 형운에게 **Incoming Webhook URL** 또는 **봇토큰(xoxb-)+채널명**을 받아 env 등록 → 재배포 → `/admin/test-slack` 검증.
- **접근 권한 / 개인정보** — 단일 비번 강화·백업 암호화·보유 정책 (1인 운영이라 후순위).
- **자동 동기화 주기 단축**(실시간화) — Vercel Pro 필요.
- **상용화 전환** — Vercel Pro·Supabase 유료·공식 계정 이관 (사용자 결정: **맨 마지막**).

> 참고: 과거 STATUS의 "ANTHROPIC_API_KEY 필요"는 **해소됨** — 무료 Gemini로 AI 회신이 동작한다. Claude는 `ANTHROPIC_API_KEY` 설정 시 폴백으로만 사용.

## 🔁 자동수신 흐름
```
홈페이지 문의 폼 ──자동──▶ Notion DB ──Notion 동기화──▶ 어드민 딜 + AI 회신초안
                                       (cron 일1회 / "지금 동기화")
```
- 실제 문의는 Gmail로 오지 않고 폼→Notion으로 들어옴 (확인 완료).
- 동기화는 `notion_page_id`로 dedup → 신규 행만 추가, 기존 딜 보존.

## ⏱ 매일 자동 (Vercel Cron `/cron/daily`, 09:00 KST)
Notion 동기화 → 노크 점검 → **보낸메일 학습** → DB 백업(Gmail) → 에러/신규문의 알림(Gmail·Slack)

## 📌 운영자가 챙길 것 (상류 / 검토)
- 폼 → Notion 자동 적재가 계속 도는지 (운영자 영역; 테스트 폼 제출 → Notion 행 확인)
- 견적/계약서에 박히는 회사정보(세팅 페이지) 정확성
- 데이터 보강: 공급가액은 38/244, 서비스명 115/244 (노션 소스 한계)
- 외부 URL에 고객 실데이터 노출 — 정책 검토

## 📦 커밋 이력 (주요)
- `feat: 클라우드 네이티브 재구축 MVP` — Vercel+Supabase
- `feat(dashboard): 개요 대시보드`
- `feat(v2): 견적/계약 문서 생성` · `feat(knock)` · `feat(sign)` · `feat(docx)`
- `feat(ingest): Notion 증분 동기화로 자동수신 전환`
- `feat(ai): 무료 Google Gemini 우선 + Claude 폴백`
- `feat(ingest): 보낸메일 학습 — 회신 분류→few-shot 사례 자동 적재`
- `feat(email): 회신 발송계정 정합 — 브랜드 계정(editor) 분리`
- `feat(slack): 신규 문의·문제 알림 Slack 연결 (코드, 연결 대기)`
