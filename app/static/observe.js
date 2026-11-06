// What the participant can observe about service continuity, derived only from
// what this browser actually saw: socket close codes, hello/status frames and
// acknowledgements. Nothing here is reported by the platform or invented.

const LABELS = { aws: 'AWS', onprem: '온프렘', local: '로컬' };

export function envLabel(env) {
  return Object.hasOwn(LABELS, env) ? LABELS[env] : '미확인 환경';
}

export function envKey(env) {
  return Object.hasOwn(LABELS, env) ? env : 'unknown';
}

export function duration(ms) {
  if (!Number.isFinite(ms) || ms < 0) return '-';
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}초`;
  return `${Math.floor(ms / 60000)}분 ${Math.round((ms % 60000) / 1000)}초`;
}

export class Continuity {
  env = null;
  blocked = false;
  reconnects = 0;
  switches = 0;
  lastGapMs = null;
  retried = 0;
  events = [];
  #droppedAt = null;
  #blockedAt = null;
  #blockedMs = 0;

  constructor(limit = 40) { this.limit = limit; }

  #push(kind, text, at) {
    const event = { kind, text, at };
    this.events.unshift(event);
    if (this.events.length > this.limit) this.events.length = this.limit;
    return event;
  }

  // The socket closed (or failed). Only the first close of an outage counts.
  dropped(code, at) {
    if (this.#droppedAt !== null) return null;
    this.#droppedAt = at;
    const observedCode = Number.isInteger(code) && code >= 1000 && code <= 4999 ? code : null;
    const event = this.#push('drop', observedCode === 1012
      ? '서버가 연결을 재배치했습니다(1012) · 자동 재접속'
      : `연결이 끊겼습니다${observedCode === null ? '' : `(${observedCode})`} · 자동 재접속`, at);
    event.closeCode = observedCode;
    return event;
  }

  // A hello frame proves a working connection to some app instance.
  connected(env, blocked, at) {
    const out = [];
    if (this.#droppedAt !== null) {
      this.reconnects++;
      this.lastGapMs = Math.max(0, at - this.#droppedAt);
      this.#droppedAt = null;
      out.push(this.#push('reconnect', `${envLabel(env)} 앱에 재접속 · 공백 ${duration(this.lastGapMs)}`, at));
    } else if (this.env === null) {
      out.push(this.#push('connect', `${envLabel(env)} 앱에 연결했습니다`, at));
    }
    return out.concat(this.status(env, blocked, at));
  }

  status(env, blocked, at) {
    const out = [];
    if (this.env !== null && env !== this.env) {
      this.switches++;
      out.push(this.#push('switch', `처리 환경 전환 · ${envLabel(this.env)} → ${envLabel(env)}`, at));
    }
    this.env = env;
    if (blocked && !this.blocked) {
      this.#blockedAt = at;
      out.push(this.#push('pause', '새 쓰기 일시 정지 · 읽기는 계속', at));
    } else if (!blocked && this.blocked) {
      const spent = Math.max(0, at - (this.#blockedAt ?? at));
      this.#blockedMs += spent; this.#blockedAt = null;
      out.push(this.#push('resume', `쓰기 재개 · 정지 ${duration(spent)}`, at));
    }
    this.blocked = blocked;
    return out;
  }

  blockedMs(at) {
    return this.#blockedMs + (this.blocked && this.#blockedAt !== null ? Math.max(0, at - this.#blockedAt) : 0);
  }

  // One saved message after `attempts` POSTs that all reused one idempotency key.
  delivered(id, attempts, at) {
    if (attempts < 2) return null;
    this.retried++;
    return this.#push('retry', `메시지 #${id} · ${attempts}번 시도, 1건만 저장(같은 멱등 키)`, at);
  }
}
