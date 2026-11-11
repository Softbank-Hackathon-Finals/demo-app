import os
from dataclasses import dataclass

from app.db_secret import DEFAULT_CREDENTIALS_FILE


@dataclass(frozen=True)
class Settings:
    app_env: str
    db_host: str
    db_port: int
    db_name: str
    # Legacy attribute name retained for callers; now an injected JSON file path.
    db_secret_id: str
    s3_bucket: str
    s3_endpoint_url: str | None
    s3_force_path_style: bool
    admin_token: str | None
    poll_interval: float = 1.0
    tx_timeout_ms: int = 200
    retry_after: int = 2
    db_wait_seconds: float = 5
    max_attachment_bytes: int = 10 * 1024 * 1024
    attachment_upload_enabled: bool = True
    # 로그인(OIDC). 비우면 로그인 없이 지금처럼 동작한다. Cognito든 Keycloak이든 주소만 다르다.
    auth_issuer: str | None = None
    auth_client_id: str | None = None
    # 서명만 믿는 다른 발급자 (주소, 클라이언트 ID). 이사 중 출발지에서 받은 토큰을 받기 위해 쓴다.
    auth_also_trust: tuple[tuple[str, str], ...] = ()

    def __post_init__(self):
        if type(self.attachment_upload_enabled) is not bool:
            raise ValueError("attachment_upload_enabled must be a boolean")
        if (self.auth_issuer is None) != (self.auth_client_id is None):
            raise ValueError("AUTH_ISSUER and AUTH_CLIENT_ID must be set together")
        if self.auth_also_trust and self.auth_issuer is None:
            raise ValueError("AUTH_ALSO_TRUST needs AUTH_ISSUER")

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        upload_enabled = env.get("ATTACHMENT_UPLOAD_ENABLED", "true")
        if upload_enabled not in {"true", "false"}:
            raise ValueError("ATTACHMENT_UPLOAD_ENABLED must be true or false")
        return cls(
            app_env=env.get("APP_ENV", "local"),
            db_host=env["DB_HOST"],
            db_port=int(env.get("DB_PORT", "5432")),
            db_name=env.get("DB_NAME", "chat"),
            db_secret_id=env.get("DB_CREDENTIALS_FILE", DEFAULT_CREDENTIALS_FILE),
            s3_bucket=env["S3_BUCKET"],
            s3_endpoint_url=env.get("S3_ENDPOINT_URL") or None,
            s3_force_path_style=env.get("S3_FORCE_PATH_STYLE", "false") == "true",
            admin_token=env.get("ADMIN_TOKEN") or None,
            poll_interval=float(env.get("POLL_INTERVAL", "1.0")),
            tx_timeout_ms=int(env.get("TX_TIMEOUT_MS", "200")),
            retry_after=int(env.get("WRITE_BLOCK_RETRY_AFTER", "2")),
            db_wait_seconds=float(env.get("DB_WAIT_SECONDS", "5")),
            attachment_upload_enabled=upload_enabled == "true",
            auth_issuer=env.get("AUTH_ISSUER") or None,
            auth_client_id=env.get("AUTH_CLIENT_ID") or None,
            auth_also_trust=parse_trust(env.get("AUTH_ALSO_TRUST", "")),
        )


def parse_trust(value: str) -> tuple[tuple[str, str], ...]:
    """`발급자주소|클라이언트ID`를 쉼표로 잇는다. 예: `https://a|c1,https://b|c2`."""
    pairs = []
    for entry in filter(None, (part.strip() for part in value.split(","))):
        url, sep, client_id = entry.partition("|")
        if not sep or not url or not client_id:
            raise ValueError("AUTH_ALSO_TRUST entries are issuer|client_id")
        pairs.append((url, client_id))
    return tuple(pairs)
