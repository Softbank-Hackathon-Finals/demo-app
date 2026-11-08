// 표준 OIDC 로그인(Authorization Code + PKCE, 비밀 없는 공개 클라이언트).
// Cognito와 Keycloak이 같은 코드다. 어느 발급자로 갈지는 서버의 /api/auth/config가 알려 준다.
// 토큰 서명 확인은 서버가 한다. 브라우저는 이름을 보여 주고 요청에 토큰을 붙이기만 한다.

const SESSION = 'bravo-oidc-session';
const PENDING = 'bravo-oidc-pending';
const NAME_CLAIMS = ['preferred_username', 'cognito:username', 'email', 'name', 'sub'];
const MAX_NAME = 40;
const EXPIRY_MARGIN_MS = 30000;

// 서버 app/auth.py의 display_name과 같은 순서 · 같은 길이 규칙이다. 다르면 작성자 확인(403)에 걸린다.
export function displayName(claims) {
  for (const claim of NAME_CLAIMS) {
    const value = claims?.[claim];
    if (typeof value === 'string' && value.trim()) return [...value.trim()].slice(0, MAX_NAME).join('');
  }
  throw new Error('token_without_name');
}

function base64url(bytes) {
  let text = '';
  for (const byte of bytes) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function decodeClaims(idToken) {
  const part = String(idToken).split('.')[1];
  if (!part) throw new Error('invalid_id_token');
  const padded = part.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - part.length % 4) % 4);
  const bytes = Uint8Array.from(atob(padded), (c) => c.charCodeAt(0));
  return JSON.parse(new TextDecoder().decode(bytes));
}

export async function pkcePair(random = (bytes) => crypto.getRandomValues(bytes)) {
  const verifier = base64url(random(new Uint8Array(32)));
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return { verifier, challenge: base64url(new Uint8Array(digest)) };
}

export function redirectUri(location) { return `${location.origin}/`; }

// 로그인 창 주소를 만들고, 돌아왔을 때 맞춰 볼 state와 verifier를 이 탭에만 남긴다.
// 코드를 바꿀 발급자 · 클라이언트 · 토큰 주소도 함께 남긴다. 이사 중에는 돌아온 페이지가 다른 쪽 앱에서
// 올 수 있어서, 그때 받은 설정이 아니라 로그인을 시작한 쪽으로 마친다.
export async function startLogin(config, { storage, location, random }) {
  const { verifier, challenge } = await pkcePair(random);
  const state = base64url((random ?? ((b) => crypto.getRandomValues(b)))(new Uint8Array(16)));
  storage.setItem(PENDING, JSON.stringify({
    state, verifier, issuer: config.issuer, client_id: config.client_id,
    token_endpoint: config.token_endpoint, redirect: redirectUri(location),
  }));
  const url = new URL(config.authorization_endpoint);
  url.search = new URLSearchParams({
    response_type: 'code', client_id: config.client_id, redirect_uri: redirectUri(location),
    scope: config.scope, state, code_challenge: challenge, code_challenge_method: 'S256',
  }).toString();
  return url.toString();
}

// 로그인 창에서 돌아온 주소(?code=&state=)를 처리한다. 돌아온 게 아니면 아무것도 하지 않는다.
export async function finishLogin({ storage, location, fetchImpl = fetch, now = Date.now }) {
  const url = new URL(location.href);
  const code = url.searchParams.get('code'), state = url.searchParams.get('state');
  const error = url.searchParams.get('error');
  if (!code && !error) return null;
  for (const key of ['code', 'state', 'error', 'error_description', 'session_state', 'iss']) url.searchParams.delete(key);
  const cleanUrl = `${url.pathname}${url.search}${url.hash}`;
  const pending = JSON.parse(storage.getItem(PENDING) || 'null');
  storage.removeItem(PENDING);
  if (error) throw Object.assign(new Error('login_refused'), { cleanUrl });
  if (!pending || pending.state !== state || typeof pending.token_endpoint !== 'string') {
    throw Object.assign(new Error('login_state_mismatch'), { cleanUrl });
  }
  const response = await fetchImpl(pending.token_endpoint, {
    method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({
      grant_type: 'authorization_code', client_id: pending.client_id, code,
      redirect_uri: pending.redirect, code_verifier: pending.verifier,
    }).toString(),
  });
  if (!response.ok) throw Object.assign(new Error('token_exchange_failed'), { cleanUrl });
  const { id_token: token } = await response.json();
  const claims = decodeClaims(token);
  const audience = Array.isArray(claims.aud) ? claims.aud : [claims.aud];
  if (claims.iss !== pending.issuer || !audience.includes(pending.client_id)) {
    throw Object.assign(new Error('token_for_another_app'), { cleanUrl });
  }
  const current = { token, name: displayName(claims), exp: Number(claims.exp) * 1000 };
  if (!(current.exp > now())) throw Object.assign(new Error('token_expired'), { cleanUrl });
  storage.setItem(SESSION, JSON.stringify(current));
  return { session: current, cleanUrl };
}

// 만료가 가까운 토큰은 없는 것으로 본다. 서버도 같은 토큰을 401로 돌려보낸다.
export function session(storage, now = Date.now) {
  let current = null;
  try { current = JSON.parse(storage.getItem(SESSION) || 'null'); } catch { current = null; }
  if (!current || typeof current.token !== 'string' || !(current.exp - EXPIRY_MARGIN_MS > now())) return null;
  return current;
}

export function signOut(storage) { storage.removeItem(SESSION); storage.removeItem(PENDING); }
