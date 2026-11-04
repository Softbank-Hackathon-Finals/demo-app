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

### T22 참여 화면

- 휴대폰 화면과 QR 참여: 현재 origin의 주소만 공유하며 query/fragment는 제외한다.
  QR은 고정 버전의 로컬 모듈로 생성한다. 외부 QR 서비스에 주소를 보내지 않는다.
  localhost QR은 다른 휴대폰에서 열리지 않으므로 접근 가능한 서비스 주소가 필요하다.
- 현재 연결된 WebSocket 서버의 `APP_ENV`를 표시한다. 클라우드 신원이나 이전 완료를
  검증하는 배지가 아니다. 연결이 끊기면 이전 값을 최신 환경처럼 표시하지 않는다.
- 첨부 미리보기, 전송 대기 목록, 같은 멱등 키 재시도, 저장 확인/미확인 상태를 구분한다.
  서버 응답만 유실돼도 같은 요청으로 확인한다. POST 응답은 WebSocket 재접속 커서를
  앞당기지 않아 다른 참여자의 중간 메시지를 건너뛰지 않는다.
- 자동 재시도는 업로드/메시지 단계별 최대 60초·32회, 요청별 최대 10초다. 초과 시
  성공·취소로 처리하지 않고 미확인 상태로 남기며 같은 요청을 수동 재시도할 수 있다.
  대기는 10건, 화면 메시지는 최근 500건으로 제한한다. 서버에는 데이터가 그대로 있다.
- 대기 중 내용·파일은 이 탭의 메모리에만 둔다. 새로고침·브라우저 종료·프로세스 종료를
  견디는 영속 outbox가 아니다. 재시도 정지/목록 숨기기는 서버의 쓰기를 취소하지 않는다.
- 인증된 금융 상담 서비스가 아닌 **공유 모의 대화방**이다. 실제 개인정보·계좌·비밀을
  입력하지 않는다. 별명은 사용자 신원을 입증하지 않는다.

```sh
corepack pnpm install --frozen-lockfile
corepack pnpm lint
corepack pnpm test                 # 재시도·커서 계약
corepack pnpm build                # 잠긴 QR 원본과 vendor 파일 일치 검사
corepack pnpm exec playwright install chromium webkit
corepack pnpm test:browser         # Python .venv 준비 후 실행
```

브라우저 시험은 localhost HTTP/WebSocket 호스트에서 재접속, 503 대기, 커밋 후 응답 유실,
모바일 레이아웃, QR 판독, 메시지의 HTML 비실행을 확인한다. 두 시험 앱은 **메모리 저장소**를
공유한다. 테스트 라우트 전환을 AWS→RKE2 이전이나 DB 무결성·무중단 운영 실측으로 세지
않는다. 실제 DB 검증은 별도의 `TEST_DATABASE_URL` 시험이며, EKS·RKE2·Edge 컷오버와
실제 휴대폰의 안정 URL 참여는 팀 통합 후 T22 완료 조건으로 확인한다.

CI의 `platform` 검사에서 기존 Python/Node 검사에 더해 Chromium·WebKit 브라우저 시험을
실행한다. 테스트 호스트·제어 endpoint는 `tests/`에만 있고 서비스 이미지에 포함하지 않는다.

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
| `DB_WAIT_SECONDS` | 연결 풀에서 연결을 기다리는 최대 시간(5초). 넘으면 503 |

## API

| 요청 | 설명 |
|---|---|
| `POST /api/messages` | `{idempotency_key, author, body, attachment_id?}`. 새로 저장하면 201, 같은 키 재전송이면 200(같은 메시지), 같은 키에 다른 내용이면 409 |
| `PUT /api/attachments/{id}` | multipart `file`. 조건부 객체 생성, 같은 ID 재시도는 실제 내용·형식 대조. 불일치409, 저장 결과 미확인503 |
| `GET /api/messages?after=<id>` · `GET /api/attachments/{id}` | 읽기. 쓰기 차단 중에도 된다 |
| `WS /ws?after=<id>` | `hello` → `messages`(id 순) · `status`(쓰기 차단 변경). 마지막 id 이후를 DB에서 1초마다 조회한다. 서버 종료 시 1012로 닫는다 |
| `GET /api/info` | 접속 환경 · 쓰기 차단 상태 |
| `GET /healthz` · `/readyz` | 생존 · 준비. **DB를 보지 않는다** (아래 "운영") |
| `GET /api/health/db` | DB까지 닿는지(2초 제한). 모니터링용, probe로 쓰지 않는다 |

### 쓰기 차단 스위치 (Cutover Executor가 호출)

```bash
curl -X PUT http://<pod>:8080/admin/write-block \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H 'Content-Type: application/json' \
  -d '{"blocked": true}'
# → {"env": "aws", "write_blocked": true}
```

