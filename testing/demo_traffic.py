"""Demo-only automatic participants for the Bravo points ledger.

They open a few accounts and keep sending small transfers through the public
POST /api/messages contract, exactly like a browser: one frozen payload and one
idempotency key per transfer, retried on 503 / timeouts until acknowledged.
During a cutover the stage view then shows "pending → debited once" without
anyone in the room having to participate.

Not part of the service image (only app/ is copied). Authors never start with
"loadgen-", so T18 load reconciliation does not count these rows.

    python testing/demo_traffic.py --base-url https://demo.example --interval 4
"""

import argparse
import json
import random
import string
import sys
import time
import urllib.error
import urllib.request
import uuid

GRANT = 100_000
PREFIX = "BRAVO-LEDGER/1 "
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_SYSTEM_RANDOM = random.SystemRandom()
DEFAULT_NAMES = ("자동 참여자 하늘", "자동 참여자 준서", "자동 참여자 서연", "자동 참여자 도윤")


def new_code(rng=_SYSTEM_RANDOM):
    return "".join(rng.choice(ALPHABET) for _ in range(6))


def _event_line(event):
    # Same shape as JSON.stringify in app/static/ledger.js (key order, no spaces).
    return PREFIX + json.dumps(event, ensure_ascii=False, separators=(",", ":"))


def encode_open(acct, name):
    return f"[Bravo 계좌 개설] {name} · 축하 포인트 {GRANT:,}P\n" + _event_line(
        {"t": "open", "acct": acct, "name": name}
    )


def encode_transfer(n, sender, receiver, amount, memo="", sender_name=None, receiver_name=None):
    who = f"{sender_name or sender} → {receiver_name or receiver}"
    head = f"[Bravo 송금] {amount:,}P · {who}" + (f" · {memo}" if memo else "")
    event = {
        "t": "transfer",
        "n": n,
        "from": sender,
        "to": receiver,
        "amount": amount,
        "memo": memo,
    }
    return head + "\n" + _event_line(event)


def decode(body):
    at = body.rfind("\n" + PREFIX) if isinstance(body, str) else -1
    if at < 0:
        return None
    try:
        event = json.loads(body[at + 1 + len(PREFIX) :])
    except ValueError:
        return None
    return event if isinstance(event, dict) and event.get("t") in {"open", "transfer"} else None


class Ledger:
    """Balances of the accounts this process has seen, replayed in id order."""

    def __init__(self):
        self.names = {}
        self.balance = {}
        self.nonces = set()

    def ingest(self, row):
        event = decode(row.get("body"))
        if event is None:
            return
        if event["t"] == "open" and event.get("acct") not in self.balance:
            self.balance[event["acct"]] = GRANT
            self.names[event["acct"]] = event.get("name", event["acct"])
        elif event["t"] == "transfer" and event.get("n") not in self.nonces:
            amount = event.get("amount")
            if not isinstance(amount, int) or amount < 1:
                return
            self.nonces.add(event["n"])
            if event.get("from") in self.balance:
                self.balance[event["from"]] -= amount
            if event.get("to") in self.balance:
                self.balance[event["to"]] += amount


