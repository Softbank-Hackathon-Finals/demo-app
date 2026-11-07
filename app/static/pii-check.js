// T46 메시지 개인정보 검사 결과 표시. 서버가 검사를 켰을 때만 동작한다(/api/info의 pii_check_enabled).
// 기존 화면 코드(chat.js)는 고치지 않고, 메시지 요소의 data-message-id에 표시만 붙인다.
// 결과에는 종류와 개수만 있다. 일치한 값은 서버가 저장하지 않으므로 화면에도 없다.

const KINDS = { rrn: '주민번호', card: '카드번호', account: '계좌번호' };
const POLL_MS = 2000;

export function label(check) {
  if (!check.has_pii) return { text: '개인정보 없음', warn: false };
  const parts = Object.entries(check.counts || {}).map(([kind, n]) => `${KINDS[kind] || kind} ${n}`);
  return { text: `개인정보 포함 · ${parts.join(', ')}`, warn: true };
}

function decorate(article, check) {
  const meta = article.querySelector('.meta');
  if (!meta || meta.querySelector('.pii-check')) return;
  const { text, warn } = label(check);
  const badge = document.createElement('span');
  badge.className = `pii-check${warn ? ' warn' : ''}`;
  badge.textContent = text;
  badge.title = '메시지 저장 뒤 별도 함수가 검사한 결과(T46)';
  meta.append(badge);
}

function style() {
  const css = document.createElement('style');
  css.textContent = `.pii-check{font-size:11px;padding:1px 6px;border-radius:8px;background:#e8f5e9;color:#2e7d32}
.pii-check.warn{background:#fdecea;color:#b71c1c;font-weight:600}`;
  document.head.append(css);
}

async function start() {
  let info;
  try { info = await (await fetch('/api/info', { cache: 'no-store' })).json(); } catch { return; }
  if (!info.pii_check_enabled) return;
  style();
  const results = new Map();
  let after = 0;
  const apply = (root) => {
    for (const article of root.querySelectorAll('article[data-message-id]')) {
      const check = results.get(Number(article.dataset.messageId));
      if (check) decorate(article, check);
    }
  };
  new MutationObserver(() => apply(document)).observe(document.body, { childList: true, subtree: true });
  const poll = async () => {
    try {
      const rows = await (await fetch(`/api/message-checks?after=${after}`, { cache: 'no-store' })).json();
      for (const row of rows) { results.set(row.message_id, row); after = Math.max(after, row.message_id); }
      if (rows.length) apply(document);
    } catch { /* 다음 주기에 다시 묻는다. */ }
    setTimeout(poll, POLL_MS);
  };
  poll();
}

if (typeof document !== 'undefined') start();
