# 메시지 개인정보 검사 함수 (T46)

메시지를 저장하면 앱이 SQS 큐에 `{"message_id": N}`을 보낸다(`app/events.py`, `PII_CHECK_QUEUE_URL`을 설정했을 때만). 이 함수가 메시지 본문을 DB에서 읽어 주민등록번호 · 카드번호 · 계좌번호 패턴을 찾고, `message_checks`에 종류와 개수만 남긴다. 일치한 값과 본문은 저장하거나 로그에 남기지 않는다.

- **AWS:** SQS → Lambda(Python 3.12, 핸들러 `pii_check.handler`, 배치 크기 10, 제한 시간 5초). DB 접속 문자열은 `DB_DSN`.
- **온프렘:** 같은 파일을 platform의 호환 런타임이 그대로 부른다(platform `services/function-runtime`, ADR-0045 · 0046 · 0047). 앱은 `AWS_ENDPOINT_URL_SQS`만 바꾼다.
- 한 이벤트는 짧은 트랜잭션 하나로 끝낸다(잠금 대기 50ms · 문장 80ms · 유휴 트랜잭션 150ms 상한). 컷오버의 긴 트랜잭션 판정(200ms)에 걸리지 않기 위해서다.
- 결과 테이블 PK는 `message_id`라 같은 메시지를 여러 번 보내도 결과는 한 행이다.

## 로컬 끝까지 시험

```sh
# platform의 런타임 베이스 이미지를 먼저 만든다(t46-function-runtime:local)
(cd <platform>/services/function-runtime/testing/local && docker compose build runtime-base)
docker compose -f compose.yaml -f compose.pii-check.yaml up -d --build
python3 testing/pii_check/e2e.py
docker compose -f compose.yaml -f compose.pii-check.yaml down -v
```

`compose.pii-check.yaml`은 기본 `compose.yaml`에 덧씌워 두 앱(aws · onprem)에 검사를 켜고, ElasticMQ와 호환 런타임을 더한다. 기본 구성은 바뀌지 않는다.

2026-11-08 결과(새 환경 2회, 모두 PASS): 두 앱이 저장한 20건 모두 검사되고 판정 일치 / 큐를 멈추면 503이고 메시지는 저장되며, 큐를 다시 켠 뒤 같은 멱등 키로 보내면 200과 함께 검사됨 / 결과 테이블 · 앱 · 함수 로그에 일치한 값과 본문 0건.
