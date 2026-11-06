import { GRANT, Ledger, MAX_AMOUNT, decode, encodeOpen, encodeTransfer, newAccountCode, points } from './ledger.js';
import { envKey, envLabel } from './observe.js';

const $ = (id) => document.getElementById(id);
const STATE_KEY = 'bravo-ledger-v1';
const ACCOUNT_KEY = 'bravo-account-v1';
const timeFormat = new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit' });
const clockFormat = new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });

let ledger = new Ledger();
let me = null; // { code, name } owned by this browser
let opening = false, saveTimer, verifyTimer, hooks;
let backgroundRows = 0;

function read(key) {
  try { return JSON.parse(localStorage.getItem(key)); } catch { return null; }
}
function write(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* Storage may be unavailable. */ }
}

// Restores the ledger and the stream position it was computed at. The caller
// resumes the WebSocket from that cursor so no row between them is skipped.
export function restore() {
  const account = read(ACCOUNT_KEY);
  if (account && /^[A-HJ-NP-Z2-9]{6}$/.test(account.code) && typeof account.name === 'string') me = account;
  const state = read(STATE_KEY);
  if (state && Number.isSafeInteger(state.cursor)) ledger = Ledger.restore(state.ledger);
  render();
  return state && Number.isSafeInteger(state.cursor) ? state.cursor : null;
}

// The database no longer has rows up to the saved cursor (a reset test DB).
export function discard() {
  ledger = new Ledger(); me = null;
  try { localStorage.removeItem(STATE_KEY); localStorage.removeItem(ACCOUNT_KEY); } catch { /* ignore */ }
  render();
}

export function persist(cursorValue) {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    if (Number.isSafeInteger(cursorValue)) write(STATE_KEY, { cursor: cursorValue, ledger: ledger.snapshot() });
  }, 400);
}

export function background() { return backgroundRows; }

export function isChat(row) {
  return !row.author.startsWith('loadgen-') && decode(row.body) === null;
}

// Splits stream rows: ledger rows update the wallet, load-test rows are only
// counted, and everything else is returned for the chat log.
export function ingest(rows, env) {
  const chat = [];
  let changed = false;
  for (const row of rows) {
    if (row.author.startsWith('loadgen-')) { backgroundRows++; continue; }
    const result = ledger.ingest(row, env);
    if (result === null) chat.push(row);
    else changed = true;
  }
  if (changed) render();
  return chat;
}

export function acknowledged(saved, ack) {
  ledger.ingest(saved, ack?.env ?? null);
  render();
}

export function balance() {
  return me ? ledger.accounts.get(me.code)?.balance ?? null : null;
}

function reserved() {
  return hooks.pending().filter((e) => e.kind === 'ledger' && e.transfer).reduce((sum, e) => sum + e.transfer.amount, 0);
}

// --- Rendering --------------------------------------------------------------

function render() {
  const account = me && ledger.accounts.get(me.code);
  $('onboard').hidden = Boolean(me);
  $('account').hidden = !me;
  $('transfer-form').hidden = !account;
  $('history-card').hidden = !me;
  if (!me) return;
  $('account-name').textContent = me.name;
  $('account-code').textContent = `BRV-${me.code}`;
  if (!account) {
    $('balance').textContent = opening ? '개설 중' : '확인 중';
    $('balance-note').textContent = opening ? '개설 요청을 같은 요청으로 보내고 있습니다.' : '장부에서 계좌를 찾는 중입니다.';
    return;
  }
  const hold = reserved();
  $('balance').textContent = points(account.balance - hold);
  $('balance-note').textContent = hold ? `처리 대기 송금 ${points(hold)} 제외 · 장부 잔액 ${points(account.balance)}` : '사용 가능 포인트';
  renderRecipients();
  renderHistory();
}

