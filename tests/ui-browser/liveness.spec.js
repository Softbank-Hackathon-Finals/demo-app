import { test, expect } from '@playwright/test';

test('advertised idle heartbeat keeps the native connection alive without writes', async ({ page, request }) => {
  await request.post('/__test__/reset');
  let sockets = 0, statuses = 0, writes = 0;
  page.on('request', req => { if (req.method() === 'POST' || req.method() === 'PUT') writes++; });
  page.on('websocket', ws => {
    sockets++;
    ws.on('framereceived', frame => { if (JSON.parse(String(frame.payload)).type === 'status') statuses++; });
  });
  await page.goto('/');
  await expect(page.locator('#conn')).toHaveText('연결됨');
  await expect.poll(() => statuses, { timeout: 12000 }).toBeGreaterThanOrEqual(2);
  await expect(page.locator('#conn')).toHaveText('연결됨');
  expect(sockets).toBe(1); expect(writes).toBe(0);
});

test('silent advertised connection loses online state and reconnects', async ({ page, request }) => {
  await request.post('/__test__/reset');
  let connections = 0;
  await page.routeWebSocket('**/ws*', socket => {
    const first = ++connections === 1;
    const server = socket.connectToServer();
    server.onMessage(raw => {
      const value = JSON.parse(String(raw));
      // Controlled receive-blackhole: only the first hello reaches this client.
      // No server outage, database mutation or production route is injected.
      if (first && value.type === 'hello') socket.send(JSON.stringify({ ...value, heartbeat_interval_seconds: 5 }));
    });
  });
  await page.goto('/');
  await expect(page.locator('#conn')).toHaveText('연결됨');
  await expect(page.locator('#conn')).not.toHaveClass(/online/, { timeout: 18000 });
  await expect.poll(() => connections, { timeout: 2000 }).toBeGreaterThan(1);
  await expect(page.locator('#environment-note')).toHaveText('마지막으로 확인한 환경입니다. 다시 연결하는 중입니다.');
});

test('older server without capability does not receive an assumed idle deadline', async ({ page, request }) => {
  await request.post('/__test__/reset');
  let connections = 0;
  await page.routeWebSocket('**/ws*', socket => {
    connections++;
    const server = socket.connectToServer();
    server.onMessage(raw => {
      const value = JSON.parse(String(raw));
      if (value.type === 'hello') {
        delete value.heartbeat_interval_seconds;
        socket.send(JSON.stringify(value));
      }
    });
  });
  await page.goto('/');
  await expect(page.locator('#conn')).toHaveText('연결됨');
  await page.waitForTimeout(16000);
  await expect(page.locator('#conn')).toHaveText('연결됨');
  expect(connections).toBe(1);
});

for (const interval of ['5', true, 0, 60]) {
  test(`malformed heartbeat capability ${JSON.stringify(interval)} never grants online`, async ({ page, request }) => {
    await request.post('/__test__/reset');
    let connections = 0;
    await page.routeWebSocket('**/ws*', socket => {
      connections++;
      const server = socket.connectToServer();
      server.onMessage(raw => {
        const value = JSON.parse(String(raw));
        if (value.type === 'hello') socket.send(JSON.stringify({ ...value, heartbeat_interval_seconds: interval }));
      });
    });
    await page.goto('/');
    await expect.poll(() => connections).toBeGreaterThan(1);
    await expect(page.locator('#conn')).not.toHaveClass(/online/);
    await expect(page.locator('#feedback')).toHaveText('응답을 확인할 수 없어 다시 연결합니다.');
  });
}
