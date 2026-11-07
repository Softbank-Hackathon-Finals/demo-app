"""T46 메시지 개인정보 검사 끝까지 시험(로컬 compose). 실제 demo-chat 앱 두 개(aws · onprem)가 메시지를 저장하고
SQS 호환 서버(ElasticMQ)로 검사 요청을 보내면, platform 호환 런타임이 functions/pii_check를 고치지 않고 실행한다.

    # platform 쪽에서 런타임 베이스 이미지를 먼저 만든다
    (cd <platform>/services/function-runtime/testing/local && docker compose build runtime-base)
    docker compose -f compose.yaml -f compose.pii-check.yaml up -d --build
    python3 testing/pii_check/e2e.py
    docker compose -f compose.yaml -f compose.pii-check.yaml down -v

확인: 두 앱의 메시지가 모두 검사되고 판정이 맞다 / 큐가 멈추면 503이고 저장은 된다 / 같은 멱등 키로 다시 보내면
검사된다 / 결과 테이블 · 로그에 일치한 값과 본문이 없다. 항목별 PASS / FAIL을 JSON으로 낸다.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COMPOSE = ["docker", "compose", "-f", "compose.yaml", "-f", "compose.pii-check.yaml"]
APPS = {"aws": "http://127.0.0.1:8081", "onprem": "http://127.0.0.1:8082"}
PII = {"주민번호 900101-1234567 입니다": {"rrn": 1}, "카드 1234-5678-9012-3456 로 결제": {"card": 1},
       "계좌 110-123-456789 로 보내 주세요": {"account": 1}}
VALUES = ["900101-1234567", "1234-5678-9012-3456", "110-123-456789"]


def compose(*args: str) -> str:
    return subprocess.run(COMPOSE + list(args), cwd=ROOT, check=True, capture_output=True, text=True).stdout


def request(method: str, url: str, body: dict | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(url, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def post(app: str, body: str, key: str | None = None) -> tuple[int, dict, str]:
    key = key or str(uuid.uuid4())
    code, data = request("POST", f"{APPS[app]}/api/messages", {"idempotency_key": key, "author": "상담원", "body": body})
    return code, data, key


def checks() -> dict[int, dict]:
    _, rows = request("GET", f"{APPS['onprem']}/api/message-checks?after=0")
    return {r["message_id"]: r for r in rows}


def all_checked(ids) -> dict | None:
    got = checks()
    return got if set(ids) <= set(got) else None


def wait_for(fn, timeout_s: float):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if value := fn():
            return value
        time.sleep(0.5)
    return fn()


def main() -> int:
    for app, base in APPS.items():
        ok = wait_for(lambda base=base: request("GET", f"{base}/api/info")[1].get("pii_check_enabled"), 60)
        if not ok:
            print(json.dumps({"verdict": "FAIL", "reason": f"{app} has pii check off"}))
            return 1
    results: dict[str, dict] = {}

    # 1. 두 앱이 저장한 메시지가 모두 검사되고 판정이 맞다
    expected: dict[int, dict] = {}
    for i in range(20):
        app = "aws" if i % 2 else "onprem"
        body = list(PII)[i % 3] if i % 4 == 0 else f"일반 상담 {i}"
        code, data, _ = post(app, body)
        assert code == 201, (code, data)
        expected[data["id"]] = PII.get(body, {})
    got = wait_for(lambda: all_checked(expected), 60) or checks()
    wrong = [m for m, counts in expected.items() if got.get(m, {}).get("counts") != counts]
    results["both_apps_checked"] = {"verdict": "PASS" if not wrong and set(expected) <= set(got) else "FAIL",
                                    "sent": len(expected), "checked": len(set(expected) & set(got)), "wrong": len(wrong)}

    # 2. 큐가 멈추면 503이고 저장은 된다. 같은 멱등 키로 다시 보내면 검사된다
    compose("stop", "queue")
    code, data, key = post("onprem", "카드 1234-5678-9012-3456 로 결제")
    saved = [m for m in request("GET", f"{APPS['onprem']}/api/messages")[1] if m["body"].startswith("카드")]
    compose("start", "queue")
    time.sleep(2)
    retry_code, retry, _ = post("onprem", "카드 1234-5678-9012-3456 로 결제", key)
    checked = wait_for(lambda: retry.get("id") in checks(), 30)
    results["queue_down_then_retry"] = {
        "verdict": "PASS" if code == 503 and retry_code == 200 and checked else "FAIL",
        "status_while_queue_down": code, "retry_status": retry_code, "checked_after_retry": bool(checked),
        "saved_rows_with_body": len(saved)}

    # 3. 결과 테이블 · 로그에 일치한 값과 본문이 없다
    table = compose("exec", "-T", "db", "psql", "-U", "postgres", "-d", "chat", "-Atc",
                    "SELECT message_checks::text FROM message_checks")
    logs = compose("logs", "pii-check", "app-aws", "app-onprem")
    leaked = {"table": sum(v in table for v in VALUES + list(PII)), "logs": sum(v in logs for v in VALUES + list(PII))}
    results["no_values_stored"] = {"verdict": "PASS" if leaked == {"table": 0, "logs": 0} else "FAIL", "leaked": leaked}

    verdict = "PASS" if all(r["verdict"] == "PASS" for r in results.values()) else "FAIL"
    json.dump({"verdict": verdict, "results": results}, sys.stdout, ensure_ascii=False, indent=2)
    print()
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
