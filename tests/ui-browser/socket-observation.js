import { expect } from '@playwright/test';

// Observe the native browser CloseEvent independently of the UI projection.
// Server ASGI tests separately require the emitted restart code to be 1012.
export async function observeSocketClosures(page) {
  await page.addInitScript(() => {
    const Native = window.WebSocket;
    window.__bravoObservedCloseCodes = [];
    window.WebSocket = new Proxy(Native, {
      construct(target, args) {
        const socket = Reflect.construct(target, args);
        socket.addEventListener('close', event => window.__bravoObservedCloseCodes.push(event.code));
        return socket;
      },
    });
  });
}

export async function expectObservedClose(page, timeline) {
  await expect.poll(() => page.evaluate(() => window.__bravoObservedCloseCodes.length)).toBeGreaterThan(0);
  const code = await page.evaluate(() => window.__bravoObservedCloseCodes[0]);
  await expect(timeline).toContainText(`(${code})`);
}
