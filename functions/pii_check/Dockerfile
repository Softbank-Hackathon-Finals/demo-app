# 함수 이미지: platform의 호환 런타임 베이스 위에 핸들러와 그 의존성만 얹는다(platform ADR-0045).
# AWS에서는 같은 pii_check.py를 Lambda(Python 3.12, 핸들러 pii_check.handler)로 배포한다.
ARG RUNTIME_IMAGE=t46-function-runtime:local
# hadolint ignore=DL3006
FROM ${RUNTIME_IMAGE}
USER root
RUN pip install --no-cache-dir "psycopg[binary]==3.2.*"
COPY pii_check.py /var/task/
USER 10001
