import { qrcode } from './vendor/qrcode.mjs';
import { MessageCursor, deliver, validateMessage, shareAddress } from './transport.js';
import { Continuity, duration, envKey, envLabel } from './observe.js';
import * as wallet from './wallet.js';

const $ = (id) => document.getElementById(id);
const cursor = new MessageCursor();
const continuity = new Continuity();
const rendered = new Map();
const pending = new Set();
const rasterTypes = new Set(['image/png', 'image/jpeg', 'image/gif', 'image/webp']);
const timeFormat = new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit' });
const clockFormat = new Intl.DateTimeFormat('ko-KR', { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false });
let renderedFloor = 0, socket, reconnectTimer, handshakeTimer, reconnectAttempt = 0;
let connected = false, blocked = false, previewUrl, stopping = false;
let uploadsEnabled = false, switchFlash;
const systemLines = [];
const coarsePointer = matchMedia('(pointer: coarse)');

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 15) | 64; b[8] = (b[8] & 63) | 128;
  const h = [...b].map((n) => n.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0,8)}-${h.slice(8,12)}-${h.slice(12,16)}-${h.slice(16,20)}-${h.slice(20)}`;
}

try { $('name').value = sessionStorage.getItem('bravo-alias') || ''; } catch { /* Private browsing. */ }

// --- Continuity panel -------------------------------------------------------

function record(events) {
  for (const event of [events].flat()) {
    if (!event) continue;
    if (event.kind === 'switch') systemLine(`처리 환경이 바뀌었습니다 · ${event.text.split(' · ')[1]} · 대화는 그대로 이어집니다`, 'switch');
  }
  renderTimeline(); renderStats();
}

function renderTimeline() {
  const items = continuity.events.map((event) => {
    const li = document.createElement('li'); li.dataset.kind = event.kind;
    const time = document.createElement('time'); time.dateTime = new Date(event.at).toISOString();
    time.textContent = clockFormat.format(event.at);
    const text = document.createElement('p'); text.textContent = event.text;
    li.append(time, text); return li;
  });
  if (!items.length) {
    const li = document.createElement('li'); li.className = 'placeholder'; li.textContent = '아직 기록이 없습니다.'; items.push(li);
  }
  $('timeline').replaceChildren(...items);
}

function renderStats() {
  $('stat-reconnects').textContent = `${continuity.reconnects}회`;
  $('stat-gap').textContent = continuity.lastGapMs === null ? '-' : duration(continuity.lastGapMs);
  const spent = continuity.blockedMs(Date.now());
  $('stat-blocked').textContent = spent ? duration(spent) : '-';
  $('stat-retried').textContent = `${continuity.retried}건`;
}
setInterval(() => { if (continuity.blocked) renderStats(); }, 500);

function renderSplit() {
  const counts = { aws: 0, onprem: 0, unknown: 0 };
  for (const el of rendered.values()) counts[el.dataset.env] = (counts[el.dataset.env] || 0) + 1;
  const total = rendered.size;
  $('split-total').textContent = `화면의 ${total.toLocaleString()}건`;
  const parts = [], legend = [];
  for (const [env, label] of [['aws', 'AWS'], ['onprem', '온프렘'], ['unknown', '미확인']]) {
    if (!counts[env]) continue;
    const bar = document.createElement('span'); bar.dataset.env = env; bar.style.width = `${(counts[env] / total) * 100}%`;
    parts.push(bar);
    const li = document.createElement('li'); li.dataset.env = env; li.textContent = `${label} ${counts[env]}건`; legend.push(li);
  }
  $('split-bar').replaceChildren(...parts);
  $('split-bar').setAttribute('aria-label', legend.length ? legend.map((li) => li.textContent).join(', ') : '표시된 메시지 없음');
  $('split-legend').replaceChildren(...legend);
}

function systemLine(text, kind) {
  const line = document.createElement('div'); line.className = `system ${kind}`; line.textContent = text;
  const log = $('log'); const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 70;
  log.append(line); systemLines.push(line); $('empty').hidden = true;
  if (systemLines.length > 20) systemLines.shift().remove();
  if (stick) log.scrollTop = log.scrollHeight;
}

$('panel-toggle').onclick = () => {
  const open = !document.body.classList.contains('panel-open');
  document.body.classList.toggle('panel-open', open); $('panel-toggle').setAttribute('aria-expanded', String(open));
};
function showTab(tab) {
  document.body.dataset.tab = tab;
  for (const button of document.querySelectorAll('.tabs button')) button.setAttribute('aria-pressed', String(button.dataset.tab === tab));
}
for (const button of document.querySelectorAll('.tabs button')) button.onclick = () => showTab(button.dataset.tab);
$('panel-close').onclick = () => { document.body.classList.remove('panel-open'); $('panel-toggle').setAttribute('aria-expanded', 'false'); };

// --- Connection and status --------------------------------------------------

function connectionState(online) {
  connected = online;
  $('conn').textContent = online ? '연결됨' : navigator.onLine ? '재연결 중' : '오프라인';
  $('conn').className = `connection${online ? ' online' : ''}`;
  $('environment-note').textContent = online ? '연결된 서버가 알려 준 실행 환경입니다.' : '마지막으로 확인한 환경입니다. 다시 연결하는 중입니다.';
  notice();
}

function notice() {
  const text = !connected ? '연결을 다시 확인하고 있습니다. 대기 중인 메시지는 이 탭에 남아 있습니다.' :
    blocked ? '현재 연결된 앱의 쓰기가 잠시 제한되었습니다. 같은 요청으로 재전송합니다. 메시지 읽기는 계속됩니다.' : '';
  $('banner').textContent = text; $('banner').hidden = !text;
  const walletText = !connected ? '연결을 다시 확인하고 있습니다. 잔액과 처리 대기 중인 송금은 이 기기에 남아 있습니다.' :
    blocked ? '시스템 전환 중이라 송금이 잠시 대기합니다. 같은 거래 번호로 자동 재시도하며 출금은 한 번만 됩니다.' : '';
  $('wallet-notice').textContent = walletText; $('wallet-notice').hidden = !walletText;
}

function status(data, hello = false) {
  if (typeof data.env !== 'string' || typeof data.write_blocked !== 'boolean') throw new Error('invalid_status');
  const key = envKey(data.env), label = envLabel(data.env), previous = continuity.env;
  $('env').textContent = label; $('env-pill-text').textContent = label;
  $('env-pill').dataset.env = key; $('env-card').dataset.env = key;
  for (const step of document.querySelectorAll('.route li[data-route]')) step.classList.toggle('active', step.dataset.route === key);
  blocked = data.write_blocked; notice();
  record(hello ? continuity.connected(data.env, blocked, Date.now()) : continuity.status(data.env, blocked, Date.now()));
  if (previous !== null && previous !== data.env) {
    wallet.environmentChanged(previous, data.env);
    clearTimeout(switchFlash); $('env-card').classList.remove('switched'); void $('env-card').offsetWidth;
    $('env-card').classList.add('switched'); switchFlash = setTimeout(() => $('env-card').classList.remove('switched'), 3500);
  }
  // An old or incomplete status never grants a new upload capability.
  uploadsEnabled = data.attachment_upload_enabled === true;
  $('file').disabled = !uploadsEnabled;
  $('upload-policy').textContent = uploadsEnabled ? '' : '이전 준비 중에는 새 첨부파일을 올릴 수 없습니다. 메시지와 기존 첨부 조회는 이용할 수 있습니다.';
  $('upload-policy').hidden = uploadsEnabled;
  if (!uploadsEnabled && $('file').files.length) {
    $('file').value = ''; clearPreview();
  }
}

// --- Messages ---------------------------------------------------------------

function hue(text) {
  let h = 0; for (const ch of text) h = (h * 31 + ch.codePointAt(0)) % 360;
  return h;
}

// `via` is what this browser observed: the app that acknowledged our POST, or the
// app whose WebSocket delivered the row. The database stores no environment.
function messageElement(m, via) {
  const el = document.createElement('article'); el.className = `msg${m.author === $('name').value.trim() ? ' mine' : ''}`;
  el.dataset.messageId = String(m.id);
  const served = envKey(via.env); el.dataset.env = served;
  const avatar = document.createElement('span'); avatar.className = 'avatar'; avatar.setAttribute('aria-hidden', 'true');
  avatar.textContent = [...m.author.trim()][0] || '?'; avatar.style.setProperty('--hue', String(hue(m.author)));
  const content = document.createElement('div'); content.className = 'content';
  const meta = document.createElement('div'); meta.className = 'meta';
  const author = document.createElement('span'); author.className = 'author'; author.textContent = m.author;
  const time = document.createElement('time'); time.dateTime = m.created_at; time.textContent = timeFormat.format(new Date(m.created_at));
  const env = document.createElement('span'); env.className = 'served'; env.dataset.env = served;
  env.textContent = served === 'unknown' ? '환경 미확인' : `${envLabel(via.env)} ${via.saved ? '저장' : '경유'}`;
  env.title = via.saved ? '이 메시지를 새로 저장했다고 응답한 앱의 환경' : '이 브라우저에 메시지를 전달한 앱의 환경';
  const id = document.createElement('span'); id.className = 'mid'; id.textContent = `#${m.id}`;
  meta.append(author, time, env, id);
  const bubble = document.createElement('div'); bubble.className = 'bubble'; bubble.textContent = m.body;
  if (m.attachment_id) {
    const url = `/api/attachments/${encodeURIComponent(m.attachment_id)}`;
    const filename = typeof m.attachment_name === 'string' ? m.attachment_name.slice(0, 200) : '첨부파일';
    if (rasterTypes.has(m.attachment_type)) {
      const image = document.createElement('img'); image.src = url; image.alt = filename; image.loading = 'lazy'; bubble.append(image);
    }
    const link = document.createElement('a'); link.href = url; link.textContent = `첨부 · ${filename}`;
    link.target = '_blank'; link.rel = 'noopener noreferrer'; bubble.append(link);
  }
  content.append(meta, bubble); el.append(avatar, content); return el;
}

