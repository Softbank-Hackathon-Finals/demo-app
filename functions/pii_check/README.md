# 메시지 개인정보 검사 함수 (T46)

메시지를 저장하면 앱이 SQS 큐에 `{"message_id": N}`을 보낸다(`app/events.py`, `PII_CHECK_QUEUE_URL`을 설정했을 때만). 이 함수가 메시지 본문을 DB에서 읽어 주민등록번호 · 카드번호 · 계좌번호 패턴을 찾고, `message_checks`에 종류와 개수만 남긴다. 일치한 값과 본문은 저장하거나 로그에 남기지 않는다.

- **AWS:** SQS → Lambda(Python 3.12, 핸들러 `pii_check.handler`, 배치 크기 10, 제한 시간 5초). DB 접속 문자열은 `DB_DSN`.
- **온프렘:** 같은 파일을 platform의 호환 런타임이 그대로 부른다(platform `services/function-runtime`, ADR-0045 · 0046 · 0047). 앱은 `AWS_ENDPOINT_URL_SQS`만 바꾼다.
- 한 이벤트는 짧은 트랜잭션 하나로 끝낸다(잠금 대기 50ms · 문장 80ms · 유휴 트랜잭션 150ms 상한). 컷오버의 긴 트랜잭션 판정(200ms)에 걸리지 않기 위해서다.
- 결과 테이블 PK는 `message_id`라 같은 메시지를 여러 번 보내도 결과는 한 행이다.
