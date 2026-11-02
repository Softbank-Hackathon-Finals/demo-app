# 에이전트 작업 안내

이 레포는 클라우드 출구전략 플랫폼의 이전 대상 데모 앱(금융 상담 실시간 채팅)이다. 작업 계획 · 이슈 · 설계 문서는 [`platform`](https://github.com/Softbank-Hackathon-Finals/platform) 레포에 있다.

## 작업 전에

1. 작업은 `platform`의 `docs/tasks.md`와 이슈에서 찾는다(데모 앱은 주로 T4 · T22). 진행 상황의 기준은 [프로젝트 보드](https://github.com/orgs/Softbank-Hackathon-Finals/projects/2)다.
2. `main`은 보호돼 있다. 브랜치에서 작업하고 PR로 올린다(CI 통과 시 자동 머지). `.github/workflows/`와 `CODEOWNERS`를 바꾸는 PR만 본인 외 팀원 1명의 승인이 필요하다.
3. 커밋 메시지와 PR 제목 앞에 `[T번호]`. PR 본문에 `Refs Softbank-Hackathon-Finals/platform#이슈번호`, 마지막 PR이면 `Closes Softbank-Hackathon-Finals/platform#이슈번호`. 끝낸 할 일은 `platform` 이슈에서 체크한다.

## 앱이 지켜야 할 조건

설계 문서 8.10(`platform/docs/design.html`)의 조건이다. 플랫폼이 이 앱을 옮기려면 반드시 지킨다. 조건이 바뀌면 설계 문서가 기준이다.

| 조건 | 이유 |
|---|---|
| PostgreSQL 16+, 모든 테이블에 PK, 앱 전용 로그인 역할 | 논리 복제와 펜싱의 전제 |
| 메시지 쓰기마다 멱등 키(클라이언트 생성) | 쓰기 정지 중 재시도의 유실 · 중복 방지 |
| WebSocket 끊김 시 마지막 메시지 ID로 자동 재접속 | 컷오버 때 1012로 닫아도 사용자가 끊김을 거의 못 느낌 |
| 첨부파일은 S3 SDK로 저장 (엔드포인트 설정 가능) | "설정 교체" 등급 의존성의 예 |
| DB 비밀번호는 Secrets Manager SDK로 직접 조회 | "코드 수정" 등급 의존성. S2 데모의 재료(의도된 의존성) |
| 쓰기 차단 스위치: 켜면 새 업무 쓰기를 503 + Retry-After로 거절, 읽기는 유지 | 컷오버의 진입점 쓰기 차단 수단 (Executor가 호출) |
| QR 접속 페이지, 접속 환경(AWS / 온프렘) 표시 | 심사위원이 직접 참여하고 전환을 눈으로 확인 |
| 최대 트랜잭션 200ms 이하, DDL은 전환 창 밖에서 | PgBouncer PAUSE가 제때 끝나야 함 |
| 메시지 전파는 공통 쓰기 DB 기준(마지막 메시지 ID 이후를 짧은 주기로 조회). 앱 서버 메모리 · 환경별 pub/sub · LISTEN/NOTIFY에 의존하지 않음 | 트래픽 전환 중 두 환경의 앱이 서로의 메시지를 봄. PgBouncer transaction 모드에서는 LISTEN/NOTIFY 불가 |

Secrets Manager 직접 조회는 플랫폼이 찾아 고치는 대상으로 **일부러 남기는** 의존성이다. 미리 추상화하거나 없애지 않는다.

## 보안 규칙

- 시크릿 값을 코드 · 로그 · 커밋에 남기지 않는다. 로컬 값은 `.env`(커밋 안 함), 예시는 `.env.example`.
- 검사 예외는 `.gitleaks.toml` · `.trivyignore`에 사유 주석과 함께만 추가한다.

## 검증

PR마다 CI(`ci.yml`)가 `secrets` · `infra` · `platform`을 검사한다. CI 실패를 워크플로를 고쳐서 넘기지 않는다. 로컬에서는 Node(`pnpm lint` · `test` · `build`) 또는 Python(`uvx ruff check .` · `uv run pytest`) 등 바꾼 부분의 검사를 돌린다.
