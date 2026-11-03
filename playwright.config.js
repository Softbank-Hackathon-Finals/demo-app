import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/ui-browser',
  timeout: 25000,
  workers: 1,
  retries: 0,
  projects: [
    { name: 'chromium', use: { browserName: 'chromium' } },
    { name: 'webkit', use: { browserName: 'webkit' } },
  ],
  use: { baseURL: 'http://127.0.0.1:18922', trace: 'retain-on-failure' },
  webServer: {
    command: '.venv/bin/python -m uvicorn tests.browser_server:browser_app --factory --host 127.0.0.1 --port 18922',
    env: { BRAVO_BROWSER_TEST: '1' },
    url: 'http://127.0.0.1:18922/healthz',
    reuseExistingServer: false,
  },
});
