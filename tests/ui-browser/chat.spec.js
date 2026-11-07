import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import jsQR from 'jsqr';
import { expectObservedClose, observeSocketClosures } from './socket-observation.js';

const admin = { Authorization: 'Bearer browser-test-only' };
const message = (body) => ({ author: '다른 참여자', body, idempotency_key: randomUUID() });
test.beforeEach(async ({ page, request }) => {
  await observeSocketClosures(page);
  await request.post('/__test__/reset');
  await page.goto('/');
  await expect(page.locator('#conn')).toHaveText('연결됨');
  await page.locator('#name').fill('방문자 07');
});

test('real HTTP writes and shared WebSocket reads survive a test route switch', async ({ page, request }) => {
  await page.locator('#body').fill('이전 전 메시지'); await page.locator('#send').click();
  await expect(page.locator('#log .msg')).toHaveCount(1);
  await expect(page.locator('#outbox')).toBeHidden();
  await request.post('/__test__/switch/onprem');
  await expect(page.locator('#env')).toHaveText('온프렘');
  await expect(page.locator('#conn')).toHaveText('연결됨');
  await request.post('/api/messages', { data: message('목적지에서 보낸 메시지') });
  await expect(page.locator('#log .msg')).toHaveCount(2);
  await expect(page.locator('#log')).toContainText('이전 전 메시지');
  await page.locator('#body').fill('이전 후 메시지'); await page.locator('#send').click();
  await expect(page.locator('#log .msg')).toHaveCount(3);
});

test('write pause retains the same request until acknowledged exactly once', async ({ page, request }) => {
  await request.put('/admin/write-block', { headers: admin, data: { blocked: true } });
  await expect(page.locator('#banner')).toContainText('쓰기가 잠시 제한');
  const bodies = [];
  page.on('request', (req) => { if (req.url().endsWith('/api/messages') && req.method() === 'POST') bodies.push(req.postData()); });
  await page.locator('#body').fill('대기 후 한 번만 저장'); await page.locator('#send').click();
  await expect(page.locator('#outbox')).toContainText('같은 요청으로 재시도');
  await request.put('/admin/write-block', { headers: admin, data: { blocked: false } });
  await expect(page.locator('#outbox')).toBeHidden({ timeout: 8000 });
  await expect(page.locator('#log .msg')).toHaveCount(1);
  expect(bodies.length).toBeGreaterThanOrEqual(2); expect(new Set(bodies).size).toBe(1);
  expect((await (await request.get('/api/messages')).json()).length).toBe(1);
});

test('queued write and missed messages survive pause plus same-address switch together', async ({ page, request }) => {
  await page.locator('#body').fill('전환 전 저장'); await page.locator('#send').click();
  await expect(page.locator('#log .msg')).toHaveCount(1);
  await expect(page.locator('#outbox')).toBeHidden();
  const originalAddress = page.url(), payloads = [], sockets = [];
  page.on('request', (req) => {
    if (new URL(req.url()).pathname === '/api/messages' && req.method() === 'POST') payloads.push(req.postData());
  });
  page.on('websocket', (ws) => sockets.push(ws.url()));
  expect((await request.put('/admin/write-block', { headers: admin, data: { blocked: true } })).ok()).toBe(true);
  const rejected = page.waitForResponse((reply) =>
    new URL(reply.url()).pathname === '/api/messages' && reply.request().method() === 'POST' && reply.status() === 503);
  await page.locator('#body').fill('전환 중 대기'); await page.locator('#send').click();
  await rejected;
  await expect(page.locator('#outbox')).toContainText('같은 요청으로 재시도');
  expect((await (await request.get('/api/messages')).json()).length).toBe(1);

  expect((await request.post('/__test__/switch/onprem')).ok()).toBe(true);
  const remote = await request.post('/api/messages', { data: message('전환 중 다른 참가자') });
  expect(remote.status()).toBe(201);
  await expect(page.locator('#env')).toHaveText('온프렘');
  await expect(page.locator('#conn')).toHaveText('연결됨');
  await expect(page.locator('#outbox')).toBeHidden({ timeout: 8000 });
  await expect(page.locator('#log .msg')).toHaveCount(3);
  for (const text of ['전환 전 저장', '전환 중 대기', '전환 중 다른 참가자']) {
    await expect(page.locator('#log')).toContainText(text);
  }
  expect(page.url()).toBe(originalAddress);
  expect(payloads.length).toBeGreaterThanOrEqual(2);
  expect(new Set(payloads).size).toBe(1);
  expect(sockets.some((url) => new URL(url).searchParams.get('after') === '1')).toBe(true);
  const rows = await (await request.get('/api/messages')).json();
  expect(rows.length).toBe(3);
  expect(rows.filter((row) => row.body === '전환 중 대기').length).toBe(1);
  expect(new Set(rows.map((row) => row.id)).size).toBe(3);
});

