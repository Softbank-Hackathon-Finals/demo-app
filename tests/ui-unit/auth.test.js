import { test } from 'node:test';
import assert from 'node:assert/strict';
import { decodeClaims, displayName, finishLogin, pkcePair, session, signOut, startLogin } from '../../app/static/auth.js';

const config = {
  enabled: true, issuer: 'https://cognito-idp.ap-northeast-2.amazonaws.com/pool', client_id: 'client-1',
  authorization_endpoint: 'https://login.example/oauth2/authorize', token_endpoint: 'https://login.example/oauth2/token',
  scope: 'openid profile email',
};
const location = (href = 'https://chat.example/') => ({ href, origin: new URL(href).origin });

class Storage {
  constructor() { this.items = new Map(); }
  getItem(key) { return this.items.has(key) ? this.items.get(key) : null; }
  setItem(key, value) { this.items.set(key, String(value)); }
  removeItem(key) { this.items.delete(key); }
}

const b64 = (value) => Buffer.from(JSON.stringify(value)).toString('base64url');
const idToken = (claims) => `${b64({ alg: 'RS256', kid: 'k' })}.${b64(claims)}.signature`;
const claims = (extra = {}) => ({ iss: config.issuer, aud: 'client-1', sub: 'u1', exp: 2_000_000_000, 'cognito:username': 'visitor01', ...extra });

test('display name uses the same claim order and 40 code point limit as the server', () => {
  assert.equal(displayName({ preferred_username: ' kim ', 'cognito:username': 'x' }), 'kim');
  assert.equal(displayName({ 'cognito:username': 'visitor01', email: 'v@example.com' }), 'visitor01');
  assert.equal(displayName({ email: 'v@example.com' }), 'v@example.com');
  assert.equal(displayName({ sub: 'abc' }), 'abc');
  assert.equal(displayName({ preferred_username: '가'.repeat(50) }), '가'.repeat(40));
  assert.throws(() => displayName({}));
});

test('claims decode from base64url including Korean names', () => {
  assert.equal(decodeClaims(idToken(claims({ name: '김태현' }))).name, '김태현');
});

test('pkce challenge is the S256 of the verifier', async () => {
  const { verifier, challenge } = await pkcePair((bytes) => bytes.fill(7));
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  assert.equal(challenge, Buffer.from(digest).toString('base64url'));
});

test('login sends the browser to the issuer with PKCE and remembers only this tab', async () => {
  const storage = new Storage();
  const url = new URL(await startLogin(config, { storage, location: location() }));
  assert.equal(`${url.origin}${url.pathname}`, config.authorization_endpoint);
  assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
  assert.equal(url.searchParams.get('redirect_uri'), 'https://chat.example/');
  assert.equal(url.searchParams.get('client_id'), 'client-1');
  const pending = JSON.parse(storage.getItem('bravo-oidc-pending'));
  assert.equal(pending.state, url.searchParams.get('state'));
});

test('returning from the issuer exchanges the code and keeps the id token', async () => {
  const storage = new Storage();
  const login = new URL(await startLogin(config, { storage, location: location() }));
  const pending = JSON.parse(storage.getItem('bravo-oidc-pending'));
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push([url, Object.fromEntries(new URLSearchParams(init.body))]);
    return { ok: true, json: async () => ({ id_token: idToken(claims()) }) };
  };
  const back = location(`https://chat.example/?code=abc&state=${login.searchParams.get('state')}`);
  const result = await finishLogin({ storage, location: back, fetchImpl, now: () => 0 });
  assert.equal(result.cleanUrl, '/');
  assert.equal(result.session.name, 'visitor01');
  assert.equal(calls[0][0], config.token_endpoint);
  assert.equal(calls[0][1].code_verifier, pending.verifier);
  assert.equal(calls[0][1].grant_type, 'authorization_code');
  assert.equal(storage.getItem('bravo-oidc-pending'), null);
  assert.equal(session(storage, () => 0).name, 'visitor01');
});

test('a forged state, another app token or an issuer error never signs in', async () => {
  const ok = async () => ({ ok: true, json: async () => ({ id_token: idToken(claims({ aud: 'other' })) }) });
  for (const [href, fetchImpl] of [
    ['https://chat.example/?code=abc&state=forged', ok],
    ['https://chat.example/?error=access_denied&state=x', ok],
  ]) {
    const storage = new Storage();
    await startLogin(config, { storage, location: location() });
    await assert.rejects(finishLogin({ storage, location: location(href), fetchImpl }));
    assert.equal(session(storage), null);
  }
  const storage = new Storage();
  const login = new URL(await startLogin(config, { storage, location: location() }));
  const back = location(`https://chat.example/?code=abc&state=${login.searchParams.get('state')}`);
  await assert.rejects(finishLogin({ storage, location: back, fetchImpl: ok }), /token_for_another_app/);
});

test('a login finishes with the issuer it started at, whichever side serves the return page', async () => {
  // 10·50% 동안 요청이 출발지·목적지로 섞인다. 돌아온 페이지가 어느 쪽 설정을 받든,
  // 코드는 로그인을 시작할 때 남긴 토큰 주소에서 그때의 클라이언트로 바꾼다.
  const storage = new Storage();
  const login = new URL(await startLogin(config, { storage, location: location() }));
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push([url, Object.fromEntries(new URLSearchParams(init.body))]);
    return { ok: true, json: async () => ({ id_token: idToken(claims()) }) };
  };
  const back = location(`https://chat.example/?code=abc&state=${login.searchParams.get('state')}`);
  const result = await finishLogin({ storage, location: back, fetchImpl, now: () => 0 });
  assert.equal(result.session.name, 'visitor01');
  assert.equal(calls[0][0], config.token_endpoint);
  assert.equal(calls[0][1].client_id, config.client_id);
});

test('a login without a remembered token address is not finished against the current page', async () => {
  // 고치기 전 버전이 남긴 기록에는 토큰 주소가 없다. 어느 쪽 주소로 바꿀지 모르면 다시 로그인하게 한다.
  const storage = new Storage();
  storage.setItem('bravo-oidc-pending', JSON.stringify({ state: 's', verifier: 'v', issuer: config.issuer, redirect: 'https://chat.example/' }));
  const fetchImpl = async () => { throw new Error('must not call'); };
  await assert.rejects(finishLogin({ storage, location: location('https://chat.example/?code=abc&state=s'), fetchImpl }), /login_state_mismatch/);
});

test('a normal page load is not treated as a login return', async () => {
  assert.equal(await finishLogin({ storage: new Storage(), location: location('https://chat.example/?x=1') }), null);
});

test('an expiring session is treated as signed out', () => {
  const storage = new Storage();
  storage.setItem('bravo-oidc-session', JSON.stringify({ token: 't', name: 'v', exp: 60000 }));
  assert.equal(session(storage, () => 0).name, 'v');
  assert.equal(session(storage, () => 40000), null);
  signOut(storage);
  assert.equal(session(storage, () => 0), null);
});
