"""로그인을 켠 앱: 쓰기는 ID 토큰이 있어야 하고, 읽기와 쓰기 차단 응답은 그대로다."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.auth import OidcVerifier
from app.config import Settings, parse_trust
from app.main import create_app
from tests.test_api import ADMIN, FakeStorage, FakeStore
from tests.test_auth import COGNITO, KEYCLOAK, FakeProvider, Fetcher


def settings(**overrides):
    return Settings(
        app_env="aws",
        db_host="unused",
        db_port=5432,
        db_name="unused",
        db_secret_id="unused",
        s3_bucket="unused",
        s3_endpoint_url=None,
        s3_force_path_style=False,
        admin_token="test-admin",
        poll_interval=0.05,
        auth_issuer=COGNITO.url,
        auth_client_id=COGNITO.client_id,
    ) if not overrides else Settings(**({
        "app_env": "aws", "db_host": "unused", "db_port": 5432, "db_name": "unused",
        "db_secret_id": "unused", "s3_bucket": "unused", "s3_endpoint_url": None,
        "s3_force_path_style": False, "admin_token": "test-admin", "poll_interval": 0.05,
    } | overrides))


@pytest.fixture
def cognito():
    return FakeProvider(COGNITO)


@pytest.fixture
def fetcher(cognito):
    return Fetcher(cognito)


@pytest.fixture
def client(fetcher):
    verifier = OidcVerifier(COGNITO, fetch=fetcher)
    with TestClient(create_app(settings(), FakeStore(), FakeStorage(), auth=verifier)) as c:
        yield c


def message(**overrides):
    return {"idempotency_key": str(uuid.uuid4()), "author": "visitor01", "body": "안녕하세요"} | overrides


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_signed_in_user_writes_under_the_name_in_the_token(client, cognito):
    response = client.post("/api/messages", json=message(), headers=bearer(cognito.token()))
    assert response.status_code == 201
    assert response.json()["author"] == "visitor01"


def test_writing_without_a_token_asks_to_sign_in(client):
    response = client.post("/api/messages", json=message())
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_writing_under_someone_elses_name_is_refused_not_rewritten(client, cognito):
    response = client.post("/api/messages", json=message(author="상담원"), headers=bearer(cognito.token()))
    assert response.status_code == 403
    assert client.get("/api/messages").json() == []


def test_forged_token_is_rejected(client):
    forger = FakeProvider(COGNITO)
    assert client.post("/api/messages", json=message(), headers=bearer(forger.token())).status_code == 401


def test_reads_stay_open_for_viewers(client, cognito):
    client.post("/api/messages", json=message(), headers=bearer(cognito.token()))
    assert len(client.get("/api/messages").json()) == 1


def test_write_block_answers_before_sign_in_is_checked(client):
    client.put("/admin/write-block", json={"blocked": True}, headers=ADMIN)
    response = client.post("/api/messages", json=message())
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "2"


def test_attachment_upload_needs_sign_in(client, cognito):
    attachment_id = uuid.uuid4()
    files = {"file": ("a.txt", b"hello", "text/plain")}
    assert client.put(f"/api/attachments/{attachment_id}", files=files).status_code == 401
    signed = client.put(f"/api/attachments/{attachment_id}", files=files, headers=bearer(cognito.token()))
    assert signed.status_code == 200


def test_unreachable_provider_is_a_retryable_503(client, cognito, fetcher):
    fetcher.down = True
    response = client.post("/api/messages", json=message(), headers=bearer(cognito.token()))
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "2"


def test_auth_config_points_the_browser_at_the_login_issuer(client):
    config = client.get("/api/auth/config").json()
    assert config["enabled"] is True
    assert config["client_id"] == COGNITO.client_id
    assert config["authorization_endpoint"] == f"{COGNITO.url}/authorize"


def test_admin_switch_keeps_its_own_token_when_sign_in_is_on(client):
    assert client.get("/admin/write-block", headers=ADMIN).status_code == 200


def test_target_app_accepts_source_tokens_during_migration():
    cognito, keycloak = FakeProvider(COGNITO, "c1"), FakeProvider(KEYCLOAK, "k1")
    verifier = OidcVerifier(KEYCLOAK, also_trusted=(COGNITO,), fetch=Fetcher(cognito, keycloak))
    app = create_app(
        settings(auth_issuer=KEYCLOAK.url, auth_client_id=KEYCLOAK.client_id,
                 auth_also_trust=((COGNITO.url, COGNITO.client_id),)),
        FakeStore(), FakeStorage(), auth=verifier,
    )
    with TestClient(app) as c:
        assert c.post("/api/messages", json=message(), headers=bearer(cognito.token())).status_code == 201
        keycloak_token = keycloak.token(**{"cognito:username": None, "preferred_username": "visitor01"})
        assert c.post("/api/messages", json=message(), headers=bearer(keycloak_token)).status_code == 201
        assert c.get("/api/auth/config").json()["issuer"] == KEYCLOAK.url


def test_without_auth_settings_the_app_behaves_as_before():
    with TestClient(create_app(settings(app_env="aws"), FakeStore(), FakeStorage())) as c:
        assert c.get("/api/auth/config").json() == {"enabled": False}
        assert c.post("/api/messages", json=message(author="상담원")).status_code == 201


def test_issuer_and_client_id_go_together():
    with pytest.raises(ValueError):
        settings(auth_issuer=COGNITO.url)
    with pytest.raises(ValueError):
        settings(auth_also_trust=((COGNITO.url, "c"),))


def test_also_trust_parsing():
    assert parse_trust("") == ()
    assert parse_trust(f"{COGNITO.url}|c1, {KEYCLOAK.url}|c2") == ((COGNITO.url, "c1"), (KEYCLOAK.url, "c2"))
    with pytest.raises(ValueError):
        parse_trust(COGNITO.url)
    # 세 번째 칸은 그 클라이언트로 받을 수 있는 유일한 이름이다(부하 생성기 계정).
    assert parse_trust(f"{COGNITO.url}|lg|loadgen") == ((COGNITO.url, "lg", "loadgen"),)
    for bad in (f"{COGNITO.url}|lg|", f"{COGNITO.url}|lg|a|b"):
        with pytest.raises(ValueError):
            parse_trust(bad)