class Http:
    def __init__(self, base_url, timeout=10):
        self.base = base_url.rstrip("/")
        self.timeout = timeout

    def request(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(
            self.base + path, data=data, method=method, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return resp.status, dict(resp.headers), json.loads(resp.read() or b"null")
        except urllib.error.HTTPError as error:
            return error.code, dict(error.headers), None


def deliver(http, payload, *, budget=60.0, sleep=time.sleep, now=time.monotonic, log=print):
    """POST one frozen payload until acknowledged; returns (row, attempts, env) or None."""
    deadline, attempts, delay = now() + budget, 0, 0.5
    while now() < deadline:
        attempts += 1
        try:
            status, headers, body = http.request("POST", "/api/messages", payload)
        except (OSError, ValueError):
            status, headers, body = None, {}, None  # lost response: same key again
        if status in (200, 201) and isinstance(body, dict):
            env = {k.lower(): v for k, v in headers.items()}.get("x-bravo-env")
            return body, attempts, env
        if status is not None and status < 500 and status != 429:
            log(f"rejected status={status}; not retrying")
            return None
        retry_after = {k.lower(): v for k, v in headers.items()}.get("retry-after")
        try:
            wait = max(0.5, min(float(retry_after), 30.0)) if retry_after else delay
        except ValueError:
            wait = delay
        delay = min(delay * 2, 5.0)
        sleep(wait)
    log(f"unconfirmed after {attempts} attempts")
    return None


def follow(http, ledger, cursor):
    """Read every row after cursor, 200 at a time, into the ledger."""
    while True:
        status, _, rows = http.request("GET", f"/api/messages?after={cursor}")
        if status != 200 or not isinstance(rows, list):
            return cursor
        for row in rows:
            ledger.ingest(row)
            cursor = max(cursor, row["id"])
        if len(rows) < 200:
            return cursor


def run(
    http,
    *,
    names,
    interval,
    duration,
    include_others,
    rng=None,
    log=print,
    sleep=time.sleep,
    clock=time.monotonic,
):
    rng = rng or random.Random()
    ledger = Ledger()
    status, _, recent = http.request("GET", "/api/messages")
    cursor = recent[-1]["id"] if status == 200 and recent else 0
    bots = []
    for name in names:
        code = new_code()
        result = deliver(
            http,
            {"idempotency_key": str(uuid.uuid4()), "author": name, "body": encode_open(code, name)},
            sleep=sleep,
            log=log,
        )
        if result:
            ledger.ingest(result[0])
            bots.append(code)
            log(f"opened {name} BRV-{code}")
    if len(bots) < 2:
        log("need at least two automatic accounts")
        return 1
    end = clock() + duration if duration else None
    sent = 0
    while end is None or clock() < end:
        cursor = follow(http, ledger, cursor)
        sender = rng.choice(bots)
        targets = [c for c in (ledger.balance if include_others else bots) if c != sender]
        balance = ledger.balance.get(sender, 0)
        if targets and balance >= 1000:
            receiver = rng.choice(targets)
            amount = rng.randrange(1, min(10, balance // 1000) + 1) * 1000
            n = str(uuid.uuid4())
            memo = rng.choice(["", "커피", "점심", "회비", "택시"])
            body = encode_transfer(
                n,
                sender,
                receiver,
                amount,
                memo,
                ledger.names.get(sender),
                ledger.names.get(receiver),
            )
            result = deliver(
                http,
                {"idempotency_key": n, "author": ledger.names[sender], "body": body},
                sleep=sleep,
                log=log,
            )
            if result:
                row, attempts, env = result
                ledger.ingest(row)
                sent += 1
                log(
                    f"#{row['id']} {amount:,}P {ledger.names[sender]} → {ledger.names.get(receiver, receiver)}"
                    f" · {env or '?'}{f' · {attempts} attempts, one row' if attempts > 1 else ''}"
                )
        sleep(interval)
    log(f"done: {sent} transfers")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://127.0.0.1:8081")
    parser.add_argument("--bots", type=int, default=4, help="number of automatic accounts (2-8)")
    parser.add_argument("--interval", type=float, default=4.0, help="seconds between transfers")
    parser.add_argument(
        "--duration", type=float, default=0, help="seconds to run; 0 = until Ctrl+C"
    )
    parser.add_argument(
        "--include-participants",
        action="store_true",
        help="also send to accounts opened by people in the room",
    )
    args = parser.parse_args(argv)
    if not 2 <= args.bots <= 8 or args.interval < 0.5:
        parser.error("--bots must be 2-8 and --interval at least 0.5")
    names = list(DEFAULT_NAMES)
    while len(names) < args.bots:
        names.append(f"자동 참여자 {''.join(random.choices(string.ascii_uppercase, k=2))}")
    try:
        return run(
            Http(args.base_url),
            names=names[: args.bots],
            interval=args.interval,
            duration=args.duration,
            include_others=args.include_participants,
        )
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