function append(items, via) {
  const log = $('log'); const stick = log.scrollHeight - log.scrollTop - log.clientHeight < 70;
  let added = 0;
  for (const m of items) {
    validateMessage(m);
    if (rendered.has(m.id) && via.saved) {
      // The stream can deliver our row before the POST acknowledgement arrives.
      const fresh = messageElement(m, via); rendered.get(m.id).replaceWith(fresh); rendered.set(m.id, fresh); added++;
      continue;
    }
    if (rendered.has(m.id) || m.id <= renderedFloor) continue;
    $('empty').hidden = true;
    const el = messageElement(m, via);
    const next = [...rendered.keys()].filter((id) => id > m.id).sort((a,b) => a-b)[0];
    log.insertBefore(el, next ? rendered.get(next) : null); rendered.set(m.id, el); added++;
    if (rendered.size > 500) {
      const oldest = Math.min(...rendered.keys()); rendered.get(oldest).remove(); rendered.delete(oldest); renderedFloor = oldest;
    }
  }
  if (added) renderSplit();
  if (stick) log.scrollTop = log.scrollHeight;
  else if (added) $('jump').hidden = false;
}
$('jump').onclick = () => { $('log').scrollTop = $('log').scrollHeight; $('jump').hidden = true; };
$('log').onscroll = () => {
  if ($('log').scrollHeight - $('log').scrollTop - $('log').clientHeight < 70) $('jump').hidden = true;
};

