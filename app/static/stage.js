// Read-only presenter view. It never writes: it follows the same WebSocket
// stream as participants and recomputes the ledger, so the big screen proves
// conservation and no duplicates with the platform's own propagation path.
import { qrcode } from './vendor/qrcode.mjs';
import { MessageCursor, shareAddress, validateMessage } from './transport.js';
import { Continuity, duration, envKey, envLabel } from './observe.js';
import { GRANT, Ledger, points } from './ledger.js';

const $ = (id) => document.getElementById(id);
const KEY = 'bravo-stage-v1';
const clock = new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
const cursor = new MessageCursor();
const continuity = new Continuity(14);
let ledger = new Ledger();
let path = [], load = 0, socket, reconnectTimer, attempt = 0, saveTimer;
const loadTimes = [];

function save() {
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    try { localStorage.setItem(KEY, JSON.stringify({ cursor: cursor.value, ledger: ledger.snapshot(), path, load })); } catch { /* ignore */ }
  }, 400);
}

function restore() {
  try {
    const state = JSON.parse(localStorage.getItem(KEY));
    if (!state || !Number.isSafeInteger(state.cursor)) return null;
    ledger = Ledger.restore(state.ledger);
    path = Array.isArray(state.path) ? state.path.filter((e) => typeof e === 'string').slice(-8) : [];
    load = Number.isSafeInteger(state.load) ? state.load : 0;
    return state.cursor;
  } catch { return null; }
}

function ingest(rows, env) {
  const now = Date.now();
  for (const row of rows) {
    if (row.author.startsWith('loadgen-')) { load++; loadTimes.push(now); continue; }
    ledger.ingest(row, env);
  }
  save(); render();
}

function render() {
  const transfers = [...ledger.transfers.values()];
  const expected = ledger.accounts.size * GRANT, total = ledger.total() + ledger.external;
  $('total').textContent = points(total);
  const ok = ledger.conserved();
  $('invariant').dataset.state = ok ? 'ok' : 'error';
  $('invariant').textContent = ok
    ? `개설 계좌 ${ledger.accounts.size}개 × ${points(GRANT)} = ${points(expected)}와 일치`
    : `기대값 ${points(expected)}와 다름 · 중복 ${ledger.duplicates}건`;
  $('k-accounts').textContent = ledger.accounts.size.toLocaleString();
  $('k-transfers').textContent = `${transfers.length.toLocaleString()}건`;
  $('k-volume').textContent = points(transfers.reduce((sum, t) => sum + t.amount, 0));
  $('k-duplicates').textContent = `${ledger.duplicates}건`;
  $('k-duplicates').parentElement.dataset.state = ledger.duplicates ? 'error' : 'ok';
  $('k-load').textContent = `${load.toLocaleString()}건`;
  const items = transfers.sort((a, b) => b.id - a.id).slice(0, 12).map((t) => {
    const li = document.createElement('li');
    const who = document.createElement('span'); who.className = 'who'; who.textContent = `${ledger.name(t.from)} → ${ledger.name(t.to)}`;
    const amount = document.createElement('strong'); amount.textContent = points(t.amount);
    const env = document.createElement('span'); env.className = 'served'; env.dataset.env = envKey(t.env); env.textContent = t.env ? envLabel(t.env) : '미확인';
    const id = document.createElement('small'); id.textContent = `#${t.id}`;
    li.append(who, amount, env, id); return li;
  });
  if (!items.length) { const li = document.createElement('li'); li.className = 'placeholder'; li.textContent = '참여자가 송금하면 여기에 바로 나타납니다.'; items.push(li); }
  $('feed').replaceChildren(...items);
  renderContinuity();
}

function renderContinuity() {
  const now = Date.now();
  while (loadTimes.length && now - loadTimes[0] > 10000) loadTimes.shift();
  $('k-rate').textContent = loadTimes.length ? `최근 10초 초당 ${(loadTimes.length / 10).toFixed(1)}건` : '';
  $('k-blocked').textContent = continuity.blockedMs(now) ? duration(continuity.blockedMs(now)) : '-';
  $('k-gap').textContent = continuity.lastGapMs === null ? '-' : duration(continuity.lastGapMs);
  $('k-reconnects').textContent = `${continuity.reconnects}회`;
  const events = continuity.events.filter((e) => e.kind !== 'retry').map((e) => {
    const li = document.createElement('li'); li.dataset.kind = e.kind;
    const time = document.createElement('time'); time.textContent = clock.format(e.at);
    const text = document.createElement('p'); text.textContent = e.text;
    li.append(time, text); return li;
  });
  if (!events.length) { const li = document.createElement('li'); li.className = 'placeholder'; li.textContent = '아직 기록이 없습니다.'; events.push(li); }
  $('timeline').replaceChildren(...events);
}
setInterval(renderContinuity, 1000);

