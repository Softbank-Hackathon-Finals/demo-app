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
  $('open-send').disabled = $('copy-code-2').disabled = !account;
  $('history-card').hidden = !me;
  if (!me) return;
  $('account-name').textContent = me.name;
  $('account-code').textContent = `BRV-${me.code}`;
  if (!account) {
    $('balance').textContent = opening ? '만드는 중' : '확인 중'; delete $('balance').dataset.value;
    $('recent-card').hidden = true;
    $('balance-note').textContent = opening ? '계좌를 만들고 있어요.' : '장부에서 계좌를 찾고 있어요.';
    return;
  }
  const hold = reserved();
  setBalance(account.balance - hold);
  $('balance-note').textContent = hold ? `처리 대기 송금 ${points(hold)} 제외 · 장부 잔액 ${points(account.balance)}` : '사용 가능 포인트';
  renderRecent();
  if (flow?.step === 'to') renderToList();
  if (flow?.step === 'amount') renderAmount();
  renderHistory();
}

// Count toward the new balance instead of jumping; the final text is exact.
let balanceFrame;
const still = matchMedia('(prefers-reduced-motion: reduce)');
function setBalance(value) {
  const el = $('balance'), from = Number(el.dataset.value);
  el.dataset.value = String(value);
  cancelAnimationFrame(balanceFrame);
  if (!Number.isFinite(from) || from === value || still.matches) { el.textContent = points(value); return; }
  const start = performance.now(), span = 450;
  const step = (now) => {
    const t = Math.min(1, (now - start) / span), eased = 1 - (1 - t) ** 3;
    el.textContent = points(Math.round(from + (value - from) * eased));
    if (t < 1) balanceFrame = requestAnimationFrame(step);
  };
  balanceFrame = requestAnimationFrame(step);
}

function recentCodes() {
  const seen = [];
  for (const t of ledger.history(me.code)) if (t.from === me.code && !seen.includes(t.to)) seen.push(t.to);
  return seen.slice(0, 8);
}

function renderRecent() {
  const codes = recentCodes();
  $('recent-card').hidden = codes.length === 0;
  $('recent').replaceChildren(...codes.map((code) => {
    const li = document.createElement('li');
    const button = document.createElement('button'); button.type = 'button'; button.dataset.code = code;
    const name = document.createElement('span'); name.textContent = ledger.name(code);
    button.append(avatar(ledger.name(code)), name);
    button.onclick = () => { openSheet(); chooseRecipient(code); };
    li.append(button); return li;
  }));
}

let toastTimer;
export function toast(message) {
  const el = $('toast'); el.textContent = message; el.hidden = false;
  el.classList.remove('show'); void el.offsetWidth; el.classList.add('show');
  clearTimeout(toastTimer); toastTimer = setTimeout(() => { el.classList.remove('show'); el.hidden = true; }, 2600);
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
    const li = document.createElement('li'); li.className = 'placeholder'; li.textContent = '아직 거래가 없어요. 첫 송금을 보내 보세요.'; items.push(li);
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
    onSaved: (saved, ack) => { opening = false; acknowledged(saved, ack); toast(`계좌를 만들었어요 · ${points(GRANT)}가 들어왔어요`); },
  });
  render();
}

// --- Transfer sheet: recipient → amount → confirm → result -------------------

let flow = null; // { step, to, digits, n, entry }
let mirror;
const CODE = /^[A-HJ-NP-Z2-9]{6}$/;

function hue(text) { let h = 0; for (const ch of text) h = (h * 31 + ch.codePointAt(0)) % 360; return h; }
function avatar(name, className = 'avatar') {
  const el = document.createElement('span'); el.className = className; el.setAttribute('aria-hidden', 'true');
  el.textContent = [...name.trim()][0] || '?'; el.style.setProperty('--hue', String(hue(name))); return el;
}