- 켜면 새 업무 쓰기(`POST /api/messages` · `PUT /api/attachments`)를 **503 + `Retry-After`**로 거절하고, 읽기와 WebSocket은 유지한다.
- 상태는 **프로세스마다** 따로 있다. 컷오버 중에는 DB가 멈추므로 DB에 두지 않는다. 그래서 Executor는 **모든 파드(진입점)**에 호출해야 한다. 파드가 새로 뜰 때는 `WRITE_BLOCKED`로 시작 상태를 정한다.
- 클라이언트는 503을 받으면 제한된 범위의 `Retry-After`를 기다렸다가 **같은 멱등 키**로 다시 보낸다. DB의 멱등 처리와 응답 확인을 함께 사용하며, 응답이 없으면 미확인으로 남긴다. 탭 종료나 DB 자체의 데이터 유실까지 보장하지 않는다.

Executor는 전환 후 출발지의 각 앱 파드에 `POST /admin/websockets/close`를 같은
`ADMIN_TOKEN` 인증으로 호출한다. 쓰기 차단이 켜져 있어야 하며, 꺼져 있으면 409다.
현재 프로세스의 연결에 1012 종료 프레임을 보내고 `{"closed": 2, "code": 1012}`처럼
처리한 연결 수를 돌려준다. 연결이 없거나 같은 연결을 다시 닫으면 `closed`는 0이다.
HTTP 요청의 2초 제한을 넘기면 503으로 응답하되, 진행 중인 종료 전송은 취소하지 않는다.
동시 호출과 재시도는 같은 종료 작업을 기다린다. 전송 실패·취소로 완료를 확인할 수 없으면
503을 유지하며, WebSocket 객체의 DISCONNECTED 표시만으로 성공 처리하지 않는다.
종료를 요청한 연결에는 새 메시지를 보내지 않고, 확인되지 않은 종료는 후속 요청에서도 추적한다.
이 호출은 DB를 조회하지 않으며, 종료를 2초 안에 확인하지 못하면 503이다.
메시지 전송과 종료는 연결마다 직렬화한다. 호출 이후 새로 들어오는 연결은 다음 호출의
대상이고, 응답의 수는 클라이언트 재접속 완료를 의미하지 않는다. 앱은 파드마다 단일
프로세스로 실행하고 Executor는 모든 출발지 파드를 호출한다.

## 운영

| 상황 | 앱 동작 | 확인 (로컬 compose) |
|---|---|---|
| DB 중단 · PgBouncer PAUSE · 풀 대기 초과 · `statement_timeout` | 모든 요청이 **503 + `Retry-After`**. 500을 내지 않는다. 클라이언트는 같은 멱등 키로 재시도 | DB를 멈추자 쓰기 503, 다시 켜고 1초 안에 201 |
| 컷오버 중 DB 멈춤 | `/readyz`는 200을 유지해 파드가 서비스에서 빠지지 않는다. WebSocket도 유지된다 | DB 중단 중 `/readyz` 200, `/api/health/db` 503 |
| DB 비밀번호 회전 | 새 연결이 인증에 실패하면 Secret을 다시 읽어 한 번 재시도한다(그 외에도 5분마다 갱신). 기존 연결은 그대로 쓴다. 재시작 불필요 | 회전 + 기존 연결 종료 후 첫 요청 503, 다음 재시도 201 |

배포(T6 · T30) 시 probe는 `livenessProbe: /healthz`, `readinessProbe: /readyz`로 둔다. DB를 보는 `/api/health/db`를 readiness로 쓰면 컷오버 순간 모든 파드가 빠져 트래픽이 끊긴다.

## 첨부 객체의 불변성과 재시도

같은 첨부 ID는 최초 저장한 바이트·Content-Type을 가리킨다. `If-None-Match: *` 조건부
생성을 사용하며 기존 객체를 덮어쓰지 않는다. 412 응답을 받은 재시도는 실제 객체 내용을
제한된 크기로 읽고 일치할 때만 DB 메타데이터 처리로 진행한다. ETag를 내용 해시로
가정하거나 객체 존재만으로 성공 처리하지 않는다. 다른 내용은 409, 인증/접속 실패·409
조건 경합·지원되지 않는 조건부 요청·잘못된 응답은 503으로 남긴다. 조건 없는 PUT으로
재시도하지 않는다. 기본 SDK 연결/읽기 제한은 각각 5초, 총 시도는 2회이며 HTTP 전체 기한은 아니다.

DB와 객체 저장소 사이에 분산 트랜잭션은 없다. 객체 생성 뒤 DB 저장이 실패하면 고아
객체가 남을 수 있으며, 동일 ID·동일 내용 재시도로 복구한다. 기존 객체를 지워서 보상하지
않는다. 금융 서비스 운영 자격·업무별 접근 통제·고아 객체 수명 정책은 별도다.

앱 외부 writer가 객체를 수정/삭제할 수 있다면 불변성은 유지되지 않는다. 운영자는
앱 역할의 삭제/덮어쓰기 권한과 버킷 정책을 통제하고, 실제 S3 호환 목적지에서 조건부
쓰기의 동시성·거부 동작을 검증해야 한다. 이 변경은 버킷 정책을 자동 수정하지 않는다.
지원하지 않는 목적지를 무조건 PUT으로 우회하는 fallback은 없다.

근거: [AWS S3 조건부 쓰기](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html),
[버킷 정책으로 조건부 쓰기 강제](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes-enforce.html).
