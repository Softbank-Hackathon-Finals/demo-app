import { test } from 'node:test';
import assert from 'node:assert/strict';
import { label } from '../../app/static/pii-check.js';

test('개인정보가 있으면 종류와 개수만 보인다', () => {
  assert.deepEqual(label({ has_pii: true, counts: { card: 1, rrn: 2 } }), { text: '개인정보 포함 · 카드번호 1, 주민번호 2', warn: true });
});

test('없으면 경고하지 않는다', () => {
  assert.deepEqual(label({ has_pii: false, counts: {} }), { text: '개인정보 없음', warn: false });
});