test('lost POST response retries the original key without duplicate storage', async ({ page, request }) => {
  let first = true; const bodies = [];
  await page.route('**/api/messages', async (route) => {
    if (route.request().method() !== 'POST') return route.continue();
    bodies.push(route.request().postData());
    if (first) { first = false; await route.fetch(); await route.abort('failed'); }
    else await route.continue();
  });
  await page.locator('#body').fill('응답만 유실된 메시지'); await page.locator('#send').click();
  await expect(page.locator('#outbox')).toBeHidden({ timeout: 8000 });
  await expect(page.locator('#log .msg')).toHaveCount(1);
  expect(new Set(bodies).size).toBe(1); expect(bodies.length).toBeGreaterThanOrEqual(2);
  expect((await (await request.get('/api/messages')).json()).length).toBe(1);
});

test('lost upload acknowledgement keeps one attachment id and then sends the message', async ({ page, request }) => {
  const addresses = [];
  page.on('request', (req) => {
    if (new URL(req.url()).pathname.startsWith('/api/attachments/') && req.method() === 'PUT') addresses.push(req.url());
  });
  expect((await request.post('/__test__/lose-next-upload-ack')).ok()).toBe(true);
  await page.locator('#file').setInputFiles({ name: 'sample.txt', mimeType: 'text/plain', buffer: Buffer.from('synthetic attachment') });
  await page.locator('#body').fill('첨부 응답 재확인'); await page.locator('#send').click();
  await expect(page.locator('#outbox')).toBeHidden({ timeout: 8000 });
  await expect(page.locator('#log .msg')).toHaveCount(1);
  expect(addresses.length).toBeGreaterThanOrEqual(2); expect(new Set(addresses).size).toBe(1);
  const rows = await (await request.get('/api/messages')).json();
  expect(rows.length).toBe(1);
  expect(new URL(addresses[0]).pathname).toBe(`/api/attachments/${rows[0].attachment_id}`);
  const attachment = await request.get(`/api/attachments/${rows[0].attachment_id}`);
  expect(attachment.ok()).toBe(true); expect(await attachment.text()).toBe('synthetic attachment');
  await expect(page.locator('#log a')).toHaveText('첨부 · sample.txt');
});

test('reconnect asks after the last streamed id and retrieves messages sent while offline', async ({ page, context, request }) => {
  await request.post('/api/messages', { data: message('온라인 메시지') });
  await expect(page.locator('#log .msg')).toHaveCount(1);
  const addresses = []; page.on('websocket', (ws) => addresses.push(ws.url()));
  await context.setOffline(true);
  await expect(page.locator('#conn')).not.toHaveText('연결됨');
  await request.post('/api/messages', { data: message('오프라인 동안의 메시지') });
  await context.setOffline(false);
  await expect(page.locator('#log .msg')).toHaveCount(2);
  expect(addresses.some((url) => new URL(url).searchParams.get('after') === '1')).toBe(true);
});