function renderRecipients() {
  const select = $('to'), current = select.value;
  const options = [...ledger.accounts.values()].filter((a) => a.code !== me.code)
    .sort((a, b) => b.openedId - a.openedId).slice(0, 50)
    .map((a) => { const o = document.createElement('option'); o.value = a.code; o.textContent = `${a.name} · BRV-${a.code}`; return o; });
  const manual = document.createElement('option'); manual.value = ''; manual.textContent = options.length ? '계좌번호 직접 입력' : '계좌번호 직접 입력 (아직 다른 참여자가 없습니다)';
  select.replaceChildren(...options, manual);
  if ([...select.options].some((o) => o.value === current)) select.value = current;
  $('to-code-row').hidden = select.value !== '';
}

function renderHistory() {
  const items = ledger.history(me.code).slice(0, 30).map((t) => {
    const out = t.from === me.code;
    const li = document.createElement('li'); li.className = out ? 'out' : 'in';
    const who = document.createElement('div'); who.className = 'who';
    const name = document.createElement('strong'); name.textContent = out ? `${ledger.name(t.to)}에게` : `${ledger.name(t.from)}님이`;
    const memo = document.createElement('small');
    memo.textContent = [t.at ? timeFormat.format(new Date(t.at)) : '', t.memo, `#${t.id}`].filter(Boolean).join(' · ');
    who.append(name, memo);
    const side = document.createElement('div'); side.className = 'side';
    const amount = document.createElement('span'); amount.className = 'amount'; amount.textContent = `${out ? '−' : '+'}${points(t.amount)}`;
    const env = document.createElement('span'); env.className = 'served'; env.dataset.env = envKey(t.env);
    env.textContent = t.env ? `${envLabel(t.env)} 처리` : '처리 환경 미확인';
    side.append(amount, env);
    li.append(who, side); return li;
  });
  if (!items.length) {
    const li = document.createElement('li'); li.className = 'placeholder'; li.textContent = '아직 거래가 없습니다. 첫 송금을 보내 보세요.'; items.push(li);
  }
  $('history').replaceChildren(...items);
}

// --- Actions ----------------------------------------------------------------

function openAccount() {
  const name = $('open-name').value.trim().slice(0, 20);
  if (!name) { $('open-feedback').textContent = '이름(별명)을 입력해 주세요.'; $('open-name').focus(); return; }
  me = { code: newAccountCode(), name }; write(ACCOUNT_KEY, me); opening = true;
  if (!$('name').value.trim()) $('name').value = name;
  hooks.enqueue({
    kind: 'ledger', author: name, body: encodeOpen({ acct: me.code, name }), container: $('ledger-outbox'),
    summary: `계좌 개설 · 축하 포인트 ${points(GRANT)}`,
    onSaved: (saved, ack) => { opening = false; acknowledged(saved, ack); $('transfer-feedback').textContent = `계좌를 개설했습니다. ${points(GRANT)}가 들어왔습니다.`; },
  });
  render();
}

function sendTransfer(event) {
  event.preventDefault();
  const account = ledger.accounts.get(me?.code);
  if (!account) return;
  const to = ($('to').value || $('to-code').value.trim().toUpperCase().replace(/^BRV-/, ''));
  const amount = Number($('amount').value.replaceAll(',', ''));
  const memo = $('memo').value.trim().slice(0, 40);
  const feedback = $('transfer-feedback');
  if (!/^[A-HJ-NP-Z2-9]{6}$/.test(to)) { feedback.textContent = '받는 사람을 고르거나 계좌번호(BRV-XXXXXX)를 입력해 주세요.'; return; }
  if (to === me.code) { feedback.textContent = '내 계좌로는 보낼 수 없습니다.'; return; }
  if (!Number.isSafeInteger(amount) || amount < 1 || amount > MAX_AMOUNT) { feedback.textContent = '금액은 1P 이상 1,000,000P 이하로 입력해 주세요.'; return; }
  if (amount > account.balance - reserved()) { feedback.textContent = '잔액이 부족합니다.'; return; }
  const n = hooks.uuid();
  const transfer = { n, from: me.code, to, amount, memo };
  hooks.enqueue({
    kind: 'ledger', transfer, key: n, author: me.name, container: $('ledger-outbox'),
    body: encodeTransfer({ ...transfer, fromName: me.name, toName: ledger.accounts.get(to)?.name }),
    summary: `송금 ${points(amount)} → ${ledger.name(to)}${memo ? ` · ${memo}` : ''}`,
    onSaved: (saved, ack) => {
      acknowledged(saved, ack);
      feedback.textContent = `${ledger.name(to)}에게 ${points(amount)}를 보냈습니다 · 거래 #${saved.id}`;
    },
  });
  $('amount').value = ''; $('memo').value = ''; feedback.textContent = '';
  render();
}

