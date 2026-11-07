"""표준 OIDC ID 토큰 확인: Cognito와 Keycloak을 같은 코드로, 발급자는 설정으로만 바꾼다."""
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from app.auth import (
    Issuer,
    OidcVerifier,
    ProviderUnavailable,
    TokenRejected,
    display_name,
)

COGNITO = Issuer("https://cognito-idp.ap-northeast-2.amazonaws.com/ap-northeast-2_demo", "cognito-client")
KEYCLOAK = Issuer("https://login.onprem.example/realms/bravo", "keycloak-client")


class FakeProvider:
    """발급자 하나를 흉내 낸다. 공개 키 문서와 서명 키를 들고 있다."""

    def __init__(self, issuer: Issuer, kid: str = "k1"):
        self.issuer = issuer
        self.kid = kid
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.discovery_issuer = issuer.url

    def jwks(self):
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [jwk | {"kid": self.kid, "alg": "RS256", "use": "sig"}]}

    def documents(self):
        base = self.issuer.url
        return {
            f"{base}/.well-known/openid-configuration": {
                "issuer": self.discovery_issuer,
                "authorization_endpoint": f"{base}/authorize",
                "token_endpoint": f"{base}/token",
                "jwks_uri": f"{base}/jwks",
            },
            f"{base}/jwks": self.jwks(),
        }

    def token(self, **claims):
        now = int(time.time())
        body = {
            "iss": self.issuer.url,
            "aud": self.issuer.client_id,
            "sub": "user-1",
            "iat": now,
            "exp": now + 3600,
            "cognito:username": "visitor01",
        } | claims
        return jwt.encode(body, self.key, algorithm="RS256", headers={"kid": self.kid})


class Fetcher:
    def __init__(self, *providers):
        self.providers = providers
        self.calls: list[str] = []
        self.down = False

    def __call__(self, url):
        self.calls.append(url)
        if self.down:
            raise ProviderUnavailable("unreachable")
        for provider in self.providers:
            documents = provider.documents()
            if url in documents:
                return documents[url]
        raise ProviderUnavailable(f"no document at {url}")


def test_cognito_id_token_is_accepted_and_named_from_its_username():
    cognito = FakeProvider(COGNITO)
    verifier = OidcVerifier(COGNITO, fetch=Fetcher(cognito))
    identity = verifier.verify(cognito.token())
    assert (identity.subject, identity.issuer, identity.name) == ("user-1", COGNITO.url, "visitor01")


def test_keycloak_token_uses_the_same_code_when_only_the_issuer_setting_changes():
    keycloak = FakeProvider(KEYCLOAK)
    verifier = OidcVerifier(KEYCLOAK, fetch=Fetcher(keycloak))
    token = keycloak.token(**{"cognito:username": None, "preferred_username": "visitor01"})
    assert verifier.verify(token).name == "visitor01"


def test_during_migration_the_target_also_trusts_the_source_issuer():
    cognito, keycloak = FakeProvider(COGNITO, "c1"), FakeProvider(KEYCLOAK, "k1")
    verifier = OidcVerifier(KEYCLOAK, also_trusted=(COGNITO,), fetch=Fetcher(cognito, keycloak))
    assert verifier.verify(cognito.token()).issuer == COGNITO.url
    assert verifier.verify(keycloak.token()).issuer == KEYCLOAK.url


def test_untrusted_issuer_is_rejected_without_fetching_its_keys():
    cognito, keycloak = FakeProvider(COGNITO), FakeProvider(KEYCLOAK)
    fetcher = Fetcher(cognito, keycloak)
    verifier = OidcVerifier(KEYCLOAK, fetch=fetcher)
    with pytest.raises(TokenRejected):
        verifier.verify(cognito.token())
    assert not any(COGNITO.url in url for url in fetcher.calls)


@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "other-client"},
        {"exp": int(time.time()) - 3600},
        {"token_use": "access"},
        {"sub": None},
    ],
)
def test_wrong_audience_expired_access_or_subjectless_tokens_are_rejected(claims):
    cognito = FakeProvider(COGNITO)
    verifier = OidcVerifier(COGNITO, fetch=Fetcher(cognito))
    body = {k: v for k, v in claims.items() if v is not None}
    token = cognito.token(**body)
    if claims.get("sub", "") is None:
        payload = jwt.decode(token, options={"verify_signature": False})
        payload.pop("sub")
        token = jwt.encode(payload, cognito.key, algorithm="RS256", headers={"kid": cognito.kid})
    with pytest.raises(TokenRejected):
        verifier.verify(token)


