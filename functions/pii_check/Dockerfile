# 함수 이미지: platform의 호환 런타임 베이스(ghcr.io/softbank-hackathon-finals/platform-function-runtime) 위에
# 핸들러와 그 의존성만 얹는다(platform ADR-0045). 배포할 때는 RUNTIME_IMAGE에 베이스의 digest를 준다.
# AWS에서는 같은 pii_check.py를 Lambda(Python 3.12, 핸들러 pii_check.handler)로 배포한다.
ARG RUNTIME_IMAGE=t46-function-runtime:local
# hadolint ignore=DL3006
FROM ${RUNTIME_IMAGE}
USER root
# 핸들러 의존성은 Lambda zip처럼 핸들러 옆(/var/task)에 넣는다. 런타임 가상환경은 읽기 전용이다.
RUN pip install --no-cache-dir --target /var/task "psycopg[binary]==3.2.*"
COPY pii_check.py /var/task/
USER 10001
