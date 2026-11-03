import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MessageCursor, deliver, retryDelay, shareAddress } from '../../app/static/transport.js';

const message = (id) => ({ id, author: 'visitor', body: `message ${id}`, created_at: '2026-11-03T00:00:00Z', attachment_id: null });
test('only the ordered stream advances the resume cursor, not a POST acknowledgement', () => {
  const cursor = new MessageCursor();
  cursor.accept([message(1)]);
  const acknowledged = message(4); // UI may render this while messages 2/3 are still in flight.
  assert.equal(acknowledged.id, 4);
  assert.equal(cursor.value, 1);
  assert.deepEqual(cursor.accept([message(2), message(3), message(4)]).map((m) => m.id), [2,3,4]);
  assert.equal(cursor.value, 4);
  assert.deepEqual(cursor.accept([message(3), message(4)]), []);
});
test('a malformed tail cannot partially advance a valid stream cursor', () => {
  const cursor = new MessageCursor(); cursor.accept([message(1)]);
  for (const items of [[message(2), message(2)], [message(3), message(2)], [message(2), { ...message(3), id: Number.MAX_SAFE_INTEGER + 1 }], [message(2), { ...message(3), attachment_id: '../admin' }]]) {
    assert.throws(() => cursor.accept(items)); assert.equal(cursor.value, 1);
  }
});
test('503, response loss and retries retain one caller-owned payload', async () => {
  const payload = Object.freeze({ idempotency_key: 'same-request', body: 'hello' });
  const calls = [], waits = []; let time = 0;
  const result = await deliver(async (signal) => {
    assert.equal(signal.aborted, false); calls.push(JSON.stringify(payload));
    if (calls.length === 1) return new Response('{}', { status: 503, headers: { 'Retry-After': '2' } });
    if (calls.length === 2) throw new Error('response lost after server commit');
    return Response.json({ id: 1 });
  }, { now: () => time, wait: async (ms) => { waits.push(ms); time += ms; } });
  assert.equal(result.id, 1); assert.equal(new Set(calls).size, 1); assert.deepEqual(waits, [2000,1000]);
});
test('conflict is terminal and never retries with a new idempotency key', async () => {
  let calls = 0;
  await assert.rejects(deliver(async () => { calls++; return new Response('', { status: 409 }); }), { code: 'conflict' });
  assert.equal(calls, 1);
});
test('unknown response is bounded and not reported as cancellation or success', async () => {
  let time = 0, calls = 0;
  await assert.rejects(deliver(async () => { calls++; throw new Error('offline'); }, {
    budgetMs: 1600, now: () => time, wait: async (ms) => { time += ms; },
  }), { code: 'unconfirmed' });
  assert.equal(calls, 3);
});
test('wall clock moving backwards still has a bounded attempt count', async () => {
  let calls = 0;
  await assert.rejects(deliver(async () => { calls++; throw new Error('lost'); }, {
    now: () => -calls * 1000, wait: async () => {},
  }), { code: 'unconfirmed' });
  assert.equal(calls, 32);
});
test('timeout aborts the attempt and does not fabricate acknowledgement', async () => {
  let aborted = 0;
  await assert.rejects(deliver((signal) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => { aborted++; reject(new Error('aborted')); });
  }), { timeoutMs: 5, budgetMs: 30 }), { code: 'unconfirmed' });
  assert.equal(aborted, 1);
});
test('manual pause stops retries but is not a server-side undo', async () => {
  const controller = new AbortController(); controller.abort();
  let called = false;
  await assert.rejects(deliver(async () => { called = true; }, { signal: controller.signal }), { code: 'paused' });
  assert.equal(called, false);
});
test('Retry-After values cannot produce negative or unlimited waits', () => {
  assert.equal(retryDelay('9e11', 0), 30000);
  assert.equal(retryDelay('-1', 0), 500);
  assert.equal(retryDelay('bad', 3), 4000);
  assert.equal(retryDelay('Tue, 03 Nov 2026 00:00:02 GMT', 0, Date.parse('2026-11-03T00:00:00Z')), 2000);
});
test('share address strips credentials/query/fragment and never calls external service', () => {
  assert.equal(shareAddress({ href: 'https://chat.example.test/?token=private#admin' }), 'https://chat.example.test/');
  assert.throws(() => shareAddress({ href: 'https://user:password@chat.example.test/' }));
  assert.throws(() => shareAddress({ href: 'javascript:alert(1)' }));
});
