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

## 관측용 처리 환경 (T22 · T36)

앱이 생성하는 모든 HTTP 응답에 `X-Bravo-Env: <APP_ENV>`를 넣습니다. 성공·정적 파일뿐
아니라 401/404/409/422, 쓰기 차단·DB 장애 503, 처리되지 않은 예외의 500에도 적용합니다.
요청 헤더는 사용하지 않으며 기존 `Retry-After`와 WebSocket 계약은 유지합니다.
`APP_ENV`는 영숫자로 시작하는 1~128자 ASCII 식별자이며 이후 `_.:-`도 허용합니다.
잘못된 값은 기동 시 거부합니다. 호스트 이름·사용자 값·비밀값을 넣지 않습니다.

이 헤더는 앱 설정의 표시이지 클라우드 신원 증명이 아닙니다. T18/T36은 등록된 환경과
검증된 요청 경로에 결속해 사용해야 합니다. Edge가 자체 생성한 오류나 응답 유실에는
헤더가 없을 수 있으므로 환경 불명 오류로 처리하고 성공으로 누락하지 않습니다.
프록시 캐시가 이전 응답을 재사용하지 않도록 관측 대상 요청의 캐시 정책도 확인해야 합니다.

## T37 표시용 HTTP 지표

`BRAVO_METRICS_ENDPOINT`를 설정하면 공식 OpenTelemetry SDK의 OTLP/HTTP exporter로
`http.server.request.duration` 히스토그램(초 단위)을 10초마다 보냅니다. 기본은 비활성입니다.
앱 요청 경로에서는 메모리에 기록하며 수집기 응답을 기다리지 않습니다. SDK·exporter는
`uv.lock`에 고정합니다. HTTP 오류도 집계하며 WebSocket 세션은 HTTP 지연에 섞지 않습니다.

- 속성은 HTTP 메서드, 상태 코드, 고정 라우트 템플릿 3개뿐입니다. 첨부 ID·원문 URL·쿼리·
  채팅 본문·별명·헤더·토큰·SQL·trace exemplar는 보내지 않습니다. 알 수 없는 경로/메서드는
  고정된 `unmatched`/`_OTHER`로 묶고 SDK의 시계열 개수 제한을 적용합니다.
- 앱은 `bravo.env`를 정하지 않습니다. 등록된 T37 Collector가 환경을 덮어씁니다.
  앱이 보고한 지표는 운영 화면용이며 T18 외부 부하 창·T36 판정 근거를 대신하지 않습니다.
- 주소는 운영자가 고정한 `/v1/metrics` HTTPS URL입니다. 같은 호스트 sidecar/로컬 시험만
  루프백 HTTP를 허용합니다. 사용자 정보·query·fragment가 있는 URL, 리다이렉트,
  ambient proxy/netrc는 허용하지 않습니다. 범용 `OTEL_EXPORTER_OTLP*` 설정이 함께 있으면
  다른 자격 증명/전송 설정을 몰래 상속하지 않고 기동을 거부합니다.
- 사설 CA는 `BRAVO_METRICS_CA_FILE`, mTLS 클라이언트 쌍은
  `BRAVO_METRICS_CLIENT_CERT`·`BRAVO_METRICS_CLIENT_KEY`의 절대 파일 경로로 설정합니다.
  키를 환경변수 값이나 명령줄에 넣지 않습니다. TLS 인증서 검증을 끄는 옵션은 없습니다.
- Collector 응답은 64 KiB로 제한하며 오류 본문과 원격 reason 문자열을 SDK에 넘기지 않습니다.
- SDK 내보내기 제한은 1 MiB/2,048개 데이터 포인트이며, 전송 timeout 2초와 reader 제한을
  둡니다. 내보내기 실패가 업무 HTTP 응답을 바꾸지 않습니다. 앱의 메모리 집계는 영속 큐가
  아니며 프로세스 종료 시 미전송 지표가 유실될 수 있습니다. Collector의 수신·보존 자격은
  T37에서 별도로 확인합니다. 서버 측 응답 시간은 클라이언트의 종단 지연과 다릅니다.

팀 Collector가 원격 평문 OTLP 포트만 제공하는 경우 이 설정을 바로 활성화하지 않습니다.
TLS 수신 또는 같은 Pod sidecar의 루프백 수신 경로, NetworkPolicy, 인증서 발급·회전,
실제 보존/개인정보 검사를 먼저 연결해야 합니다. 현재 AWS/RKE2 계측 배포 완료를 뜻하지 않습니다.

실제 앱·mTLS·부하창·WebSocket 연결 결과: [로컬 실측](docs/evidence/t22-observability-2026-11-04/README.md).

공식 API: [OpenTelemetry Python exporters](https://opentelemetry.io/docs/languages/python/exporters/),
[OTLP HTTP exporter](https://opentelemetry-python.readthedocs.io/en/latest/exporter/otlp/otlp.html).

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
| `PUT /api/attachments/{id}` | multipart `file`. 클라이언트가 만든 `id`가 멱등 키다 |
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