export function koreanAmount(value) {
  const man = Math.floor(value / 10000), rest = value % 10000;
  const parts = [man ? `${man.toLocaleString('ko-KR')}만` : '', rest ? rest.toLocaleString('ko-KR') : ''].filter(Boolean);
  return `${parts.join(' ') || '0'}P`;
}

function available() {
  const account = ledger.accounts.get(me?.code);
  return account ? account.balance - reserved() : 0;
}

const ORDER = ['to', 'amount', 'confirm', 'done'];
function showStep(step) {
  $('send-sheet').dataset.dir = ORDER.indexOf(step) >= ORDER.indexOf(flow.step) ? 'forward' : 'back';
  flow.step = step;
  for (const name of ['to', 'amount', 'confirm', 'done']) $(`step-${name}`).hidden = name !== step;
  $('sheet-back').hidden = step === 'to' || step === 'done';
  $('send-sheet').dataset.step = step;
}

export function openSheet() {
  if (!ledger.accounts.get(me?.code)) return;
  clearInterval(mirror);
  flow = { step: 'to', to: null, digits: '' };
  $('to-search').value = ''; renderToList(); showStep('to');
  $('send-sheet').showModal();
  if (!matchMedia('(pointer: coarse)').matches) $('to-search').focus();
}

function recipientButton(code, title, subtitle, initial) {
  const li = document.createElement('li');
  const button = document.createElement('button'); button.type = 'button'; button.dataset.code = code;
  const text = document.createElement('span'); text.className = 'to-text';
  const name = document.createElement('strong'); name.textContent = title;
  const sub = document.createElement('small'); sub.textContent = subtitle;
  text.append(name, sub); button.append(avatar(initial), text);
  button.onclick = () => chooseRecipient(code); li.append(button); return li;
}

function renderToList() {
  const raw = $('to-search').value.trim();
  const query = raw.toUpperCase().replace(/^BRV-?/, '');
  const items = [...ledger.accounts.values()].filter((a) => a.code !== me.code)
    .filter((a) => !raw || a.name.includes(raw) || a.code.includes(query))
    .sort((a, b) => b.openedId - a.openedId).slice(0, 50)
    .map((a) => recipientButton(a.code, a.name, `Bravo 포인트 BRV-${a.code}`, a.name));
  if (CODE.test(query) && query !== me.code && !ledger.accounts.has(query)) {
    items.push(recipientButton(query, `BRV-${query}`, '이 화면에서 아직 확인하지 못한 계좌로 보내기', '#'));
  }
  if (!items.length) {
    const li = document.createElement('li'); li.className = 'placeholder';
    li.textContent = raw ? '찾는 사람이 없어요. 계좌번호 6자리를 그대로 입력해 보세요.' : '아직 다른 참여자가 없어요. 옆 사람에게 QR을 보여 주세요.';
    items.push(li);
  }
  $('to-list').replaceChildren(...items);
  const recent = raw ? [] : recentCodes().map((code) => recipientButton(code, ledger.name(code), `Bravo 포인트 BRV-${code}`, ledger.name(code)));
  $('to-recent-wrap').hidden = recent.length === 0;
  $('to-recent').replaceChildren(...recent);
}

function chooseRecipient(code) {
  flow.to = code; flow.digits = '';
  $('amount-to').textContent = ledger.name(code);
  showStep('amount'); renderAmount();
}

function renderAmount() {
  const amount = Number(flow.digits || 0), limit = available();
  const display = $('amount-display'), hint = $('amount-hint');
  display.dataset.empty = String(!flow.digits);
  const text = flow.digits ? points(amount) : '얼마를 보낼까요?';
  if (display.textContent !== text && flow.digits) { display.classList.remove('bump'); void display.offsetWidth; display.classList.add('bump'); }
  display.textContent = text;
  hint.dataset.state = '';
  if (amount > MAX_AMOUNT) { hint.textContent = `한 번에 ${points(MAX_AMOUNT)}까지 보낼 수 있어요`; hint.dataset.state = 'error'; }
  else if (amount > limit) { hint.textContent = `잔액이 부족해요 · 보낼 수 있는 금액 ${points(limit)}`; hint.dataset.state = 'error'; }
  else hint.textContent = amount ? koreanAmount(amount) : `잔액 ${points(limit)}`;
  $('amount-next').disabled = !(amount >= 1 && amount <= limit && amount <= MAX_AMOUNT);
}

