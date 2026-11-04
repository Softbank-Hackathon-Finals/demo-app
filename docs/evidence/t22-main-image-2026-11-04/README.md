# 통합 main 이미지 발행

첨부 조건부 저장 #17과 업로드 동결 #15를 병합한 main `8d82a924`를 기존
[publish-image 실행](https://github.com/Softbank-Hackathon-Finals/demo-chat/actions/runs/41803312129)으로 발행했다.

```text
ghcr.io/softbank-hackathon-finals/demo-chat@sha256:ca5ce1b24f1deb2e7346643860ff0bb151409c1acec503c105f56d9043a389b6
```

- 대상은 linux/amd64다. 소스 revision 라벨과 main 커밋이 일치한다.
- 같은 OCI 바이트를 검사한 뒤 digest를 보존해 발행하고 registry에서 다시 pull했다.
- Trivy의 HIGH/CRITICAL·수정 가능 항목 정책에서 취약점 0개와 비밀 패턴 0개였다.
  모든 취약점·런타임 공격이 없다는 의미는 아니다.
- 최종 이미지 UID 10001, 앱 import·health, installer 제거 검사를 네트워크 없이 통과했다.
- provenance/SBOM 생성 경로와 attestation manifest 존재를 확인했다.

[receipt.json](receipt.json)은 발행·readback 결과와 비공개 원본 해시를 기록한다.
이 이미지에는 성능 자격이 보류된 #16이나 시험 전용 DB·브라우저 도구가 들어 있지 않다.
클러스터에서의 pull·배포·실제 AWS→온프렘 전환은 아직 확인하지 않았다.
