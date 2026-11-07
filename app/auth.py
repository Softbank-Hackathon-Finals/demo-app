"""표준 OIDC ID 토큰 확인. Cognito와 Keycloak을 같은 코드로 다룬다.

앱이 아는 것은 발급자 주소와 클라이언트 ID뿐이다. 그래서 Cognito에서 다른 발급자로 옮기는 일은
"설정 교체"다. 코드는 바꾸지 않는다.

이사 중에는 두 환경의 앱이 같은 사용자를 받는다. 출발지에서 로그인한 사람의 토큰을 목적지 앱도
받아야 하므로, 브라우저를 보내는 발급자(로그인 발급자) 하나와 서명만 믿는 발급자를 따로 둔다.
"""
import json
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import jwt

NAME_CLAIMS = ("preferred_username", "cognito:username", "email", "name", "sub")
MAX_NAME = 40  # 메시지 작성자 칸의 길이
SCOPE = "openid profile email"
CACHE_SECONDS = 600
REFRESH_GAP_SECONDS = 30  # 모르는 kid가 계속 와도 공개 키 문서를 이 간격보다 자주 받지 않는다
LOCAL_HOSTS = {"localhost", "127.0.0.1", "[::1]"}


class TokenRejected(Exception):
    """토큰이 이 앱의 사용자를 증명하지 못한다. 다시 로그인해야 한다."""


class ProviderUnavailable(Exception):
    """발급자 문서나 공개 키를 지금 확인할 수 없다. 잠시 뒤 다시 시도할 일이다."""


@dataclass(frozen=True)
class Issuer:
    url: str
    client_id: str

    def __post_init__(self):
        parts = urlsplit(self.url)
        local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
        if parts.scheme != "https" and not (parts.scheme == "http" and local):
            raise ValueError("issuer must use https")
        if not self.client_id:
            raise ValueError("client_id is required")


@dataclass(frozen=True)
class Identity:
    subject: str
    issuer: str
    name: str


def display_name(claims: dict) -> str:
    """화면과 메시지에 쓰는 이름. 브라우저(auth.js)도 같은 순서로 고른다."""
    for claim in NAME_CLAIMS:
        value = claims.get(claim)
        if isinstance(value, str) and value.strip():
            return "".join(list(value.strip())[:MAX_NAME])
    raise TokenRejected("token has no usable name")


def fetch_json(url: str) -> dict:
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310 - https 발급자만 받는다
            document = json.loads(response.read(1_000_000))
    except (OSError, ValueError) as error:
        raise ProviderUnavailable(f"{url}: {type(error).__name__}") from error
    if not isinstance(document, dict):
        raise ProviderUnavailable(f"{url}: not an object")
    return document


class OidcVerifier:
    def __init__(
        self,
        login: Issuer,
        also_trusted: tuple[Issuer, ...] = (),
        fetch: Callable[[str], dict] = fetch_json,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.login = login
        # 발급자마다 믿는 클라이언트 ID 목록. 같은 발급자의 브라우저 클라이언트와 부하 생성기 클라이언트처럼
        # 하나의 발급자를 여러 클라이언트로 믿을 수 있어야 한다. 뒤의 것이 앞의 것을 지우지 않게 모은다.
        self._audiences: dict[str, tuple[str, ...]] = {}
        for issuer in (login, *also_trusted):
            known = self._audiences.get(issuer.url, ())
            if issuer.client_id not in known:
                self._audiences[issuer.url] = (*known, issuer.client_id)
        self._fetch = fetch
        self._clock = clock
        self._lock = threading.Lock()
        self._discovery: dict[str, tuple[float, dict]] = {}
        self._keys: dict[str, tuple[float, dict[str, jwt.PyJWK]]] = {}
        self._refreshed_at: dict[str, float] = {}

    def login_config(self) -> dict:
        document = self._discover(self.login.url)
        return {
            "enabled": True,
            "issuer": self.login.url,
            "client_id": self.login.client_id,
            "authorization_endpoint": document["authorization_endpoint"],
            "token_endpoint": document["token_endpoint"],
            "scope": SCOPE,
        }

    def verify(self, token: str) -> Identity:
        try:
            header = jwt.get_unverified_header(token)
            unverified = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError as error:
            raise TokenRejected("malformed token") from error
        issuer_url = unverified.get("iss")
        audiences = self._audiences.get(issuer_url) if isinstance(issuer_url, str) else None
        if audiences is None:
            raise TokenRejected("untrusted issuer")
        if header.get("alg") != "RS256":
            raise TokenRejected("unsupported algorithm")
        key = self._key(issuer_url, header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key.key,
                algorithms=["RS256"],
                audience=list(audiences),
                issuer=issuer_url,
                leeway=60,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as error:
            raise TokenRejected(type(error).__name__) from error
        # Cognito 액세스 토큰은 aud가 없고 token_use=access다. 사용자 신원은 ID 토큰으로만 받는다.
        if claims.get("token_use", "id") != "id":
            raise TokenRejected("not an id token")
        return Identity(str(claims["sub"]), issuer_url, display_name(claims))

    def _discover(self, issuer_url: str) -> dict:
        with self._lock:
            cached = self._discovery.get(issuer_url)
            if cached and self._clock() - cached[0] < CACHE_SECONDS:
                return cached[1]
        document = self._fetch(f"{issuer_url}/.well-known/openid-configuration")
        if document.get("issuer") != issuer_url:
            raise ProviderUnavailable("discovery document names another issuer")
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not isinstance(document.get(field), str):
                raise ProviderUnavailable(f"discovery document has no {field}")
        with self._lock:
            self._discovery[issuer_url] = (self._clock(), document)
        return document

    def _key(self, issuer_url: str, kid: str | None) -> jwt.PyJWK:
        if not kid:
            raise TokenRejected("token has no key id")
        with self._lock:
            cached = self._keys.get(issuer_url)
        now = self._clock()
        fresh = cached is not None and now - cached[0] < CACHE_SECONDS
        if fresh and kid in cached[1]:
            return cached[1][kid]
        if fresh:
            # 키 교체 직후의 새 kid는 한 번 다시 받아 본다. 모르는 kid를 반복해 보내도
            # 발급자에 요청이 쏟아지지 않게 그 재요청은 간격을 둔다.
            with self._lock:
                last = self._refreshed_at.get(issuer_url)
                if last is not None and now - last < REFRESH_GAP_SECONDS:
                    raise TokenRejected("unknown key id")
                self._refreshed_at[issuer_url] = now
        keys = self._load_keys(issuer_url)
        with self._lock:
            self._keys[issuer_url] = (now, keys)
        if kid not in keys:
            raise TokenRejected("unknown key id")
        return keys[kid]

    def _load_keys(self, issuer_url: str) -> dict[str, jwt.PyJWK]:
        document = self._fetch(self._discover(issuer_url)["jwks_uri"])
        keys = {}
        for entry in document.get("keys", []):
            if not isinstance(entry, dict) or entry.get("kty") != "RSA" or not entry.get("kid"):
                continue
            if entry.get("use", "sig") != "sig":
                continue
            try:
                keys[entry["kid"]] = jwt.PyJWK(entry, algorithm="RS256")
            except jwt.PyJWTError:
                continue
        if not keys:
            raise ProviderUnavailable("issuer publishes no RSA signing key")
        return keys
