import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';

const admin = { Authorization: 'Bearer browser-test-only' };
const ledgerRows = async (request) => (await (await request.get('/api/messages')).json())
  .filter((m) => m.body.includes('\nBRAVO-LEDGER/1 '));

async function openAccount(page, name) {
  await page.goto('/');
  await expect(page.locator('#conn')).toHaveText('연결됨');
  await page.locator('#open-name').fill(name);
  await page.locator('#open-account').click();
  await expect(page.locator('#balance')).toHaveText('100,000P');
  return (await page.locator('#account-code').textContent()).replace('BRV-', '');
}

test.beforeEach(async ({ request }) => { await request.post('/__test__/reset'); });

test('two participants open accounts and a transfer moves points exactly once', async ({ page, browser, request }) => {
  const other = await (await browser.newContext()).newPage();
  await openAccount(page, '방문자 07');
  const code = await openAccount(other, '방문자 03');
  await expect(page.locator('#to')).toContainText(`방문자 03 · BRV-${code}`);
  await page.locator('#to').selectOption(code);
  await page.locator('[data-amount="30000"]').click();
  await page.locator('#memo').fill('점심값');
  await page.locator('#transfer-send').click();
  await expect(page.locator('#balance')).toHaveText('70,000P');
  await expect(other.locator('#balance')).toHaveText('130,000P');
  await expect(other.locator('#history')).toContainText('방문자 07님이');
  await expect(other.locator('#history')).toContainText('+30,000P');
  await expect(page.locator('#history')).toContainText('점심값');
  expect((await ledgerRows(request)).length).toBe(3); // two openings and one transfer
  await expect(page.locator('#log .msg')).toHaveCount(0); // ledger rows are not chat
});

test('a transfer during the write pause and switch is debited once and re-verified on the target', async ({ page, browser, request }) => {
  const other = await (await browser.newContext()).newPage();
  const code = await openAccount(other, '받는 사람');
  await openAccount(page, '보내는 사람');
  await expect(page.locator('#to')).toContainText('받는 사람');
  const bodies = [];
  page.on('request', (r) => { if (new URL(r.url()).pathname === '/api/messages' && r.method() === 'POST') bodies.push(r.postData()); });
  await request.put('/admin/write-block', { headers: admin, data: { blocked: true } });
  await page.locator('#to').selectOption(code);
  await page.locator('#amount').fill('25000');
  await page.locator('#transfer-send').click();
  await expect(page.locator('#ledger-outbox')).toContainText('같은 거래 번호');
  await expect(page.locator('#balance')).toHaveText('75,000P'); // reserved while pending
  await request.post('/__test__/switch/onprem');
  await request.put('/admin/write-block', { headers: admin, data: { blocked: false } });
  await expect(page.locator('#ledger-outbox')).toBeHidden({ timeout: 8000 });
  await expect(page.locator('#env')).toHaveText('온프렘');
  await expect(other.locator('#balance')).toHaveText('125,000P');
  expect(bodies.length).toBeGreaterThanOrEqual(2); expect(new Set(bodies).size).toBe(1);
  expect((await ledgerRows(request)).filter((m) => m.body.includes('"t":"transfer"')).length).toBe(1);
  await expect(page.locator('#verify-result')).toHaveAttribute('data-state', 'ok', { timeout: 8000 });
  await expect(page.locator('#verify-result')).toContainText('온프렘 환경의 DB에서 계좌 개설과 거래 1건을 모두 그대로 확인');
  await expect(page.locator('#history .served')).toHaveText(['온프렘 처리']);
});

test('load-test rows stay out of the chat and are only counted', async ({ page, request }) => {
  await page.goto('/');
  await expect(page.locator('#conn')).toHaveText('연결됨');
  for (let i = 0; i < 3; i++) {
    await request.post('/api/messages', { data: { author: 'loadgen-t18-demo', body: `t18 run=demo req=${i}`, idempotency_key: randomUUID() } });
  }
  await request.post('/api/messages', { data: { author: '상담원', body: '안녕하세요', idempotency_key: randomUUID() } });
  await expect(page.locator('#log .msg')).toHaveCount(1);
  await expect(page.locator('#load-note')).toHaveText('부하 시험 메시지 3건은 대화창에 표시하지 않습니다');
});

test('a reload resumes the saved ledger instead of replaying, and overdraft is refused', async ({ page, browser }) => {
  const other = await (await browser.newContext()).newPage();
  const code = await openAccount(other, '친구');
  await openAccount(page, '나');
  await expect(page.locator('#to')).toContainText('친구');
  await page.locator('#to').selectOption(code);
  await page.locator('#amount').fill('40000');
  await page.locator('#transfer-send').click();
  await expect(page.locator('#balance')).toHaveText('60,000P');
  await page.waitForTimeout(600); // the snapshot is written shortly after the stream batch
  const sockets = []; page.on('websocket', (ws) => sockets.push(new URL(ws.url()).searchParams.get('after')));
  await page.reload();
  await expect(page.locator('#balance')).toHaveText('60,000P');
  expect(sockets[0]).not.toBeNull();
  await page.locator('#to').selectOption(code);
  await page.locator('#amount').fill('60001');
  await page.locator('#transfer-send').click();
  await expect(page.locator('#transfer-feedback')).toHaveText('잔액이 부족합니다.');
  await expect(page.locator('#balance')).toHaveText('60,000P');
});

test('the read-only stage view shows conservation, the live feed and the switch', async ({ page, browser, request }) => {
  const stage = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  const writes = []; stage.on('request', (r) => { if (r.method() !== 'GET') writes.push(r.url()); });
  await stage.goto('/stage.html');
  await expect(stage.locator('#conn')).toHaveText('연결됨');
  await expect(stage.locator('#env')).toHaveText('AWS');
  const other = await (await browser.newContext()).newPage();
  const code = await openAccount(other, '받는 사람');
  await openAccount(page, '보내는 사람');
  await expect(page.locator('#to')).toContainText('받는 사람');
  await page.locator('#to').selectOption(code); await page.locator('#amount').fill('15000');
  await page.locator('#transfer-send').click();
  await expect(stage.locator('#feed')).toContainText('보내는 사람 → 받는 사람');
  await expect(stage.locator('#total')).toHaveText('200,000P');
  await expect(stage.locator('#invariant')).toHaveAttribute('data-state', 'ok');
  await expect(stage.locator('#k-duplicates')).toHaveText('0건');
  await request.post('/__test__/switch/onprem');
  await expect(stage.locator('#env')).toHaveText('온프렘');
  await expect(stage.locator('#path')).toHaveText(/AWS\s*온프렘/);
  await expect(stage.locator('#timeline')).toContainText('1012');
  expect(writes).toEqual([]);
});