test('mobile layout, local preview and sharing omit query tokens', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/?token=do-not-share#private');
  await expect(page.locator('#send')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.locator('#file').setInputFiles({ name: 'sample.png', mimeType: 'image/png', buffer: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=', 'base64') });
  await expect(page.locator('#file-preview img')).toBeVisible();
  await page.locator('#file-preview button').click();
  await expect(page.locator('#file-preview')).toBeHidden();
  await page.locator('#share-mobile').click();
  const sharedAddress = `${new URL(page.url()).origin}/`;
  await expect(page.locator('#share-url')).toHaveValue(sharedAddress);
  await expect(page.locator('#qr')).toBeVisible();
  const pixels = await page.locator('#qr').evaluate((canvas) => ({
    width: canvas.width, height: canvas.height,
    data: [...canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data],
  }));
  expect(jsQR(Uint8ClampedArray.from(pixels.data), pixels.width, pixels.height)?.data).toBe(sharedAddress);
  await expect(page.locator('#share-note')).toContainText('현재 PC 전용');
});

test('message markup is text, and a rejected send stays visible for correction', async ({ page, request }) => {
  await request.post('/api/messages', { data: message('<img src=x onerror="window.injected=true">') });
  await expect(page.locator('#log')).toContainText('<img src=x');
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
  await expect(page.locator('#log img')).toHaveCount(0);
  await page.route('**/api/messages', (route) => route.fulfill({ status: 409, json: { detail: 'conflict' } }));
  await page.locator('#body').fill('남아 있어야 하는 메시지'); await page.locator('#send').click();
  await expect(page.locator('#outbox')).toContainText('내용 충돌');
  await expect(page.locator('#outbox')).toContainText('남아 있어야 하는 메시지');
  await expect(page.getByRole('button', { name: '같은 요청 재시도' })).toBeHidden();
});

test('migration upload policy clears selection and blocks direct upload while chat continues', async ({ page, request }) => {
  await page.locator('#file').setInputFiles({ name: 'pending.txt', mimeType: 'text/plain', buffer: Buffer.from('not uploaded') });
  await expect(page.locator('#file-preview')).toBeVisible();
  await request.post('/__test__/switch/upload-blocked');
  await expect(page.locator('#file')).toBeDisabled();
  await expect(page.locator('#upload-policy')).toContainText('새 첨부파일을 올릴 수 없습니다');
  await expect(page.locator('#file-preview')).toBeHidden();
  expect(await page.locator('#file').evaluate((input) => input.files.length)).toBe(0);
  const direct = await request.put(`/api/attachments/${randomUUID()}`, {
    multipart: { file: { name: 'bypass.txt', mimeType: 'text/plain', buffer: Buffer.from('bypass') } },
  });
  expect(direct.status()).toBe(403);
  await page.locator('#body').fill('첨부 없이 계속 상담'); await page.locator('#send').click();
  await expect(page.locator('#outbox')).toBeHidden();
  await expect(page.locator('#log')).toContainText('첨부 없이 계속 상담');
  await request.post('/__test__/switch/aws');
  await expect(page.locator('#file')).toBeEnabled();
  await expect(page.locator('#upload-policy')).toBeHidden();
});

test('each message shows the environment the browser observed and the switch is recorded', async ({ page, request }) => {
  await page.locator('#body').fill('출발지에서 저장'); await page.locator('#send').click();
  await expect(page.locator('#log .msg .served')).toHaveText(['AWS 저장']);
  await request.post('/api/messages', { data: message('다른 참여자의 출발지 메시지') });
  await expect(page.locator('#log .msg .served')).toHaveText(['AWS 저장', 'AWS 경유']);
  await expect(page.locator('#env-pill')).toHaveAttribute('data-env', 'aws');
  await request.post('/__test__/switch/onprem');
  await expect(page.locator('#env')).toHaveText('온프렘');
  await expect(page.locator('#env-pill')).toHaveAttribute('data-env', 'onprem');
  await expectObservedClose(page, page.locator('#timeline'));
  await expect(page.locator('#timeline')).toContainText('처리 환경 전환 · AWS → 온프렘');
  await expect(page.locator('#log .system')).toContainText('AWS → 온프렘');
  await expect(page.locator('#stat-reconnects')).toHaveText('1회');
  await page.locator('#body').fill('목적지에서 저장'); await page.locator('#send').click();
  await expect(page.locator('#log .msg .served')).toHaveText(['AWS 저장', 'AWS 경유', '온프렘 저장']);
  await expect(page.locator('#split-legend')).toHaveText(/AWS 2건\s*온프렘 1건/);
  await expect(page.locator('#log .msg')).toHaveCount(3);
});
