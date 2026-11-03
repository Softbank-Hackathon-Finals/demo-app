# Runtime image packaging

The image installs the frozen application dependencies in a build stage and
copies the resulting virtual environment into the runtime stage. Both stages
use the same pinned Python image so the copied interpreter links remain valid.
The service runs as UID 10001 with the existing Uvicorn command.

`uv`, `pip`, and `ensurepip` are not runtime requirements. The runtime stage
does not copy `uv`, and removes the base image's `pip` installation and
`ensurepip` recovery wheel. Dependency changes require rebuilding the image.
Application dependencies, including urllib3 2.8.0, remain frozen in `uv.lock`.

## Reason for the change

The initial publication of source `4a24a1f798c31dafd8f35866aa42ffc5878648cd`
stopped before registry authentication or upload. Its image scan found five
HIGH vulnerabilities and zero secret matches in installer-vendored packages:

| Package in base pip | Version | Findings |
| --- | --- | --- |
| msgpack | 1.1.2 | CVE-2026-114613, GHSA-6v7p-g79w-8964 |
| setuptools | 70.3.0 | CVE-2025-47273 |
| urllib3 | 2.7.0 | CVE-2026-97687, CVE-2026-97689 |

The [failed scan run](https://github.com/Softbank-Hackathon-Finals/demo-chat/actions/runs/41605060069)
contains bounded package findings. Inspecting that run's exact Python base
image independently confirmed all three versions in
`/usr/local/lib/python3.13/site-packages/pip/_vendor/vendor.txt`, plus the
bundled `ensurepip/_bundled/pip-26.2.2-py3-none-any.whl` recovery copy.
The application lock already uses urllib3 2.8.0. Removing the unused installers
addresses their runtime presence without a scanner exception or application
dependency change.

## Verify the final image

Build from a clean tracked checkout, then run the probe through the final
image's interpreter:

```sh
docker build --platform linux/amd64 -t demo-chat:check .
docker run --rm --network none -i --platform linux/amd64 demo-chat:check \
  .venv/bin/python - < scripts/check_runtime_image.py
trivy image --scanners vuln,secret --severity HIGH,CRITICAL --ignore-unfixed \
  --exit-code 1 demo-chat:check
```

The probe checks UID 10001, native PostgreSQL driver loading, app imports,
static assets, lifespan and health handlers with stub external dependencies,
and absence of installers. It performs no AWS, database or object-store calls.
It is a packaging check; deployment still requires real service integration
and cluster pull verification. The publication workflow separately scans the
exact artifact before pushing and preserves its digest.

## References

- [Docker multi-stage builds](https://docs.docker.com/build/building/multi-stage/)
  describes copying selected build outputs into a final stage.
- [uv Docker integration](https://docs.astral.sh/uv/guides/integration/docker/)
  documents installing project environments and using uv during a build.
- [Python ensurepip](https://docs.python.org/3/library/ensurepip.html)
  describes the bundled installer used to bootstrap pip without network access.