def test_token_signed_by_another_key_is_rejected():
    cognito, forger = FakeProvider(COGNITO), FakeProvider(COGNITO)
    verifier = OidcVerifier(COGNITO, fetch=Fetcher(cognito))
    with pytest.raises(TokenRejected):
        verifier.verify(forger.token())


def test_unsigned_or_hmac_tokens_are_rejected():
    cognito = FakeProvider(COGNITO)
    verifier = OidcVerifier(COGNITO, fetch=Fetcher(cognito))
    claims = jwt.decode(cognito.token(), options={"verify_signature": False})
    for token in (
        jwt.encode(claims, None, algorithm="none"),
        jwt.encode(claims, "shared-secret-that-is-long-enough-for-hs256", algorithm="HS256", headers={"kid": "k1"}),
    ):
        with pytest.raises(TokenRejected):
            verifier.verify(token)


def test_rotated_key_is_fetched_once_and_then_cached():
    cognito = FakeProvider(COGNITO, "old")
    fetcher = Fetcher(cognito)
    verifier = OidcVerifier(COGNITO, fetch=fetcher)
    verifier.verify(cognito.token())
    cognito.kid = "new"
    verifier.verify(cognito.token())
    verifier.verify(cognito.token())
    jwks_calls = [url for url in fetcher.calls if url.endswith("/jwks")]
    assert len(jwks_calls) == 2


def test_repeated_unknown_key_ids_do_not_flood_the_issuer():
    cognito = FakeProvider(COGNITO, "real")
    fetcher = Fetcher(cognito)
    verifier = OidcVerifier(COGNITO, fetch=fetcher)
    verifier.verify(cognito.token())
    forged = FakeProvider(COGNITO, "unknown")
    for _ in range(5):
        with pytest.raises(TokenRejected):
            verifier.verify(forged.token())
    assert len([url for url in fetcher.calls if url.endswith("/jwks")]) == 2


def test_discovery_for_another_issuer_is_refused():
    cognito = FakeProvider(COGNITO)
    cognito.discovery_issuer = "https://attacker.example"
    verifier = OidcVerifier(COGNITO, fetch=Fetcher(cognito))
    with pytest.raises(ProviderUnavailable):
        verifier.login_config()


def test_unreachable_provider_is_unavailable_not_a_rejection():
    cognito = FakeProvider(COGNITO)
    fetcher = Fetcher(cognito)
    fetcher.down = True
    verifier = OidcVerifier(COGNITO, fetch=fetcher)
    with pytest.raises(ProviderUnavailable):
        verifier.verify(cognito.token())


def test_login_config_points_the_browser_at_the_login_issuer():
    keycloak = FakeProvider(KEYCLOAK)
    config = OidcVerifier(KEYCLOAK, also_trusted=(COGNITO,), fetch=Fetcher(keycloak)).login_config()
    assert config == {
        "enabled": True,
        "issuer": KEYCLOAK.url,
        "client_id": KEYCLOAK.client_id,
        "authorization_endpoint": f"{KEYCLOAK.url}/authorize",
        "token_endpoint": f"{KEYCLOAK.url}/token",
        "scope": "openid profile email",
    }


def test_plain_http_issuer_is_refused_except_on_this_machine():
    with pytest.raises(ValueError):
        OidcVerifier(Issuer("http://login.example/realms/bravo", "c"))
    OidcVerifier(Issuer("http://localhost:8081/realms/bravo", "c"))


@pytest.mark.parametrize(
    "claims, expected",
    [
        ({"preferred_username": " kim ", "cognito:username": "x"}, "kim"),
        ({"cognito:username": "visitor01", "email": "v@example.com"}, "visitor01"),
        ({"email": "v@example.com"}, "v@example.com"),
        ({"sub": "abc"}, "abc"),
        ({"preferred_username": "가" * 50}, "가" * 40),
    ],
)
def test_display_name_order_and_length(claims, expected):
    assert display_name(claims) == expected