// Re-read my rows from whichever environment now serves this page. This is a
// direct check against the current database, not the local copy of the ledger.
export async function verify(reason = '') {
  if (!me) return;
  const account = ledger.accounts.get(me.code);
  if (!account) return;
  const rows = [{ id: account.openedId, check: (e) => e.t === 'open' && e.acct === me.code }]
    .concat(ledger.history(me.code).slice(0, 20).map((t) => ({ id: t.id, check: (e) => e.t === 'transfer' && e.n === t.n && e.amount === t.amount })));
  const result = $('verify-result');
  result.dataset.state = 'running'; result.textContent = `${reason}현재 연결된 DB에서 내 기록 ${rows.length}건을 다시 읽는 중…`;
  let ok = 0, env = null;
  try {
    for (const { id, check } of rows) {
      const response = await fetch(`/api/messages?after=${id - 1}`);
      if (!response.ok) throw new Error('unavailable');
      env = response.headers.get('X-Bravo-Env') || env;
      const [first] = await response.json();
      const event = first && first.id === id ? decode(first.body) : null;
      if (event && !event.invalid && check(event)) ok++;
    }
  } catch {
    result.dataset.state = 'error'; result.textContent = '지금은 DB를 다시 읽을 수 없습니다. 연결이 돌아오면 다시 확인해 주세요.'; return;
  }
  const where = env ? `${envLabel(env)} 환경` : '현재 환경';
  result.dataset.state = ok === rows.length ? 'ok' : 'error';
  result.textContent = ok === rows.length
    ? `${reason}${where}의 DB에서 계좌 개설과 거래 ${rows.length - 1}건을 모두 그대로 확인했습니다 · ${clockFormat.format(Date.now())}`
    : `${where}의 DB에서 ${rows.length}건 중 ${ok}건만 확인됐습니다. 운영자에게 알려 주세요.`;
}

export function environmentChanged(from, to) {
  clearTimeout(verifyTimer);
  verifyTimer = setTimeout(() => verify(`${envLabel(from)} → ${envLabel(to)} 전환 뒤 자동 확인 · `), 1500);
}

export function init(options) {
  hooks = options;
  $('open-account').onclick = openAccount;
  $('open-name').onkeydown = (event) => { if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); openAccount(); } };
  $('transfer-form').onsubmit = sendTransfer;
  $('to').onchange = () => { $('to-code-row').hidden = $('to').value !== ''; };
  for (const chip of document.querySelectorAll('[data-amount]')) {
    chip.onclick = () => { $('amount').value = Number(chip.dataset.amount).toLocaleString('ko-KR'); };
  }
  $('verify').onclick = () => verify();
  $('copy-code').onclick = async () => {
    try { await navigator.clipboard.writeText(`BRV-${me.code}`); $('transfer-feedback').textContent = '내 계좌번호를 복사했습니다.'; }
    catch { $('transfer-feedback').textContent = `내 계좌번호: BRV-${me?.code}`; }
  };
  try { $('open-name').value = $('name').value || ''; } catch { /* ignore */ }
  render();
}

export { render as renderWallet };
