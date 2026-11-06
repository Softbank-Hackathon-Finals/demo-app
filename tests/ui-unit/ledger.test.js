import { test } from 'node:test';
import assert from 'node:assert/strict';
import { GRANT, Ledger, decode, encodeOpen, encodeTransfer, newAccountCode } from '../../app/static/ledger.js';

const N1 = '00000000-0000-4000-8000-000000000001';
const N2 = '00000000-0000-4000-8000-000000000002';
let id = 0;
const row = (body, author = 'visitor') => ({ id: ++id, author, body, created_at: '2026-11-06T00:00:00Z', attachment_id: null });
const open = (acct, name) => row(encodeOpen({ acct, name }), name);
const send = (n, from, to, amount) => row(encodeTransfer({ n, from, to, amount, memo: '점심' }));

test('ledger rows are readable text and round-trip through the message body', () => {
  const body = encodeTransfer({ n: N1, from: 'AAAAAA', to: 'BBBBBB', amount: 30000, fromName: '방문자 07', toName: '방문자 03' });
  assert.match(body, /^\[Bravo 송금\] 30,000P · 방문자 07 → 방문자 03\n/);
  assert.equal(body.length < 2000, true);
  assert.deepEqual(decode(body), { t: 'transfer', n: N1, from: 'AAAAAA', to: 'BBBBBB', amount: 30000, memo: '' });
  assert.equal(decode('이체 한도는 어디서 바꾸나요?'), null);
  assert.equal(decode('t18 run=t18-test req=1'), null);
});

test('malformed or hostile ledger lines are counted, never applied', () => {
  const ledger = new Ledger();
  ledger.ingest(open('AAAAAA', 'a')); ledger.ingest(open('BBBBBB', 'b'));
  for (const body of [
    'x\nBRAVO-LEDGER/1 {bad json',
    'x\nBRAVO-LEDGER/1 {"t":"transfer","n":"' + N1 + '","from":"AAAAAA","to":"BBBBBB","amount":-5,"memo":""}',
    'x\nBRAVO-LEDGER/1 {"t":"transfer","n":"' + N1 + '","from":"AAAAAA","to":"AAAAAA","amount":5,"memo":""}',
    'x\nBRAVO-LEDGER/1 {"t":"transfer","n":"' + N1 + '","from":"AAAAAA","to":"BBBBBB","amount":1.5,"memo":""}',
    'x\nBRAVO-LEDGER/1 {"t":"open","acct":"AAAAAA","name":"a","balance":999999}',
    'x\nBRAVO-LEDGER/1 {"t":"open","acct":"<b>xx","name":"a"}',
  ]) assert.equal(ledger.ingest(row(body)).kind, 'invalid');
  assert.equal(ledger.invalid, 6);
  assert.equal(ledger.total(), 2 * GRANT);
  assert.equal(ledger.conserved(), true);
});

test('transfers move points, totals are conserved, and a repeated row is harmless', () => {
  const ledger = new Ledger();
  ledger.ingest(open('AAAAAA', 'a')); ledger.ingest(open('BBBBBB', 'b'));
  const first = send(N1, 'AAAAAA', 'BBBBBB', 30000);
  assert.equal(ledger.ingest(first).kind, 'transfer');
  assert.equal(ledger.ingest(first).kind, 'seen'); // POST ack and WebSocket deliver the same row
  assert.equal(ledger.ingest(row('같은 방 잡담')), null);
  assert.deepEqual([ledger.accounts.get('AAAAAA').balance, ledger.accounts.get('BBBBBB').balance], [70000, 130000]);
  assert.equal(ledger.conserved(), true);
  assert.deepEqual(ledger.history('BBBBBB').map((t) => t.n), [N1]);
});

test('the same transfer stored twice is detected as a duplicate and not double-spent', () => {
  const ledger = new Ledger();
  ledger.ingest(open('AAAAAA', 'a')); ledger.ingest(open('BBBBBB', 'b'));
  ledger.ingest(send(N2, 'AAAAAA', 'BBBBBB', 1000));
  assert.equal(ledger.ingest(send(N2, 'AAAAAA', 'BBBBBB', 1000)).kind, 'duplicate');
  assert.equal(ledger.accounts.get('AAAAAA').balance, 99000);
  assert.equal(ledger.duplicates, 1);
  assert.equal(ledger.conserved(), false);
});

test('accounts opened before this view are tracked as external, keeping the check exact', () => {
  const ledger = new Ledger();
  ledger.ingest(open('AAAAAA', 'a'));
  ledger.ingest(send('00000000-0000-4000-8000-000000000003', 'AAAAAA', 'ZZZZZZ', 5000));
  ledger.ingest(send('00000000-0000-4000-8000-000000000004', 'YYYYYY', 'AAAAAA', 2000));
  assert.equal(ledger.accounts.get('AAAAAA').balance, 97000);
  assert.equal(ledger.external, 3000);
  assert.equal(ledger.conserved(), true);
});

test('snapshot restore resumes the same state and rejects tampered entries', () => {
  const ledger = new Ledger();
  ledger.ingest(open('AAAAAA', 'a')); ledger.ingest(open('BBBBBB', 'b'));
  const t = send('00000000-0000-4000-8000-000000000005', 'AAAAAA', 'BBBBBB', 700);
  ledger.ingest(t);
  const copy = Ledger.restore(JSON.parse(JSON.stringify(ledger.snapshot())));
  assert.equal(copy.total(), ledger.total());
  assert.equal(copy.ingest(t).kind, 'seen');
  assert.equal(Ledger.restore({ v: 1, accounts: [{ code: 'bad', balance: 1 }] }).accounts.size, 0);
  assert.equal(Ledger.restore(null).accounts.size, 0);
});

test('account codes use an unambiguous alphabet', () => {
  assert.match(newAccountCode(), /^[A-HJ-NP-Z2-9]{6}$/);
  assert.equal(newAccountCode(() => new Uint8Array([0, 1, 2, 3, 4, 5])), 'ABCDEF');
});
