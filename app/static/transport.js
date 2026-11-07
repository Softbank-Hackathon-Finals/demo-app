// API acknowledgement and the contiguous WebSocket cursor are deliberately separate.
// A fast POST response must not skip messages committed by another participant.
export class MessageCursor {
  value = null;
  accept(items) {
    if (!Array.isArray(items) || items.length > 200) throw new Error('invalid_messages');
    let previous = 0;
    for (const item of items) {
      validateMessage(item);
      if (item.id <= previous) throw new Error('unordered_messages');
      previous = item.id;
    }
    const fresh = items.filter((item) => item.id > (this.value ?? 0));
    if (items.length) this.value = Math.max(this.value ?? 0, previous);
    return fresh;
  }
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function validateMessage(item) {
  if (!item || !Number.isSafeInteger(item.id) || item.id < 1 ||
      typeof item.author !== 'string' || item.author.length > 40 || !item.author.length ||
      typeof item.body !== 'string' || item.body.length > 2000 ||
      typeof item.created_at !== 'string' || !Number.isFinite(Date.parse(item.created_at)) ||
      (item.attachment_id != null && !UUID.test(item.attachment_id))) {
    throw new Error('invalid_message');
  }
  return item;
}

export class DeliveryError extends Error {
  constructor(code) { super(code); this.code = code; }
}

export function retryDelay(header, attempt, now = Date.now()) {
  let seconds = Number(header);
  if (header && !Number.isFinite(seconds)) seconds = (Date.parse(header) - now) / 1000;
  if (!header || !Number.isFinite(seconds) || seconds <= 0) {
    return Math.min(500 * 2 ** Math.min(attempt, 4), 5000);
  }
  return Math.max(500, Math.min(seconds * 1000, 30000));
}

export function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DeliveryError('paused'));
    const done = () => { signal?.removeEventListener('abort', abort); resolve(); };
    const timer = setTimeout(done, ms);
    const abort = () => {
      clearTimeout(timer); signal.removeEventListener('abort', abort);
      reject(new DeliveryError('paused'));
    };
    signal?.addEventListener('abort', abort, { once: true });
  });
}

// Keep request payload and idempotency keys outside this function, unchanged on retries.
export async function deliver(request, {
  signal, onWait = () => {}, budgetMs = 60000, timeoutMs = 10000,
  now = Date.now, wait = sleep, validate = (value) => value,
} = {}) {
  const deadline = now() + budgetMs;
  for (let attempt = 0; ; attempt++) {
    if (attempt >= 32) throw new DeliveryError('unconfirmed');
    if (signal?.aborted) throw new DeliveryError('paused');
    const remaining = deadline - now();
    if (remaining <= 0) throw new DeliveryError('unconfirmed');
    const controller = new AbortController();
    const abort = () => controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(abort, Math.min(timeoutMs, remaining));
    let delay = retryDelay(null, attempt, now());
    try {
      const response = await request(controller.signal);
      if (response.ok) return validate(await response.json());
      if (response.status < 500 && response.status !== 429) {
        throw new DeliveryError(response.status === 409 ? 'conflict' :
          response.status === 413 ? 'too_large' : response.status === 401 ? 'unauthorized' : 'rejected');
      }
      delay = retryDelay(response.headers.get('Retry-After'), attempt, now());
    } catch (error) {
      if (error instanceof DeliveryError) throw error;
      // A timeout, response loss or invalid success body is an uncertain result.
      // Retrying the same key recovers the original acknowledgement.
    } finally {
      clearTimeout(timer); signal?.removeEventListener('abort', abort);
    }
    if (signal?.aborted) throw new DeliveryError('paused');
    if (delay >= deadline - now()) throw new DeliveryError('unconfirmed');
    onWait();
    await wait(delay, signal);
  }
}

export function shareAddress(location) {
  const url = new URL(location.href);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password) {
    throw new Error('invalid_share_address');
  }
  // No query, fragment, admin token or operator-selected redirect is shared.
  return `${url.origin}/`;
}
