# 고정 소스로 GHCR 이미지 게시

`publish-image`는 검토된 `main`의 전체 commit SHA를 받아 추적된 파일만 빌드한다. 로컬 `.env`, 수정된 파일, 작업 폴더의 미추적 파일은 빌드 컨텍스트에 포함하지 않는다. Secrets Manager 직접 조회 등 데모 앱의 의도된 동작은 바꾸지 않는다.

## 실행과 증거

1. `source_sha`에 검토한 `main` 커밋의 40자리 SHA를 넣어 workflow를 실행한다. 소스가 현재 `origin/main`의 조상이 아니면 실패한다.
2. `git archive`로 생성한 컨텍스트와 소스 manifest를 만들고 기존 Dockerfile로 `linux/amd64` OCI 이미지를 한 번 빌드한다.
3. 그 OCI layout 디렉터리에 Trivy HIGH/CRITICAL 취약점(수정 가능한 항목)과 secret 검사를 수행한다. 실패하면 게시하지 않는다. 검사 예외는 추가하지 않는다. Trivy의 OCI 입력은 tar 파일이 아닌 layout 디렉터리를 사용한다.
4. 이 job의 일시적 `GITHUB_TOKEN`으로 로그인하고 **검사한 동일 OCI layout**을 digest 보존 옵션으로 GHCR에 복사한다. 개인 토큰이나 장기 AWS 키를 사용하지 않는다.
5. 반환 digest가 빌드 digest와 일치하는지 검사하고, registry에서 digest로 실제 `docker pull`한다. 네트워크를 차단한 컨테이너에서 해당 소스의 `check_runtime_image.py`로 앱 로딩·health handler·설치 도구 부재를 검사한다. 소스 revision label, 실행 UID 10001, OCI attestation manifest도 확인한다. 이 검사를 포함한 main 소스 SHA를 사용해야 한다.
6. source SHA/archive hash, digest, pull 결과, registry index, Trivy JSON을 Actions artifact로 남긴다. 이미지 아카이브와 Docker 인증 파일은 artifact에 넣지 않는다. 로그인 action은 종료 시 인증을 정리한다.

게시 주소는 `ghcr.io/softbank-hackathon-finals/demo-chat`이며 `sha-<source_sha>` 태그를 붙인다. 배포에는 태그가 아닌 **`ghcr.io/softbank-hackathon-finals/demo-chat@sha256:...`**를 사용한다. 같은 소스라도 base image나 builder가 달라진 재빌드의 digest는 달라질 수 있으므로, 실행 결과의 digest와 provenance를 보존한다.

BuildKit의 max provenance와 SBOM을 OCI attestation으로 함께 게시한다. 빌드 컨텍스트의 source SHA와 archive SHA는 `source-manifest.json`, 실제 사용한 base image와 빌드 정보는 OCI provenance로 대조한다. workflow를 실행한 커밋과 앱을 빌드한 `source_sha`는 서로 다를 수 있다. 빌드 인자에 자격증명을 넣지 않는다. [Docker provenance/SBOM 문서](https://docs.docker.com/build/ci/github-actions/attestations/).

## 최초 게시와 소비자 접근

최초 확인은 검토된 소스 SHA로 고정한 임시 브랜치 push 경로로 수행했다.
성공 증거를 확보한 뒤 이 임시 trigger와 기본 SHA를 제거했다. 이후에는
명시적인 SHA 입력 실행만 사용한다. PR 필수 CI나 보호 규칙은 변경하지 않는다.

GHCR package의 최초 기본 가시성은 별도 확인한다. runner의 인증된 pull 성공은 익명 pull이나 EKS/RKE2 pull 성공을 뜻하지 않는다. 공개 데모 소스에 맞춰 package를 public으로 둘지는 소유자가 설정하고, 익명 pull과 실제 소비 클러스터의 동일 digest pull을 각각 기록한다. package가 private인 동안 장기 PAT를 공용 imagePullSecret으로 복제해 해결하지 않는다.

게시된 `image-receipt.json`의 digest를 사용해 빈 임시 폴더로 익명 다운로드를
검증할 수 있다. `--src-no-creds`는 기존 로컬 registry 인증을 사용하지 않는다.

```sh
image='ghcr.io/softbank-hackathon-finals/demo-chat'
digest='sha256:<image-receipt.json의 64자리 digest>'
destination=$(mktemp -d)
skopeo copy --src-no-creds --all --preserve-digests \
  --digestfile "$destination/downloaded-digest.txt" \
  "docker://$image@$digest" "oci:$destination/image:verified"
test "$(cat "$destination/downloaded-digest.txt")" = "$digest"
```

이 다운로드는 로컬 검증이다. 실제 소비 클러스터에서 같은 digest를 받은
결과는 별도로 기록한다. 사용 후 정리는 이 명령으로 만든 임시 폴더에만 한정한다.

워크플로에는 앱 DB/Secrets Manager/S3 설정이나 클러스터 배포 단계가 없다. 앱의 실행 준비와 업무 연결은 T6/T30에서 같은 digest로 별도 검증한다. 이 이미지 게시 성공만으로 EKS/RKE2 또는 전체 T7 태스크를 완료 처리하지 않는다.

## 2026-11-03 확인 결과

[게시 실행 41606831578](https://github.com/Softbank-Hackathon-Finals/demo-chat/actions/runs/41606831578)은
main 소스 `ce5727eb3e3d2355077afdaadcb1a722aba9c7cd`로 성공했다. 식별값과
검사 결과는 `docs/evidence/image-publication-2026-11-03/`에 있다.
GitHub UI에서 이번 실행의 index와 두 하위 manifest, 연결된 source repository를
대조했다. 기존의 다른 버전은 관찰되지 않았다.

현재 package는 private이고 조직 관리자가 public/internal 전환을 비활성화한
상태다. 조직 정책은 변경하지 않았다. 인증된 runner pull은 성공했으나 익명
manifest 조회는 실패했으며, RKE2/EKS 이미지 pull 완료로 기록하지 않았다.
조직의 공개 게시 범위가 결정되면 같은 digest로 익명 다운로드와 클러스터
다운로드를 이어간다. 이 대기 상태는 `consumer-access.json`에 별도로 기록한다.

## 로컬 검사

```sh
uv sync --frozen
uv run pytest tests/test_image_source.py
uvx ruff check .
actionlint .github/workflows/publish-image.yml
```

추적 파일로만 만든 컨텍스트인지, main 밖의 커밋·추적된 환경 파일을 거부하는지 검사한다. 실제 게시·취약점·registry pull 결과는 해당 Actions 실행의 영수증으로 확인한다.
