# T22 첨부파일 멱등 저장과 실패 복구

같은 첨부 ID·파일명·크기로 다른 바이트를 올리면 기존 객체가 덮어써지던 경로를
조건부 생성으로 바꾼다. `If-None-Match: *`를 지원하는 저장소에서만 사용하며,
412의 실제 객체 바이트·길이·Content-Type이 모두 일치해야 재시도 성공으로 처리한다.
ETag나 호출자가 넣은 메타데이터만으로 동일성을 판단하지 않는다.

- 다른 내용은 409, 권한 거부·알 수 없는 412·409 경합·조건 미지원·응답 유실은 503이다.
- 불확실한 결과를 무조건 PUT으로 재시도하거나 새 ID로 바꾸지 않는다.
- 비교 GET은 입력 길이+1 이내로 읽고 HTTP body를 닫는다. SDK 연결/읽기 기한은 각5초,
  최대 시도2회다. 이것은 전체 API 실행 시간 상한을 뜻하지 않는다.

## 실제 로컬 검증

SeaweedFS 4.52의 고정 digest, 버킷 한정 임시 자격 증명, 실제 앱 boto3 설정으로 시험했다.
별도 PostgreSQL17 Unix socket·비관리자 앱 역할과 실제 앱 ASGI/Store를 추가로 연결했다.

1. 같은 ID·같은 내용 재시도 성공, 같은 크기의 다른 내용과 다른 MIME 거부.
2. 다른 바이트8개 동시 제출: 1개 수락,7개 충돌. 실제 읽기 내용은 수락된1개와 일치.
3. 실제 PUT 직후 응답 유실 주입: 성공으로 표시하지 않음. 같은 ID 재시도로 동일 객체 확인.
4. 실제 DB INSERT 권한 회수: 파일은 저장됐지만 메타데이터0행, API500.
5. 권한 복구 후 같은 ID 재시도: API200·메타데이터1행. 반복해도1행, 다른 내용409, 원문 유지.
6. 만든 컨테이너는 정확한 생성 ID로 정리하고, 임시 PostgreSQL은 종료.

`result.json`은 비공개 원문 해시와 코드 해시를 포함한 공개 요약이다.
실제 SQL 권한 오류는 기존 예외 처리에 따라500이며, 이 PR이 모든 실패를503으로
변환하거나 분산 트랜잭션을 제공한다고 주장하지 않는다. 객체만 남은 상태는 가능하다.

## 재현

검토한 platform의 T8 컨테이너 도우미와 네이티브 PostgreSQL 실행 파일을 지정한다.
로컬 Unix Docker context만 허용하고 팀·클라우드 자격 증명은 사용하지 않는다.
생성 키는 테스트용이며 명령 인자·Git 산출물에 기록하지 않는다.

```sh
.venv/bin/python testing/immutable_attachments.py \
  --platform /path/to/reviewed/platform \
  --postgres-bin /path/to/postgresql/bin \
  --output /private/path/new-qualification
```

## 운영 인수 조건

이 검증은 로컬 단일 SeaweedFS·Unix socket PostgreSQL이며 AWS S3, 팀 RKE2,
운영 TLS/권한/다중 replica 저장소의 동시성 자격을 대신하지 않는다. 외부 writer의
무조건 PUT/삭제까지 이 앱이 막는 것은 아니다. 별도 버킷 정책과 쓰기 주체 통제가 필요하다.
T8 최종 inventory 전 모든 앱의 업로드 차단·진행 중 업로드 배출·외부 writer 차단과
객체/DB 대조가 필요하다. 이 PR만으로 복사 완료·이전 성공을 선언하지 않는다.
