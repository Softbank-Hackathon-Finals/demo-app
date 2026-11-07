import { test, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';

test.beforeEach(async ({ request }) => { await request.post('/__test__/reset'); });

test('the public room starts empty; names, previews and searches use actual messages', async ({ page, request }) => {
  await page.goto('/');
  await expect(page.locator('#conn')).toHaveText('연결됨');
  expect(await (await request.get('/api/messages')).json()).toEqual([]);
  await expect(page.locator('#log .msg')).toHaveCount(0);
  const send = async (author, body) => request.post('/api/messages', { data: { author, body, idempotency_key: randomUUID() } });
  await send('김하늘', '점심 정산은 여기에서 해요');
  await send('<img src=x onerror=alert(1)>', '다음 환경에서도 대화가 이어져요');
  await expect(page.locator('#room-preview')).toContainText('다음 환경에서도');
  await page.locator('#nav-friends').click();
  await expect(page.locator('#friends-list button')).toHaveCount(2);
  await expect(page.locator('#friends-list img')).toHaveCount(0);
  await page.locator('#list-search').fill('김하늘');
  await expect(page.locator('#friends-list button')).toHaveCount(1);
  await page.locator('#friends-list button').click();
  await page.locator('#room-search-toggle').click();
  await page.locator('#room-search').fill('점심 정산');
  await expect(page.locator('#search-count')).toHaveText('1건');
  await expect(page.locator('#log .msg:visible')).toHaveCount(1);
  await send('김하늘', '점심 정산을 마쳤어요');
  await expect(page.locator('#search-count')).toHaveText('2건');
  await page.locator('#room-search-toggle').click();
  await expect(page.locator('#log .msg:visible')).toHaveCount(3);
});

test('mobile room/list navigation and emoji entry keep the real message contract', async ({ page, request }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await expect(page.locator('#conn')).toHaveText('연결됨');
  await expect(page.locator('#send')).toBeVisible();
  await page.locator('#room-back').click();
  await expect(page.locator('#list-title')).toHaveText('채팅');
  await expect(page.locator('#nav-more')).toBeVisible();
  await page.locator('#nav-more').click();
  await expect(page.locator('#more-view')).toContainText('독립 데모');
  await page.locator('#nav-wallet').click();
  await expect(page.locator('#wallet-dialog')).toBeVisible();
  await page.locator('#wallet-close').click();
  await page.locator('#nav-chats').click();
  await page.locator('#room-open').click();
  await page.locator('#name').fill('내 별명');
  await page.locator('#body').fill('안녕하세요 ');
  await page.locator('#emoji-toggle').click();
  await page.getByRole('button', { name: '👋 입력', exact: true }).click();
  await page.locator('#send').click();
  await expect(page.locator('#log .msg')).toHaveCount(1);
  const [row] = await (await request.get('/api/messages')).json();
  expect(row.author).toBe('내 별명');
  expect(row.body).toContain('👋');
  await expect(page.locator('#emoji-picker')).toBeHidden();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