function connect() {
  if (stopping) return;
  clearTimeout(reconnectTimer); clearTimeout(handshakeTimer);
  connectionState(false);
  if (!navigator.onLine) { connectionState(false); return; }
  const previous = socket;
  socket = null; previous?.close();
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(`${proto}//${location.host}/ws${cursor.value === null ? '' : `?after=${cursor.value}`}`);
  socket = ws;
  handshakeTimer = setTimeout(() => { if (socket === ws) ws.close(); }, 10000);
  ws.onmessage = (event) => {
    if (socket !== ws) return;
    try {
      if (typeof event.data !== 'string' || event.data.length > 2 * 1024 * 1024) throw new Error('invalid_frame');
      const data = JSON.parse(event.data);
      if (data.type === 'hello') {
        status(data, true); clearTimeout(handshakeTimer); reconnectAttempt = 0; connectionState(true);
      } else if (data.type === 'status') status(data);
      else if (data.type === 'messages') {
        const rows = wallet.ingest(cursor.accept(data.items), continuity.env);
        append(rows, { env: continuity.env, saved: false }); wallet.persist(cursor.value); backgroundNote();
      }
      else throw new Error('unknown_frame');
    } catch {
      $('feedback').textContent = '응답을 확인할 수 없어 다시 연결합니다.'; ws.close();
    }
  };
  ws.onerror = () => ws.close();
  ws.onclose = (event) => {
    if (socket !== ws || stopping) return;
    clearTimeout(handshakeTimer); connectionState(false);
    record(continuity.dropped(event.code, Date.now()));
    reconnectTimer = setTimeout(connect, Math.min(500 * 2 ** Math.min(reconnectAttempt++, 4), 5000) + Math.random() * 200);
  };
}
window.addEventListener('offline', () => { connectionState(false); socket?.close(); });
window.addEventListener('online', connect);
document.addEventListener('visibilitychange', () => { if (!document.hidden) connect(); });