function press(key) {
  if (key === 'back') flow.digits = flow.digits.slice(0, -1);
  else if (!(flow.digits === '' && key.startsWith('0')) && flow.digits.length + key.length <= 7) flow.digits += key;
  renderAmount();
}

function add(value) {
  const next = value === 'all' ? available() : Number(flow.digits || 0) + Number(value);
  flow.digits = next > 0 ? String(Math.min(next, 9999999)) : '';
  renderAmount();
}

function confirmStep() {
  if ($('amount-next').disabled) return;
  const amount = Number(flow.digits), name = ledger.name(flow.to);
  $('confirm-avatar').replaceChildren(avatar(name, 'big-avatar-inner'));
  $('confirm-to').textContent = name; $('confirm-amount').textContent = points(amount);
  $('confirm-code').textContent = `BRV-${flow.to}`;
  $('confirm-from').textContent = `${me.name} · BRV-${me.code}`;
  $('memo').value = ''; showStep('confirm');
}

function doneView(state, title, status, receipt = null) {
  $('done-mark').dataset.state = state; $('done-title').textContent = title; $('done-status').textContent = status;
  flow.receipt = receipt;
  $('done-share').disabled = !receipt;
}

async function shareReceipt() {
  if (!flow?.receipt) return;
  const text = flow.receipt;
  try {
    if (navigator.share) { await navigator.share({ text }); return; }
    await navigator.clipboard.writeText(text); toast('송금 내용을 복사했어요');
  } catch { toast('공유를 마치지 못했어요'); }
}

function sendTransfer() {
  const amount = Number(flow.digits), to = flow.to;
  if (!(amount >= 1 && amount <= available() && amount <= MAX_AMOUNT) || !CODE.test(to) || to === me.code) {
    showStep('amount'); renderAmount(); return;
  }
  const memo = $('memo').value.trim().slice(0, 40);
  const n = hooks.uuid(), name = ledger.name(to), current = flow;
  const transfer = { n, from: me.code, to, amount, memo };
  current.n = n;
  showStep('done');
  doneView('pending', `${name}님에게\n${points(amount)}를 보내고 있어요`, '처리 중이에요');
  current.entry = hooks.enqueue({
    kind: 'ledger', transfer, key: n, author: me.name, container: $('ledger-outbox'),
    body: encodeTransfer({ ...transfer, fromName: me.name, toName: ledger.accounts.get(to)?.name }),
    summary: `송금 ${points(amount)} → ${name}${memo ? ` · ${memo}` : ''}`,
    onSaved: (saved, ack, entry) => {
      acknowledged(saved, ack);
      if (flow !== current) toast(`${name}님에게 ${points(amount)}를 보냈어요`);
      if (flow !== current) return;
      clearInterval(mirror);
      const where = ack?.env ? `${envLabel(ack.env)}에서 처리` : '처리 완료';
      const retried = entry.attempts > 1 ? ` · ${entry.attempts}번 시도했지만 출금은 한 번만 됐어요` : '';
      doneView('ok', `${name}님에게\n${points(amount)}를 보냈어요`, `거래 #${saved.id} · ${where}${retried}`,
        `Bravo 뱅크 모의 송금 · ${name}님에게 ${points(amount)} · 거래 #${saved.id}`);
    },
    onFailed: (code, permanent) => {
      if (flow !== current) return;
      clearInterval(mirror);
      doneView('error', permanent ? '송금이 거절됐어요' : '송금 결과를 아직 확인하지 못했어요',
        permanent ? '입력을 확인하고 새로 보내 주세요.'
          : '이미 처리됐을 수 있어요. 거래 내역을 확인하거나 처리 대기 목록에서 같은 요청으로 다시 시도해 주세요.');
    },
  });
  // While the platform pauses writes, mirror the queue's own retry state.
  clearInterval(mirror);
  mirror = setInterval(() => {
    if (flow !== current || $('done-mark').dataset.state !== 'pending') { clearInterval(mirror); return; }
    if ((current.entry?.state?.textContent || '').includes('재시도')) {
      $('done-status').textContent = '시스템 전환 중이라 잠시 기다리고 있어요 · 같은 거래 번호로 다시 보내므로 출금은 한 번만 돼요';
    }
  }, 300);
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
  result.dataset.state = 'running'; result.textContent = `${reason}지금 연결된 DB에서 내 기록 ${rows.length}건을 다시 읽고 있어요…`;
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
    result.dataset.state = 'error'; result.textContent = '지금은 DB를 다시 읽을 수 없어요. 연결이 돌아오면 다시 확인해 주세요.'; return;
  }
  const where = env ? `${envLabel(env)} 환경` : '현재 환경';
  result.dataset.state = ok === rows.length ? 'ok' : 'error';
  result.textContent = ok === rows.length
    ? `${reason}${where}의 DB에서 계좌 개설과 거래 ${rows.length - 1}건을 모두 그대로 확인했어요 · ${clockFormat.format(Date.now())}`
    : `${where}의 DB에서 ${rows.length}건 중 ${ok}건만 확인됐어요. 운영자에게 알려 주세요.`;
}

