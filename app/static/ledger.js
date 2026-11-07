// A mock points ledger carried entirely inside ordinary chat messages.
//
// Each transfer is one POST /api/messages whose body ends with a machine line:
//   BRAVO-LEDGER/1 {"t":"transfer","n":"<idempotency key>","from":"K7Q2X9",...}
// Nothing on the server knows about points: the API, schema and 8.10 contract are
// unchanged. Every browser replays the rows it has seen in id order with the same
// rules, so the platform's guarantees (no loss, no duplicate, ordered propagation)
// become a balance anyone can check. It is a demo ledger, not an authority: the
// server does not verify ownership or balances.

export const GRANT = 100000;
export const MAX_AMOUNT = 1000000;
export const PREFIX = 'BRAVO-LEDGER/1 ';
const CODE = /^[A-HJ-NP-Z2-9]{6}$/;
const NONCE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';

export function newAccountCode(random = (n) => crypto.getRandomValues(new Uint8Array(n))) {
  return [...random(6)].map((b) => ALPHABET[b % ALPHABET.length]).join('');
}

export function points(value) {
  return `${Math.trunc(value).toLocaleString('ko-KR')}P`;
}

function text(value, max) {
  return typeof value === 'string' && value.trim() === value && value.length >= 1 && value.length <= max;
}

export function encodeOpen({ acct, name }) {
  const event = { t: 'open', acct, name };
  if (!validEvent(event)) throw new Error('invalid_open');
  return `[Bravo 계좌 개설] ${name} · 축하 포인트 ${points(GRANT)}\n${PREFIX}${JSON.stringify(event)}`;
}

export function encodeTransfer({ n, from, to, amount, memo = '', fromName, toName }) {
  const event = { t: 'transfer', n, from, to, amount, memo };
  if (!validEvent(event)) throw new Error('invalid_transfer');
  const who = `${fromName || from} → ${toName || to}`;
  return `[Bravo 송금] ${points(amount)} · ${who}${memo ? ` · ${memo}` : ''}\n${PREFIX}${JSON.stringify(event)}`;
}

function validEvent(e) {
  if (!e || typeof e !== 'object' || Array.isArray(e)) return false;
  if (e.t === 'open') {
    return Object.keys(e).length === 3 && CODE.test(e.acct) && text(e.name, 20);
  }
  if (e.t === 'transfer') {
    return Object.keys(e).length === 6 && typeof e.n === 'string' && NONCE.test(e.n) &&
      CODE.test(e.from) && CODE.test(e.to) && e.from !== e.to &&
      Number.isSafeInteger(e.amount) && e.amount >= 1 && e.amount <= MAX_AMOUNT &&
      (e.memo === '' || text(e.memo, 40));
  }
  return false;
}

// null: an ordinary chat message. {invalid: true}: claims to be a ledger row but is not.
export function decode(body) {
  if (typeof body !== 'string') return null;
  const at = body.lastIndexOf(`\n${PREFIX}`);
  if (at < 0) return body.startsWith(PREFIX) ? { invalid: true } : null;
  try {
    const event = JSON.parse(body.slice(at + 1 + PREFIX.length));
    return validEvent(event) ? event : { invalid: true };
  } catch {
    return { invalid: true };
  }
}

export class Ledger {
  accounts = new Map(); // code -> { code, name, openedId, balance }
  transfers = new Map(); // nonce -> { id, n, from, to, amount, memo, env }
  ids = new Set(); // message ids of ledger rows already applied
  duplicates = 0; // the same transfer nonce stored as two different rows
  invalid = 0;
  external = 0; // net points held by accounts opened before this view (unknown here)

  // Returns what the row did, or null for an ordinary chat message.
  ingest(row, env = null) {
    const event = decode(row.body);
    if (event === null) return null;
    if (this.ids.has(row.id)) return { kind: 'seen' };
    this.ids.add(row.id);
    if (event.invalid) { this.invalid++; return { kind: 'invalid' }; }
    if (event.t === 'open') {
      if (this.accounts.has(event.acct)) return { kind: 'seen' };
      const account = { code: event.acct, name: event.name, openedId: row.id, balance: GRANT };
      this.accounts.set(event.acct, account);
      return { kind: 'open', account };
    }
    const known = this.transfers.get(event.n);
    if (known) {
      if (known.id !== row.id) this.duplicates++;
      return { kind: 'duplicate', transfer: known };
    }
    const transfer = { id: row.id, n: event.n, from: event.from, to: event.to, amount: event.amount, memo: event.memo, env, author: row.author, at: row.created_at };
    this.transfers.set(event.n, transfer);
    const from = this.accounts.get(event.from), to = this.accounts.get(event.to);
    if (from) from.balance -= event.amount; else this.external -= event.amount;
    if (to) to.balance += event.amount; else this.external += event.amount;
    return { kind: 'transfer', transfer };
  }

  total() {
    let sum = 0; for (const a of this.accounts.values()) sum += a.balance;
    return sum;
  }

  // Points can only move between accounts; the sum is fixed by how many were opened.
  conserved() {
    return this.total() + this.external === this.accounts.size * GRANT && this.duplicates === 0;
  }

  name(code) { return this.accounts.get(code)?.name ?? code; }

  history(code) {
    return [...this.transfers.values()].filter((t) => t.from === code || t.to === code).sort((a, b) => b.id - a.id);
  }

  snapshot() {
    return { v: 1, accounts: [...this.accounts.values()], transfers: [...this.transfers.values()], ids: [...this.ids], duplicates: this.duplicates, invalid: this.invalid, external: this.external };
  }

  static restore(data) {
    const ledger = new Ledger();
    if (!data || data.v !== 1) return ledger;
    for (const a of data.accounts || []) if (CODE.test(a.code) && Number.isSafeInteger(a.balance)) ledger.accounts.set(a.code, a);
    for (const t of data.transfers || []) if (NONCE.test(t.n) && Number.isSafeInteger(t.amount)) ledger.transfers.set(t.n, t);
    for (const id of data.ids || []) if (Number.isSafeInteger(id)) ledger.ids.add(id);
    for (const key of ['duplicates', 'invalid', 'external']) if (Number.isSafeInteger(data[key])) ledger[key] = data[key];
    return ledger;
  }
}