// --- Composer ---------------------------------------------------------------

function clearPreview() {
  if (previewUrl) URL.revokeObjectURL(previewUrl);
  previewUrl = null; $('file-preview').replaceChildren(); $('file-preview').hidden = true;
}
$('file').onchange = () => {
  clearPreview(); const file = $('file').files[0];
  if (!file) return;
  if (file.size > 10 * 1024 * 1024) { $('file').value = ''; $('feedback').textContent = '첨부파일은 10 MB 이하로 선택해 주세요.'; return; }
  if (rasterTypes.has(file.type)) {
    const image = document.createElement('img'); previewUrl = URL.createObjectURL(file); image.src = previewUrl; image.alt = '전송 전 첨부 미리보기'; $('file-preview').append(image);
  }
  const label = document.createElement('span'); label.textContent = `${file.name} · ${Math.max(1, Math.ceil(file.size / 1024))} KB`;
  const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = '제거'; remove.onclick = () => { $('file').value = ''; clearPreview(); };
  $('file-preview').append(label, remove); $('file-preview').hidden = false;
};
function updateCounter() { $('counter').textContent = `${$('body').value.length.toLocaleString()} / 2,000`; }
$('body').oninput = updateCounter;
$('body').onkeydown = (event) => {
  // Touch keyboards keep Enter as a newline; the send button is always visible there.
  if (event.key === 'Enter' && !event.shiftKey && !event.isComposing && !coarsePointer.matches) { event.preventDefault(); $('form').requestSubmit(); }
};
for (const chip of $('quick').querySelectorAll('button')) {
  chip.onclick = () => { $('body').value = chip.dataset.text; updateCounter(); $('body').focus(); };
}

function updateOutbox() {
  const chat = [...pending].filter((e) => e.kind === 'chat').length;
  $('outbox').hidden = chat === 0; $('ledger-outbox').hidden = pending.size === chat;
  $('send').disabled = $('transfer-send').disabled = pending.size >= 10;
  wallet.renderWallet();
}

// Every attempt reuses the caller-owned request; only the observed outcome is recorded.
function tracked(entry, stage, request) {
  return async (signal) => {
    if (stage === 'message') entry.attempts++;
    try {
      const response = await request(signal);
      const env = response.headers.get('X-Bravo-Env');
      entry.trail.push(`${stage === 'upload' ? '첨부 ' : ''}${env ? envLabel(env) : '?'} ${response.status}`);
      if (stage === 'message' && response.ok) entry.ack = { env, saved: response.status === 201 };
      return response;
    } catch (error) {
      entry.trail.push(`${stage === 'upload' ? '첨부 ' : ''}응답 없음`); throw error;
    } finally {
      entry.trailEl.textContent = `시도 기록 · ${entry.trail.slice(-5).join(' → ')}`;
    }
  };
}

