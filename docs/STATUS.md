# 현황 (STATUS) — 2026-06-14

ANTIEGG B2B 어드민 클라우드 재구축의 현재 상태 요약.

## 🌐 라이브
- **공개 URL**: https://antiegg-b2b-cloud.vercel.app
- **로그인**: Basic Auth (`ADMIN_USER` / `ADMIN_PASSWORD`, Vercel env)
- **레포**: github.com/ruruzene-del/antiegg-b2b-cloud (private)
- **배포**: `git push origin main` → Vercel 자동 배포 (git connect 완료)
- **DB**: Supabase Postgres (transaction pooler), 실데이터 244건

## ✅ 작동 중 (라이브 검증 완료)
| 기능 | 비고 |
|---|---|
| 로그인 / 전 페이지 | 인박스·개요·파이프라인·회사·검색·세팅·사례·에러·딜추가 |
| 딜 관리 | 생성·수정·조건·단계변경·삭제·검색 (전체 라이프사이클) |
| 개요 대시보드 | KPI(전체/승률/계약액) + 월별추이 + 분포 |
| 견적/계약 문서 | `/preview/quote·contract` HTML → 브라우저 ⌘P로 PDF |
| **자동수신** | 홈페이지 폼 → Notion(자동) → **Notion 동기화**로 딜 적재 (일1회 cron + "지금 동기화" 버튼) |
| 회신 초안 → Gmail | 사람이 검토 후 Gmail에서 발송 |
| 에러 로깅 / 자동배포 | |

## ⚠️ 남은 것 (단 하나)
- **`ANTHROPIC_API_KEY`** (+ 크레딧) → 새 문의 딜의 **AI 회신 초안 자동 생성**.
  - 없어도 시스템은 정상(딜은 들어오고 초안만 빈칸, 사람이 직접 작성).
  - 넣는 법: `vercel env add ANTHROPIC_API_KEY production` → 재배포.

## 🔁 자동수신 흐름
```
홈페이지 문의 폼 ──자동──▶ Notion DB ──Notion 동기화──▶ 어드민 딜 + AI 회신초안
                                       (cron 일1회 / "지금 동기화")
```
- 실제 문의는 Gmail로 오지 않고 폼→Notion으로 들어옴 (확인 완료).
- 동기화는 `notion_page_id`로 dedup → 신규 행만 추가, 기존 딜 보존.

## 📌 운영자가 챙길 것 (상류 / 검토)
- 폼 → Notion 자동 적재가 계속 도는지 (운영자 영역; 테스트 폼 제출 → Notion 행 확인)
- 견적/계약서에 박히는 회사정보(세팅 페이지) 정확성
- 데이터 보강: 공급가액은 38/244, 서비스명 115/244 (노션 소스 한계)
- 외부 URL에 고객 실데이터 노출 — 정책 검토

## 📦 커밋 이력 (주요)
- `feat: 클라우드 네이티브 재구축 MVP` — Vercel+Supabase+Claude
- `feat(dashboard): 개요 대시보드`
- `feat(deal-panel): 노션 견적/계약 링크 + 공급가액`
- `fix(inbox): 초안 저장 딜 추적 버그`
- `feat(v2): 견적/계약 문서 생성`
- `feat(ingest): Notion 증분 동기화로 자동수신 전환`
