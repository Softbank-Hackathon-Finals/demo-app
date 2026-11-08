# 브라보톡 · 메신저 데모

클라우드 출구전략 플랫폼([`platform`](https://github.com/Softbank-Hackathon-Finals/platform))의 이전 대상 데모 앱이다. 카카오톡 스타일의 독립 메신저로, 한 개의 공개 대화방에서 실제 메시지·첨부파일과 모의 포인트를 주고받는다.

노란 말풍선·파란 대화 배경과 데스크톱 대화 목록, 모바일 대화방을 제공한다. 입력창의 **송금하기**에서 지갑 생성 → 받는 사람 → 금액 → 확인을 진행하고, 저장한 송금은 대화 카드로도 표시한다. 참여자 목록은 실제 대화·지갑 기록의 별명으로 구성하며 온라인 사용자나 별도 1:1 방을 만들어 표시하지 않는다. 대화 검색은 현재 브라우저에 표시한 최근 메시지를 대상으로 한다. 이모지·첨부 미리보기·QR 초대·전환 관찰도 같은 화면에서 이용한다.

포인트는 기존 메시지 API에 저장한 이벤트를 브라우저가 계산하는 **모의 원장**이다. 서버의 계좌 소유권·잔액 검증, 실제 송금·결제, 카카오 계정 연동을 제공하지 않는다. 총 잔액 표시는 전체 데이터 무결성 증거를 대신하지 않으며 실제 이전 판정은 플랫폼의 T13/T18/T19가 담당한다. 기능·시험·경계는 [메신저 개발 기록](docs/messenger-ui-2026-11-07.md)에 있다.

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
공유한다. 다음 명령은 새 PostgreSQL을 직접 만들고 비관리자 `chat_app` 역할의 실제
`Store`로 Python 전체와 같은 Chromium·WebKit 시험을 실행한다. 기존 DB에 연결하지
않으며 실행이 끝나면 이번 DB 프로세스를 종료한다. 첨부 바이트 저장소는 시험 대역이다.

```sh
uv run python testing/browser_database.py \
  --pg-bin "$(pg_config --bindir)" --output "$PRIVATE_OUTPUT"
```

PostgreSQL 서버 도구와 설치된 브라우저가 필요하다. 출력은 존재하지 않는 비공개
디렉터리로 지정한다. 쓰기 정지 중 대기한 요청, 동일 주소에서의 1012 재접속, 다른
참가자의 중간 메시지 수신과 같은 키로 한 번만 저장되는 흐름을 함께 확인한다.
첨부 성공 응답 유실 시험은 실제 브라우저 multipart를 바꾸지 않고, 저장이 끝난 뒤
시험 호스트가 성공 응답만 503으로 바꾼다. 시험용 관리 라우트는 서비스 이미지에 없다.

이것은 같은 PostgreSQL을 사용하는 로컬 앱 전환이다. AWS→RKE2나 DB 이전·무중단
운영 실측을 뜻하지 않는다. EKS·RKE2·Edge 컷오버와 실제 휴대폰의 안정 URL 참여는
팀 통합 후 T22 완료 조건으로 확인한다.

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
| `ATTACHMENT_UPLOAD_ENABLED` | 새 첨부 업로드 허용. 기본 `true`, 이전 복사 전 모든 serving 파드에 `false` 적용. 소문자 `true`/`false` 외에는 시작 실패 |
| `POLL_INTERVAL` · `TX_TIMEOUT_MS` · `WRITE_BLOCK_RETRY_AFTER` | 전파 주기(1초) · 쓰기 트랜잭션 제한(200ms) · `Retry-After`(2초) |
| `DB_WAIT_SECONDS` | 연결 풀에서 연결을 기다리는 최대 시간(5초). 넘으면 503 |
| `AUTH_ISSUER` · `AUTH_CLIENT_ID` | 로그인(OIDC) 발급자 주소와 공개 클라이언트 ID. 둘 다 비우면 로그인 없이 지금처럼 동작한다. 하나만 넣으면 시작 실패 |
| `AUTH_ALSO_TRUST` | 서명만 믿는 다른 발급자. `발급자\|클라이언트ID`를 쉼표로 잇는다. 이사 중 양쪽 앱이 서로의 토큰을 받을 때 쓴다 |

## 로그인 (Cognito · Keycloak)

`AUTH_ISSUER`를 넣으면 메시지와 첨부 **쓰기**에 로그인이 필요하다. 읽기(대화 보기 · WebSocket)는 로그인 없이 그대로다.
표준 OIDC만 쓰므로 Cognito 사용자 풀과 Keycloak realm이 같은 코드로 동작한다. 발급자를 바꾸는 일은 **설정 교체**다.

| 발급자 | `AUTH_ISSUER` 예 |
|---|---|
| Amazon Cognito | `https://cognito-idp.<region>.amazonaws.com/<user-pool-id>` |
| Keycloak | `https://<호스트>/realms/<realm>` |

- 브라우저는 Authorization Code + PKCE(S256)로 로그인한다. 클라이언트 비밀은 없다. 콜백 주소는 앱 주소의 `/`다.
- 서버는 ID 토큰의 RS256 서명(발급자 공개 키), `iss`, `aud`(= 클라이언트 ID), 만료를 확인한다. Cognito 액세스 토큰(`token_use=access`)은 받지 않는다.
- 작성자 이름은 토큰의 `preferred_username` → `cognito:username` → `email` → `name` → `sub` 순으로 고른다(40자). 다른 이름으로 쓰면 **403**이다. 서버가 이름을 바꿔 저장하지 않는다(브라우저가 응답의 작성자를 대조한다).
- 쓰기 차단 중에는 로그인보다 먼저 503 + `Retry-After`가 나간다. 컷오버 응답은 로그인 여부와 무관하다.
- 발급자 문서나 공개 키를 못 받으면 503 + `Retry-After`다(토큰 거절 401과 구별).
- **이사 중:** 트래픽을 나누는 동안 요청은 세션 고정 없이 출발지·목적지로 섞인다. 그래서 양쪽 앱이 서로의 발급자를 믿는다.
  | 앱 | `AUTH_ISSUER` | `AUTH_ALSO_TRUST` |
  |---|---|---|
  | 출발지 | 출발지 Cognito | 목적지 Keycloak`\|demo-chat` |
  | 목적지 | 목적지 Keycloak | 출발지 Cognito`\|<클라이언트 ID>` |

  - 어느 쪽에서 로그인했든 다음 쓰기가 다른 쪽으로 가도 받아진다. 출발지 앱은 Keycloak 공개 키를 받으러 목적지 로그인 주소에 나갈 수 있어야 한다.
  - 로그인은 시작한 쪽 발급자로 마친다. 로그인 창에서 돌아온 페이지가 다른 쪽 앱에서 와도 브라우저가 시작할 때 남긴 토큰 주소로 코드를 바꾼다. 그래서 두 발급자 모두 같은 콜백(앱 주소의 `/`)을 등록해 둔다.
  - 이사가 끝나면 목적지의 `AUTH_ALSO_TRUST`를 지워 AWS 의존을 끊는다.
- **한계:** Cognito는 비밀번호 해시를 내보내지 않는다. 사용자는 옮길 수 있어도 비밀번호는 옮길 수 없어 목적지에서 비밀번호를 다시 정해야 한다.
- 부하 생성기(T18)처럼 사람이 아닌 쓰기도 토큰이 필요하다. 로그인을 켠 상태의 부하 측정은 별도 계정과 토큰 발급이 먼저다.

## API

| 요청 | 설명 |
|---|---|
| `POST /api/messages` | `{idempotency_key, author, body, attachment_id?}`. 새로 저장하면 201, 같은 키 재전송이면 200(같은 메시지), 같은 키에 다른 내용이면 409 |
| `PUT /api/attachments/{id}` | multipart `file`. 조건부 객체 생성, 같은 ID 재시도는 실제 내용·형식 대조. 불일치409, 저장 결과 미확인503 |
| `GET /api/messages?after=<id>` · `GET /api/attachments/{id}` | 읽기. 쓰기 차단 중에도 된다 |
| `WS /ws?after=<id>` | `hello` → `messages`(id 순) · `status`(쓰기 차단 변경). 마지막 id 이후를 DB에서 1초마다 조회한다. 서버 종료 시 1012로 닫는다 |
| `GET /api/info` | 접속 환경 · 쓰기 차단 상태 · `attachment_upload_enabled` (현재 프로세스의 시작 정책) |
| `GET /api/auth/config` | 로그인 설정. 꺼져 있으면 `{"enabled": false}`, 켜져 있으면 발급자 · 클라이언트 ID · 로그인/토큰 주소 |
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

### 첨부파일 복사 중 새 업로드 제한 (T8 → T22/T28 인계)

`ATTACHMENT_UPLOAD_ENABLED=false`로 시작한 프로세스는 `PUT /api/attachments/*`를
multipart 본문을 읽기 전에 403으로 거부한다. DB 메타데이터·S3 객체에 접근하지 않는다.
기존 첨부 다운로드와 텍스트 메시지 쓰기는 유지한다. 전체 쓰기 차단 스위치는 별개이고,
`/admin/write-block`을 해제해도 이 시작 정책이 바뀌지 않는다. 정책을 바꾸려면 재배포한다.
일시 재시도용 503이 아닌 정책 거부이므로 `Retry-After`를 반환하지 않는다.

`/api/info`와 WebSocket `hello`/`status`에 현재 프로세스의 허용 여부를 덧붙인다.
화면은 허용이 확인되기 전 첨부 선택을 막고, 연결 대상이 차단 상태로 바뀌면 아직 선택만 한
파일과 미리보기를 비운다. 이미 전송을 시도한 대기 항목은 자동 삭제하거나 새 멱등 키로
다시 보내지 않는다. 서버의 거부가 최종 경계이며 UI 조작으로 우회할 수 없다.

이 기능 하나가 **클러스터 전체의 업로드 종료·복사 무결성을 증명하지는 않는다.**
T8/T12/T28 실행 경로는 다음을 별도로 확인해야 한다.

1. 출발지·목적지의 모든 실제 serving 파드에 같은 정책을 적용하고, 파드 UID·이미지 digest·
   `/api/info`의 값을 대조한다. 공유 주소 한 번 조회로 모든 파드의 상태를 대신하지 않는다.
2. 이전 버전 파드와 실행 중인 업로드를 종료·배출하고 다른 버킷 writer도 통제한다.
   이미 시작된 요청, DB 실패 후 남은 객체, 직접 S3 쓰기는 이 시작 정책이 통제하지 않는다.
3. 그 뒤 T8 inventory/copy를 수행하고 최종 메타데이터·객체 내용 대조 결과를 게이트에 연결한다.
4. 대상 검증과 전환 승인 뒤 대상 업로드를 다시 열지 결정한다. 출발지는 복귀 정책에 따라
   계속 제한한다. 등록되지 않은 writer나 배출 상태가 불명확하면 복사 완료로 판정하지 않는다.

로컬 시험은 본문 미소비·직접 업로드 거부·기존 읽기/채팅 유지·시작 설정 오타 거부를
검증한다. 팀 배포 설정, 실제 S3/SeaweedFS 복사, 모든 파드 배출은 별도 인수 대상이다.

## 운영

| 상황 | 앱 동작 | 확인 (로컬 compose) |
|---|---|---|
| DB 중단 · PgBouncer PAUSE · 풀 대기 초과 · `statement_timeout` | 모든 요청이 **503 + `Retry-After`**. 500을 내지 않는다. 클라이언트는 같은 멱등 키로 재시도 | DB를 멈추자 쓰기 503, 다시 켜고 1초 안에 201 |
| 컷오버 중 DB 멈춤 | `/readyz`는 200을 유지해 파드가 서비스에서 빠지지 않는다. WebSocket도 유지된다 | DB 중단 중 `/readyz` 200, `/api/health/db` 503 |
| DB 비밀번호 회전 | 새 연결이 인증에 실패하면 Secret을 다시 읽어 한 번 재시도한다(그 외에도 5분마다 갱신). 기존 연결은 그대로 쓴다. 재시작 불필요 | 회전 + 기존 연결 종료 후 첫 요청 503, 다음 재시도 201 |

배포(T6 · T30) 시 probe는 `livenessProbe: /healthz`, `readinessProbe: /readyz`로 둔다. DB를 보는 `/api/health/db`를 readiness로 쓰면 컷오버 순간 모든 파드가 빠져 트래픽이 끊긴다.

### 유휴 WebSocket의 수신 중단 확인

새 서버는 최초 `hello`에 `heartbeat_interval_seconds: 5`를 광고하고, 상태 변경이나
메시지가 없어도 기존 `status` 프레임을 5초마다 보낸다. 별도 DB 쓰기·추가 쿼리·클라이언트
송신은 만들지 않고 기존 조회 루프와 소켓 전송 잠금을 사용한다.

새 UI는 이 값을 정확한 숫자 5로 받은 연결만 감시한다. 유효 프레임이 15초 동안
오지 않으면 온라인 표시를 내리고 원래 마지막 메시지 ID로 다시 연결한다. 타이머가
정지하는 브라우저 백그라운드·절전 상태까지 15초의 실시간 상한을 보장하지 않는다.
다시 보이는 탭은 기존 visibility 처리로 새 연결을 확인한다.

협상 필드가 없는 구형 서버에는 유휴 제한을 임의 적용하지 않는다. 숫자 문자열·boolean·
미지원 주기는 거부한다. 서버와 정적 자산을 같은 검증 이미지로 배포하고 실행 자산도
확인해야 새 동작을 인수할 수 있다. 이 연결 상태는 서버 수신 경로의 생존 표시이며
DB 정상성·쓰기 성공·컷오버 완료·실행 권한을 증명하지 않는다.

[격리 PostgreSQL·브라우저 검증](docs/evidence/websocket-liveness-2026-11-06/README.md)에
재현 조건과 운영 미검증 범위를 남겼다.

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