async function send(entry) {
  if (entry.running) return;
  entry.running = true; entry.retry.hidden = true; entry.dismiss.hidden = true; entry.pause.hidden = false;
  entry.controller = new AbortController(); entry.state.textContent = '전송 확인 중';
  const options = { signal: entry.controller.signal, onWait: () => {
    entry.state.textContent = entry.kind === 'ledger' ? '처리 대기 · 같은 거래 번호(같은 요청)로 재시도 중' : '응답 대기 · 같은 요청으로 재시도 중';
  } };
  try {
    if (entry.file && !entry.uploaded) {
      if (!uploadsEnabled) throw Object.assign(new Error('uploads_disabled'), { code: 'uploads_disabled' });
      await deliver(tracked(entry, 'upload', (signal) => {
        const form = new FormData(); form.append('file', entry.file);
        return fetch(`/api/attachments/${entry.message.attachment_id}`, { method: 'PUT', body: form, signal });
      }), { ...options, validate: (value) => {
        if (value?.id !== entry.message.attachment_id) throw new Error('invalid_attachment_ack');
        return value;
      } });
      entry.uploaded = true;
    }
    const saved = await deliver(tracked(entry, 'message', (signal) => fetch('/api/messages', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(entry.message), signal,
    })), { ...options, validate: (value) => {
      validateMessage(value);
      if (value.author !== entry.message.author || value.body !== entry.message.body ||
          (value.attachment_id ?? null) !== entry.message.attachment_id) throw new Error('invalid_message_ack');
      return value;
    } });
    // Do not advance the resume cursor here: other participants' messages may be in flight.
    // 201 means this app stored it now; 200 means an earlier attempt already had.
    pending.delete(entry); entry.el.remove();
    entry.onSaved(saved, entry.ack ?? { env: null, saved: false }, entry);
    record(continuity.delivered(saved.id, entry.attempts, Date.now()));
  } catch (error) {
    const permanent = ['conflict', 'rejected', 'too_large', 'uploads_disabled'].includes(error.code);
    entry.state.textContent = ({ uploads_disabled: '첨부 업로드 제한 · 첨부 없이 새 메시지를 작성해 주세요', conflict: '내용 충돌 · 새 메시지로 작성해 주세요', rejected: '요청 거부 · 입력을 확인해 주세요', too_large: '첨부 용량 초과', paused: '재시도 일시 정지 · 저장 여부 미확인' })[error.code] || '자동 재시도 종료 · 저장 여부 미확인';
    entry.retry.hidden = permanent; entry.pause.hidden = true; entry.dismiss.hidden = false;
  } finally { entry.running = false; updateOutbox(); }
}

// One queue for chat messages and ledger rows: every entry keeps one frozen
// payload and one idempotency key across every retry and environment switch.
function enqueue({ kind, author, body, file = null, summary, container, onSaved, transfer = null, key = uuid() }) {
  const entry = { kind, transfer, onSaved, message: Object.freeze({ idempotency_key: key, author, body, attachment_id: file ? uuid() : null }), file, uploaded: false, running: false, attempts: 0, trail: [] };
  entry.el = document.createElement('div'); entry.el.className = 'pending-item';
  const details = document.createElement('div'), text = document.createElement('p');
  text.textContent = summary; entry.state = document.createElement('small');
  entry.trailEl = document.createElement('small'); entry.trailEl.className = 'trail';
  details.append(text, entry.state, entry.trailEl);
  entry.retry = document.createElement('button'); entry.retry.type = 'button'; entry.retry.textContent = '같은 요청 재시도'; entry.retry.hidden = true; entry.retry.onclick = () => send(entry);
  entry.pause = document.createElement('button'); entry.pause.type = 'button'; entry.pause.textContent = '재시도 정지'; entry.pause.onclick = () => entry.controller?.abort();
  entry.dismiss = document.createElement('button'); entry.dismiss.type = 'button'; entry.dismiss.textContent = '목록에서 숨기기'; entry.dismiss.hidden = true;
  entry.dismiss.onclick = () => {
    if (entry.running || !window.confirm('대기 목록에서만 숨깁니다. 이미 저장된 메시지·송금·첨부는 취소되지 않습니다.')) return;
    pending.delete(entry); entry.el.remove(); updateOutbox();
  };
  entry.el.append(details, entry.retry, entry.pause, entry.dismiss); container.append(entry.el); pending.add(entry);
  updateOutbox(); send(entry);
  return entry;
}

