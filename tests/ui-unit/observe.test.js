import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Continuity, duration, envKey, envLabel } from '../../app/static/observe.js';

test('labels never echo an unknown server value', () => {
  assert.equal(envLabel('aws'), 'AWS');
  assert.equal(envLabel('onprem'), '온프렘');
  assert.equal(envLabel('<script>'), '미확인 환경');
  assert.equal(envLabel('constructor'), '미확인 환경');
  assert.equal(envKey('toString'), 'unknown');
  assert.equal(duration(820), '820ms');
  assert.equal(duration(4250), '4.3초');
  assert.equal(duration(-1), '-');
});

test('a 1012 cutover close, reconnect and environment change are recorded once', () => {
  const c = new Continuity();
  assert.deepEqual(c.connected('aws', false, 0).map((e) => e.kind), ['connect']);
  assert.equal(c.dropped(1012, 1000).text.includes('1012'), true);
  assert.equal(c.dropped(1006, 1200), null); // repeated failures belong to the same outage
  const events = c.connected('onprem', false, 1800);
  assert.deepEqual(events.map((e) => e.kind), ['reconnect', 'switch']);
  assert.equal(c.lastGapMs, 800);
  assert.equal(c.reconnects, 1);
  assert.equal(c.switches, 1);
  assert.equal(c.events[0].text, '처리 환경 전환 · AWS → 온프렘');
});

test('write pause time accumulates, including an ongoing pause', () => {
  const c = new Continuity();
  c.connected('aws', false, 0);
  assert.deepEqual(c.status('aws', true, 100).map((e) => e.kind), ['pause']);
  assert.equal(c.blockedMs(600), 500);
  assert.deepEqual(c.status('aws', false, 2100).map((e) => e.kind), ['resume']);
  assert.equal(c.blockedMs(9000), 2000);
  assert.deepEqual(c.status('aws', false, 9100), []);
});

test('only retried deliveries are reported, and the journal is bounded', () => {
  const c = new Continuity(3);
  assert.equal(c.delivered(1, 1, 0), null);
  assert.match(c.delivered(2, 3, 0).text, /3번 시도, 1건만 저장/);
  assert.equal(c.retried, 1);
  for (let i = 0; i < 5; i++) c.dropped(1006, i) && c.connected('aws', false, i);
  assert.equal(c.events.length, 3);
});
