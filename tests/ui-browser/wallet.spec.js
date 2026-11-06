import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';

const admin = { Authorization: 'Bearer browser-test-only' };
const ledgerRows = async (request) => (await (await request.get('/api/messages')).json())
  .filter((m) => m.body.includes('\nBRAVO-LEDGER/1 '));

async function openAccount(page, name) {
  await page.goto('/');
  await expect(page.locator('#conn')).toHaveText('연결됨');
  await page.locator('#nav-wallet').click();
  await page.locator('#open-name').fill(name);
  await page.locator('#open-account').click();
  await expect(page.locator('#balance')).toHaveText('100,000P');
  const code = (await page.locator('#account-code').textContent()).replace('BRV-', '');
  await page.locator('#wallet-close').click();
  return code;
}

// recipient → amount (keypad) → confirm → result, as a participant would.
async function startTransfer(page, code, digits) {
  await page.locator('#composer-pay').click();
  await page.locator(`#to-list [data-code="${code}"]`).click();
  for (const key of digits) await page.locator(`.keypad [data-key="${key}"]`).click();
  await page.locator('#amount-next').click();
}

test.beforeEach(async ({ request }) => { await request.post('/__test__/reset'); });

test('two participants open accounts and a transfer moves points exactly once', async ({ page, browser, request }) => {
  const other = await (await browser.newContext()).newPage();
  await openAccount(page, '방문자 07');
  const code = await openAccount(other, '방문자 03');
  await page.locator('#composer-pay').click();
  await expect(page.locator('#to-list')).toContainText('방문자 03');
  await page.locator('#to-search').fill('03');
  await expect(page.locator('#to-list button')).toHaveCount(1);
  await page.locator(`#to-list [data-code="${code}"]`).click();
  await page.locator('[data-add="10000"]').click();
  await expect(page.locator('#amount-display')).toHaveText('10,000P');
  for (let i = 0; i < 3; i++) await page.locator('.keypad [data-key="0"]').click(); // capped at 7 digits
  await expect(page.locator('#amount-display')).toHaveText('1,000,000P');
  await expect(page.locator('#amount-hint')).toContainText('잔액이 부족해요');
  await expect(page.locator('#amount-next')).toBeDisabled();
  for (let i = 0; i < 7; i++) await page.locator('.keypad [data-key="back"]').click();
  await expect(page.locator('#amount-display')).toHaveText('얼마를 보낼까요?');
  for (const key of ['3', '0', '00', '0']) await page.locator(`.keypad [data-key="${key}"]`).click();
  await expect(page.locator('#amount-display')).toHaveText('30,000P');
  await expect(page.locator('#amount-hint')).toHaveText('3만P');
  await page.locator('#amount-next').click();
  await expect(page.locator('#step-confirm h2')).toContainText('방문자 03님에게');
  await expect(page.locator('#confirm-amount')).toHaveText('30,000P');
  await page.locator('#memo').fill('점심값');
  await page.locator('#transfer-send').click();
  await expect(page.locator('#done-mark')).toHaveAttribute('data-state', 'ok');
  await expect(page.locator('#done-title')).toHaveText('방문자 03님에게\n30,000P를 보냈어요');
  await page.locator('#done-close').click();
  await expect(page.locator('#balance')).toHaveText('70,000P');
  await expect(other.locator('#balance')).toHaveText('130,000P');
  await expect(other.locator('#history')).toContainText('방문자 07님이');
  await expect(other.locator('#history')).toContainText('+30,000P');
  await expect(page.locator('#history')).toContainText('점심값');
  expect((await ledgerRows(request)).length).toBe(3); // two openings and one transfer
  await expect(page.locator('#log .transfer')).toHaveCount(1); // one saved transfer card; openings stay out of chat
  await expect(page.locator('#log .transfer')).toContainText('30,000P');
  await expect(other.locator('#log .transfer')).toHaveCount(1);
});

test('a stream-confirmed transfer with a lost POST response shows one debit and one card', async ({ page, browser, request }) => {
  const other = await (await browser.newContext()).newPage();
  const code = await openAccount(other, '받는 사람');
  await openAccount(page, '보내는 사람');
  let release;
  const held = new Promise(resolve => { release = resolve; });
  let first = true;
  const bodies = [];
  await page.route('**/api/messages', async route => {
    if (route.request().method() !== 'POST') return route.continue();
    bodies.push(route.request().postData());
    if (!first) return route.continue();
    first = false;
    await route.fetch(); // Stored and streamed, but HTTP acknowledgement is withheld.
    await held;
    await route.abort('failed');
  });
  try {
    await startTransfer(page, code, ['1', '0', '0', '0', '0']);
    await page.locator('#transfer-send').click();
    await expect(page.locator('#log .transfer')).toHaveCount(1);
    await expect(page.locator('#balance')).toHaveText('90,000P');
    await expect(page.locator('#done-mark')).toHaveAttribute('data-state', 'pending');
    release();
    await expect(page.locator('#done-mark')).toHaveAttribute('data-state', 'ok', { timeout: 8000 });
    await expect(page.locator('#log .transfer')).toHaveCount(1);
    await expect(other.locator('#balance')).toHaveText('110,000P');
    expect(bodies.length).toBeGreaterThanOrEqual(2);
    expect(new Set(bodies).size).toBe(1);
    expect((await ledgerRows(request)).filter(row => row.body.includes('"t":"transfer"')).length).toBe(1);
  } finally { release(); }
});