$('form').onsubmit = (event) => {
  event.preventDefault(); if (pending.size >= 10) return;
  const author = $('name').value.trim(), body = $('body').value, file = $('file').files[0];
  if (file && !uploadsEnabled) { $('feedback').textContent = '현재 새 첨부파일 업로드가 제한되어 있습니다.'; return; }
  if (!author || (!body.trim() && !file)) {
    $('feedback').textContent = '별명과 메시지 또는 첨부파일을 입력해 주세요.';
    (author ? $('body') : $('name')).focus(); return;
  }
  try { sessionStorage.setItem('bravo-alias', author); } catch { /* No message contents are persisted. */ }
  enqueue({ kind: 'chat', author, body, file, summary: body || file.name, container: $('outbox'), onSaved: (saved, ack, entry) => {
    append([saved], ack);
    $('feedback').textContent = entry.attempts > 1
      ? `메시지 #${saved.id} 저장을 확인했습니다 · ${entry.attempts}번 시도했지만 같은 멱등 키라 1건만 저장됐습니다.`
      : `메시지 #${saved.id} 저장을 확인했습니다.`;
  } });
  $('body').value = ''; $('file').value = ''; updateCounter(); $('feedback').textContent = ''; clearPreview();
};

// --- Sharing ----------------------------------------------------------------

function showShare() {
  const address = shareAddress(location); $('share-url').value = address;
  try {
    const code = qrcode(0, 'M'); code.addData(address); code.make();
    const count = code.getModuleCount(), scale = Math.max(1, Math.floor(256 / (count + 8)));
    const canvas = $('qr'); canvas.width = canvas.height = (count + 8) * scale;
    const ctx = canvas.getContext('2d'); ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, canvas.width, canvas.height); ctx.fillStyle = '#0f2a3a';
    for (let y = 0; y < count; y++) for (let x = 0; x < count; x++) if (code.isDark(y, x)) ctx.fillRect((x + 4) * scale, (y + 4) * scale, scale, scale);
    canvas.hidden = false;
    $('share-note').textContent = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname) ? '이 주소는 현재 PC 전용입니다. 휴대폰 참여에는 접근 가능한 서버 주소가 필요합니다.' : '참여자가 이 서버 주소에 접근할 수 있어야 합니다. QR은 이 브라우저에서 생성합니다.';
  } catch { $('qr').hidden = true; $('share-note').textContent = 'QR 생성이 어려우면 아래 주소를 복사해 주세요.'; }
  $('share-dialog').showModal();
}
$('share').onclick = $('share-mobile').onclick = showShare;
$('close-share').onclick = () => $('share-dialog').close();
$('copy-link').onclick = async () => {
  try { await navigator.clipboard.writeText($('share-url').value); $('share-note').textContent = '주소를 복사했습니다.'; }
  catch { $('share-url').select(); $('share-note').textContent = '주소를 선택했습니다. 직접 복사해 주세요.'; }
};
window.addEventListener('beforeunload', (event) => { if (pending.size) { event.preventDefault(); event.returnValue = ''; } });
window.addEventListener('pagehide', () => { stopping = true; clearTimeout(reconnectTimer); clearTimeout(handshakeTimer); socket?.close(); for (const entry of pending) entry.controller?.abort(); clearPreview(); });
window.addEventListener('pageshow', (event) => { if (event.persisted) { stopping = false; connect(); } });

function backgroundNote() {
  const count = wallet.background();
  $('load-note').hidden = count === 0;
  $('load-note').textContent = `부하 시험 메시지 ${count.toLocaleString()}건은 대화창에 표시하지 않습니다`;
}

// Resume the stream where the saved ledger was computed, so a reload neither
// replays the whole database nor skips a row in between. Recent chat history
// is fetched separately for display only.
async function startup() {
  let saved = wallet.restore();
  try {
    const response = await fetch('/api/messages', { signal: AbortSignal.timeout(3000) });
    if (!response.ok) throw new Error('unavailable');
    const env = response.headers.get('X-Bravo-Env');
    const page = await response.json();
    if (!Array.isArray(page)) throw new Error('invalid_messages');
    const latest = page.length ? page[page.length - 1].id : 0;
    if (saved !== null && saved > latest) { wallet.discard(); saved = null; } // the database was reset
    if (saved === null) {
      append(wallet.ingest(cursor.accept(page), env), { env, saved: false });
      wallet.persist(cursor.value); backgroundNote();
    } else {
      cursor.value = saved;
      for (const m of page) validateMessage(m);
      append(page.filter((m) => m.id <= saved && wallet.isChat(m)), { env, saved: false });
    }
  } catch {
    if (saved !== null) cursor.value = saved;
  }
}

wallet.init({ enqueue, uuid, pending: () => [...pending] });
renderSplit();
await startup();
connect();