export function environmentChanged(from, to) {
  clearTimeout(verifyTimer);
  verifyTimer = setTimeout(() => verify(`${envLabel(from)} → ${envLabel(to)} 전환 뒤 자동 확인 · `), 1500);
}

export function init(options) {
  hooks = options;
  $('open-account').onclick = openAccount;
  $('open-name').onkeydown = (event) => { if (event.key === 'Enter' && !event.isComposing) { event.preventDefault(); openAccount(); } };
  $('open-send').onclick = openSheet;
  $('to-search').oninput = renderToList;
  for (const key of document.querySelectorAll('[data-key]')) key.onclick = () => press(key.dataset.key);
  for (const chip of document.querySelectorAll('[data-add]')) chip.onclick = () => add(chip.dataset.add);
  $('amount-next').onclick = confirmStep;
  $('transfer-send').onclick = sendTransfer;
  $('done-close').onclick = () => $('send-sheet').close();
  $('done-share').onclick = shareReceipt;
  $('done-history').onclick = () => { $('send-sheet').close(); hooks.showTab('history'); };
  $('sheet-close').onclick = () => $('send-sheet').close();
  $('sheet-back').onclick = () => {
    if (flow.step === 'amount') showStep('to');
    else if (flow.step === 'confirm') { showStep('amount'); renderAmount(); }
  };
  $('send-sheet').addEventListener('close', () => { clearInterval(mirror); flow = null; });
  $('send-sheet').onkeydown = (event) => {
    if (flow?.step !== 'amount' || event.target.tagName === 'INPUT') return;
    if (/^[0-9]$/.test(event.key)) { event.preventDefault(); press(event.key); }
    else if (event.key === 'Backspace') { event.preventDefault(); press('back'); }
    else if (event.key === 'Enter') { event.preventDefault(); confirmStep(); }
  };
  $('verify').onclick = () => verify();
  $('copy-code').onclick = $('copy-code-2').onclick = async () => {
    try { await navigator.clipboard.writeText(`BRV-${me.code}`); toast('내 계좌번호를 복사했어요'); }
    catch { toast(`내 계좌번호 BRV-${me?.code}`); }
  };
  try { $('open-name').value = $('name').value || ''; } catch { /* ignore */ }
  render();
}

export { render as renderWallet };
