# T22 앱·부하창·관측 연결 — 2026-11-04 로컬 실측

실제 PostgreSQL 16, 앱 프로세스 2개, OTel Collector 2개, Prometheus,
T36 HTTP intake를 연결했다. 앱→Collector와 Collector→Prometheus는 **서로 다른 CA의 mTLS**다.
AWS SDK 호출은 moto, Edge는 로컬 Nginx 대역이다. 두 앱은 하나의 DB를 사용한다.
**AWS/RKE2 배포, 실제 Cloudflare, DB 이전, 운영 승인, 무중단 보장은 검증하지 않았다.**

## 결과

- 환경별 30초, 쓰기 50 + 읽기 150 req/s: 각각 쓰기 1,500건·읽기 4,500건.
  전체 12,000 요청에서 HTTP 오류·타임아웃·연결 실패 0. 쓰기 후 실제 DB 대조 성공.
- T18 창 3개/환경, 환경 불명 0, 전송 실패 0. T36 intake가 창을 수신했고
  잘못된 인증은 401, 같은 창 재전송은 409로 거부했다.
- 최신 10초 창 2,000건은 `demo-shift/v1` 기준 PASS.
  이 프로필은 아직 **proposed**다. 본 결과가 실제 컷오버를 승인하거나 배포하지 않는다.
- T37 정상 HTTP 오류율 0, 수집기 중단 후 대상은 UNOBSERVABLE·값 null,
  계속 요청한 출발지의 관측은 유지했다.
- 앱의 메시지·별명·인증값·원문 경로 대신 허용된 지표 라벨만 확인했다.
- 클라이언트 인증서 없는 Collector 접속 거부 + 같은 경로의 정상 mTLS 전송을 함께 확인했다.

## 같은 URL의 WebSocket 전환

Nginx의 upstream만 바꾸고 동일 URL에서 다음을 실제 앱으로 시험했다.
무인증 종료 거부, 쓰기 차단 전 종료 거부, 차단 후 503/Retry-After,
기존 연결 1개의 1012 종료, 대상 연결의 onprem 표시, 마지막 ID 이후 메시지만 수신,
동일 멱등 키 재전송 시 DB 1행·같은 메시지 ID.
이는 **앱/연결 전환 시험이며 DB 컷오버가 아니다.** 관리 API 자체는 기존 PR #10에서 구현됐다.

## 재현

`testing/observability_e2e.py`는 명시한 checkout의 T18 fixture를 재사용한다.
T18 #158(`dea9ced`), T36 #168(`b137d89`), T37 #160의 앱 mTLS 구성 이후 버전이 필요하다.
각 checkout 의존성을 잠금 파일로 설치하고, T18 Python 환경에 WebSocket 시험 클라이언트
`websockets==16.1.1`을 설치한다. T37의 `.venv`에는 API extra가 필요하다.
Docker/Compose, OpenSSL, uv와 로컬 `postgres:16`, 고정 digest Nginx 이미지가 필요하다.

```sh
<loadgen-python> testing/observability_e2e.py \
  --loadgen <T18-checkout>/services/loadgen \
  --observability <T37-checkout>/services/observability \
  --windows <T36-checkout>/services/observability \
  --output <새로운-비공개-결과-디렉터리>
```

실행마다 이름이 다른 자원만 만들고 종료 시 정리한다. 기존 DB/클러스터는 사용하지 않는다.
T18 fixture는 demo-chat의 `.venv`를 `uv sync --no-dev`로 준비하므로,
종료 후 단위 시험에는 `uv sync --locked`가 다시 필요하다.
원본 로그/시험 키는 비공개 디렉터리에 보존하며 저장소에 올리지 않는다.

`summary.json`은 원본의 안전한 수치 투영이며 원본 SHA-256과 앱 소스 해시를 포함한다.
실제 checkout HEAD와 함께 미커밋 소스 해시를 기록했다. 해시는 서명이나 신뢰할 수 있는 발급자 증명이 아니다.

## 별도 검증

- Python 전체 61 통과 후 시작 실패 정리 시험 1개 추가(계측 집중 19 통과), 기존 DB 조건부 3개 제외. 이 문서의 실제 DB 시험은 별도 실행이다.
- Node 10개, Chromium/WebKit 브라우저 14개, QR 산출물 확인 통과.
- 실제 TLS의 신뢰되지 않은 서버·클라이언트 인증서 없음 거부, Collector 응답 64KiB 상한과 오류 본문 제거.
- 앱 이미지 non-root UID 10001·네트워크 없음·읽기 전용 실행 및 health 확인.
- Trivy 0.76.1, 해당 시점 DB 기준 수정 가능한 HIGH/CRITICAL 0건. 모든 취약점 부재를 뜻하지 않는다.
