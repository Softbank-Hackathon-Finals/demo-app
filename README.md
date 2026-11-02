# demo-chat

클라우드 출구전략 플랫폼([`platform`](https://github.com/Softbank-Hackathon-Finals/platform))의 이전 대상 데모 앱이다. 금융 상담 맥락의 사람 간 실시간 채팅(WebSocket)이다.

AWS 출발지와 온프레미스 목적지에 같은 이미지(GHCR, digest 고정)로 배포된다. 플랫폼이 이 앱을 옮기려면 설계 문서 8.10의 데모 앱 요구사항을 지켜야 한다.

## 로컬 실행

```bash
docker compose up -d --build   # Postgres 16 · moto(S3 · Secrets Manager 흉내) · 앱 두 개
open http://localhost:8081     # APP_ENV=aws
open http://localhost:8082     # APP_ENV=onprem (같은 DB를 쓴다)
```

두 앱은 같은 DB를 기준으로 메시지를 전파하므로 한쪽에서 보낸 메시지가 다른 쪽에 보인다. 트래픽 전환 중 두 환경이 동시에 떠 있는 상황과 같다.

테스트: `uv run pytest` (DB 테스트는 `TEST_DATABASE_URL=postgresql://chat_app:local-only-app@localhost:5432/chat`이 있을 때만 돈다)

## 구성

| 파일 | 내용 |
|---|---|
| `app/main.py` | HTTP · WebSocket API, 쓰기 차단 스위치 |
| `app/store.py` | Postgres 접근. 멱등 키, 커밋 순서 = id 순서 |
| `app/db_secret.py` | DB 자격 증명을 **Secrets Manager SDK로 직접 조회** (의도된 "코드 수정" 등급 의존성) |
| `app/storage.py` | 첨부를 **S3 SDK**로 저장 (`S3_ENDPOINT_URL`만 바꾸면 S3 호환 저장소로) |
| `db/schema.sql` | 스키마. 관리자 역할로 적용하고 앱(`chat_app`)은 `SELECT` · `INSERT`만 가진다 |

## 환경변수

| 이름 | 설명 |
|---|---|
| `APP_ENV` | 화면에 표시할 접속 환경 (`aws` · `onprem`) |
| `DB_HOST` · `DB_PORT` · `DB_NAME` | DB(또는 PgBouncer) 주소 |
| `DB_SECRET_ID` | Secrets Manager 항목. `{"username": …, "password": …}` |
| `S3_BUCKET` · `S3_ENDPOINT_URL` · `S3_FORCE_PATH_STYLE` | 첨부 저장소 |
| `ADMIN_TOKEN` | 쓰기 차단 API 토큰. 없으면 API가 꺼진다(404) |
| `WRITE_BLOCKED` | 시작할 때 쓰기 차단 상태 (`true`/`false`) |
| `POLL_INTERVAL` · `TX_TIMEOUT_MS` · `WRITE_BLOCK_RETRY_AFTER` | 전파 주기(1초) · 쓰기 트랜잭션 제한(200ms) · `Retry-After`(2초) |

## API

| 요청 | 설명 |
|---|---|
| `POST /api/messages` | `{idempotency_key, author, body, attachment_id?}`. 새로 저장하면 201, 같은 키 재전송이면 200(같은 메시지), 같은 키에 다른 내용이면 409 |
| `PUT /api/attachments/{id}` | multipart `file`. 클라이언트가 만든 `id`가 멱등 키다 |
| `GET /api/messages?after=<id>` · `GET /api/attachments/{id}` | 읽기. 쓰기 차단 중에도 된다 |
| `WS /ws?after=<id>` | `hello` → `messages`(id 순) · `status`(쓰기 차단 변경). 마지막 id 이후를 DB에서 1초마다 조회한다. 서버 종료 시 1012로 닫는다 |
| `GET /api/info` · `/healthz` · `/readyz` | 접속 환경 · 쓰기 차단 상태, 생존 · DB 준비 |

### 쓰기 차단 스위치 (Cutover Executor가 호출)

```bash
curl -X PUT http://<pod>:8080/admin/write-block \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"blocked": true}'
# → {"env": "aws", "write_blocked": true}
```

- 켜면 새 업무 쓰기(`POST /api/messages` · `PUT /api/attachments`)를 **503 + `Retry-After`**로 거절하고, 읽기와 WebSocket은 유지한다.
- 상태는 **프로세스마다** 따로 있다. 컷오버 중에는 DB가 멈추므로 DB에 두지 않는다. 그래서 Executor는 **모든 파드(진입점)**에 호출해야 한다. 파드가 새로 뜰 때는 `WRITE_BLOCKED`로 시작 상태를 정한다.
- 클라이언트는 503을 받으면 `Retry-After`만큼 기다렸다가 **같은 멱등 키**로 다시 보낸다. 그래서 쓰기 정지 중에도 유실이나 중복이 없다.