test('a transfer during the write pause and switch is debited once and re-verified on the target', async ({ page, browser, request }) => {
  const other = await (await browser.newContext()).newPage();
  const code = await openAccount(other, '받는 사람');
  await openAccount(page, '보내는 사람');
  const bodies = [];
  page.on('request', (r) => { if (new URL(r.url()).pathname === '/api/messages' && r.method() === 'POST') bodies.push(r.postData()); });
  await request.put('/admin/write-block', { headers: admin, data: { blocked: true } });
  await startTransfer(page, code, ['2', '5', '0', '0', '0']);
  await page.locator('#transfer-send').click();
  await expect(page.locator('#done-mark')).toHaveAttribute('data-state', 'pending');
  await expect(page.locator('#done-status')).toContainText('출금은 한 번만', { timeout: 8000 });
  await expect(page.locator('#ledger-outbox')).toContainText('같은 거래 번호');
  await request.post('/__test__/switch/onprem');
  await request.put('/admin/write-block', { headers: admin, data: { blocked: false } });
  await expect(page.locator('#done-mark')).toHaveAttribute('data-state', 'ok', { timeout: 8000 });
  await expect(page.locator('#done-status')).toContainText('온프렘에서 처리');
  await expect(page.locator('#done-status')).toContainText('출금은 한 번만 됐어요');
  await page.locator('#done-close').click();
  await expect(page.locator('#ledger-outbox')).toBeHidden();
  await expect(page.locator('#balance')).toHaveText('75,000P');
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
  await startTransfer(page, code, ['4', '0', '0', '0', '0']);
  await page.locator('#transfer-send').click();
  await expect(page.locator('#done-mark')).toHaveAttribute('data-state', 'ok');
  await page.locator('#done-close').click();
  await expect(page.locator('#balance')).toHaveText('60,000P');
  await page.waitForTimeout(600); // the snapshot is written shortly after the stream batch
  const sockets = []; page.on('websocket', (ws) => sockets.push(new URL(ws.url()).searchParams.get('after')));
  await page.reload();
  await expect(page.locator('#balance')).toHaveText('60,000P');
  expect(sockets[0]).not.toBeNull();
  await page.locator('#composer-pay').click();
  await page.locator(`#to-list [data-code="${code}"]`).click();
  await page.keyboard.type('60001');
  await expect(page.locator('#amount-hint')).toHaveText('잔액이 부족해요 · 보낼 수 있는 금액 60,000P');
  await expect(page.locator('#amount-next')).toBeDisabled();
  await page.locator('[data-add="all"]').click();
  await expect(page.locator('#amount-display')).toHaveText('60,000P');
  await expect(page.locator('#amount-next')).toBeEnabled();
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
  await startTransfer(page, code, ['1', '5', '0', '0', '0']);
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

test('phone chat: inline transfer, wallet history and recent recipient shortcut', async ({ browser }) => {
  test.slow(); // a long multi-step flow; WebKit under a full parallel run can exceed the default budget
  const friend = await (await browser.newContext()).newPage();
  const code = await openAccount(friend, '김하늘');
  const page = await (await browser.newContext({ viewport: { width: 390, height: 844 } })).newPage();
  await page.goto('/');
  await expect(page.locator('#conn')).toHaveText('연결됨');
  await page.locator('#composer-pay').click();
  await expect(page.locator('#toast')).toHaveText('모의 포인트 지갑을 먼저 만들어 주세요');
  await page.locator('#open-name').fill('방문자 07'); await page.locator('#open-account').click();
  await expect(page.locator('#balance')).toHaveText('100,000P');
  await expect(page.locator('#send-sheet')).toBeVisible(); // account creation returns directly to the requested transfer
  await expect(page.locator('#recent-card')).toBeHidden();
  await expect(page.locator('#to-recent-wrap')).toBeHidden();
  await page.locator(`#to-list [data-code="${code}"]`).click();
  await page.locator('[data-add="10000"]').click(); await page.locator('#amount-next').click();
  await page.locator('#transfer-send').click();
  await expect(page.locator('#done-mark')).toHaveAttribute('data-state', 'ok');
  await expect(page.locator('#done-share')).toBeEnabled();
  await page.locator('#done-history').click();
  await expect(page.locator('#wallet-dialog')).toBeVisible();
  await expect(page.locator('#history')).toContainText('김하늘에게');
  await expect(page.locator('#recent button')).toHaveText(['김김하늘']);
  await page.locator(`#recent [data-code="${code}"]`).click();
  await expect(page.locator('#step-amount')).toBeVisible();
  await expect(page.locator('#amount-to')).toHaveText('김하늘');
  await page.locator('#sheet-back').click();
  await expect(page.locator('#to-recent')).toContainText('김하늘');
  await page.locator('#sheet-close').click();
  await page.locator('#composer-pay').click(); // dismisses the wallet and opens the transfer sheet
  await page.locator('#sheet-close').click();
  await expect(page.locator('#send')).toBeVisible();
  await expect(page.locator('#wallet')).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('one tap opens an account with a ready-made name', async ({ page }) => {
  await page.goto('/');
  await expect(page.locator('#conn')).toHaveText('연결됨');
  await page.locator('#nav-wallet').click();
  await expect(page.locator('#open-name')).toHaveValue(/^참여자 \d{4}$/);
  const name = await page.locator('#open-name').inputValue();
  await page.locator('#open-account').click();
  await expect(page.locator('#balance')).toHaveText('100,000P');
  await expect(page.locator('#account-name')).toHaveText(name);
});