function status(data, hello) {
  if (typeof data.env !== 'string' || typeof data.write_blocked !== 'boolean') throw new Error('invalid_status');
  const now = Date.now();
  if (hello) continuity.connected(data.env, data.write_blocked, now); else continuity.status(data.env, data.write_blocked, now);
  if (path[path.length - 1] !== data.env) { path.push(data.env); path = path.slice(-8); save(); }
  $('env').textContent = envLabel(data.env);
  $('env-card').dataset.env = envKey(data.env);
  $('path').replaceChildren(...path.map((env, i) => {
    const li = document.createElement('li'); li.dataset.env = envKey(env); li.textContent = envLabel(env);
    if (i === path.length - 1) li.className = 'current';
    return li;
  }));
  $('write-state').dataset.state = data.write_blocked ? 'blocked' : 'open';
  $('write-state').textContent = data.write_blocked ? '쓰기 일시 정지 · 읽기와 연결은 유지' : '쓰기 정상';
  renderContinuity();
}

function connect() {
  clearTimeout(reconnectTimer);
  $('conn').textContent = '재연결 중'; $('conn').className = 'connection';
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws${cursor.value === null ? '' : `?after=${cursor.value}`}`);
  socket = ws;
  ws.onmessage = (event) => {
    if (socket !== ws) return;
    try {
      if (typeof event.data !== 'string' || event.data.length > 2 * 1024 * 1024) throw new Error('invalid_frame');
      const data = JSON.parse(event.data);
      if (data.type === 'hello') { status(data, true); attempt = 0; $('conn').textContent = '연결됨'; $('conn').className = 'connection online'; }
      else if (data.type === 'status') status(data, false);
      else if (data.type === 'messages') ingest(cursor.accept(data.items), continuity.env);
      else throw new Error('unknown_frame');
    } catch { ws.close(); }
  };
  ws.onerror = () => ws.close();
  ws.onclose = (event) => {
    if (socket !== ws) return;
    continuity.dropped(event.code, Date.now()); renderContinuity();
    $('conn').textContent = '재연결 중'; $('conn').className = 'connection';
    reconnectTimer = setTimeout(connect, Math.min(500 * 2 ** Math.min(attempt++, 4), 5000) + Math.random() * 200);
  };
}

function showQr() {
  const address = shareAddress(location);
  $('join-url').textContent = address;
  try {
    const code = qrcode(0, 'M'); code.addData(address); code.make();
    const count = code.getModuleCount(), scale = Math.max(1, Math.floor(220 / (count + 8)));
    const canvas = $('qr'); canvas.width = canvas.height = (count + 8) * scale;
    const ctx = canvas.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height); ctx.fillStyle = '#0f2a3a';
    for (let y = 0; y < count; y++) for (let x = 0; x < count; x++) if (code.isDark(y, x)) ctx.fillRect((x + 4) * scale, (y + 4) * scale, scale, scale);
  } catch { $('qr').hidden = true; }
}

async function startup() {
  let saved = restore();
  try {
    const response = await fetch('/api/messages', { signal: AbortSignal.timeout(3000) });
    if (!response.ok) throw new Error('unavailable');
    const page = await response.json();
    if (!Array.isArray(page)) throw new Error('invalid');
    for (const m of page) validateMessage(m);
    const latest = page.length ? page[page.length - 1].id : 0;
    if (saved !== null && saved > latest) { ledger = new Ledger(); path = []; load = 0; saved = null; }
    if (saved === null) ingest(cursor.accept(page), response.headers.get('X-Bravo-Env'));
    else cursor.value = saved;
  } catch {
    if (saved !== null) cursor.value = saved;
  }
  render();
}

showQr();
await startup();
connect();
