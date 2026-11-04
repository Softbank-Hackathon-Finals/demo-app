"""Run Chromium/WebKit against the real Store in an owned temporary PostgreSQL.

Requires PostgreSQL server tools and installed Python/Node/browser dependencies.
No existing database, cloud credentials or deployed app is used.
"""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pg-bin", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New private evidence directory")
    args = parser.parse_args()
    os.umask(0o077)
    args.output.mkdir(parents=True, exist_ok=False, mode=0o700)
    out = args.output.resolve()
    pg = args.pg_bin.resolve(strict=True)
    for name in ("initdb", "pg_ctl", "postgres"):
        if not (pg / name).is_file():
            parser.error("PostgreSQL server tools required")
    pnpm = [shutil.which("corepack"), "pnpm", "test:browser"]
    if not pnpm[0]:
        parser.error("corepack required")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("AWS_", "OTEL_", "OPENAI_", "GITHUB_", "GH_", "BRAVO_", "PG"))
        and "proxy" not in k.lower()
    }
    env["AWS_EC2_METADATA_DISABLED"] = "true"
    env["AWS_CONFIG_FILE"] = env["AWS_SHARED_CREDENTIALS_FILE"] = os.devnull
    os.environ.clear()
    os.environ.update(env)
    result = {
        "scope": "local browser / shared PostgreSQL / test attachment storage",
        "database_migration": "NOT_VERIFIED",
        "cloud_cutover": "NOT_VERIFIED",
    }
    with tempfile.TemporaryDirectory(prefix="bravo-browser-pg-", dir="/tmp") as tmp:
        root = Path(tmp).resolve()
        data = root / "data"
        running = False

        def run(name, *params):
            with (out / "postgres.log").open("a") as log:
                return subprocess.run(
                    [str(pg / name), *map(str, params)],
                    stdout=log,
                    stderr=log,
                    check=True,
                    timeout=40,
                    env=env,
                )

        try:
            run(
                "initdb",
                "-D",
                data,
                "--auth-local=trust",
                "--auth-host=reject",
                "--no-locale",
                "--encoding=UTF8",
            )
            with (data / "postgresql.conf").open("a") as f:
                f.write(f"\nlisten_addresses = ''\nunix_socket_directories = '{root}'\n")
            run("pg_ctl", "-D", data, "-l", root / "server.log", "-w", "start")
            running = True
            name = "bravo_browser_" + uuid.uuid4().hex[:16]
            with psycopg.connect(
                make_conninfo(host=str(root), dbname="postgres"), autocommit=True
            ) as c:
                c.execute(
                    "CREATE ROLE chat_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
                )
                c.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
                result["postgres_version"] = c.execute("SHOW server_version").fetchone()[0]
            with psycopg.connect(make_conninfo(host=str(root), dbname=name), autocommit=True) as c:
                c.execute((ROOT / "db/schema.sql").read_text())
            fixture = root / "fixture.json"
            fixture.write_text(json.dumps({"database": name}))
            env["BRAVO_BROWSER_DATABASE_FIXTURE"] = str(fixture)
            env["TEST_DATABASE_URL"] = make_conninfo(host=str(root), dbname=name, user="chat_app")
            with (out / "python.log").open("w") as log:
                python = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q"],
                    cwd=ROOT,
                    env=env,
                    stdout=log,
                    stderr=log,
                    timeout=120,
                )
            result["python_exit_code"] = python.returncode
            if python.returncode:
                raise RuntimeError("python_regression_failed; see private python.log")
            with (out / "browser.log").open("w") as log:
                process = subprocess.Popen(
                    pnpm, cwd=ROOT, env=env, stdout=log, stderr=log, start_new_session=True
                )
                try:
                    result["browser_exit_code"] = process.wait(timeout=240)
                finally:
                    # Reap only this run's process group on timeout/interruption.
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
                    if process.poll() is None:
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
        finally:
            if running:
                run("pg_ctl", "-D", data, "-m", "fast", "-w", "stop")
            result["owned_postgres_stopped"] = True
            (out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))
    return result["browser_exit_code"]


if __name__ == "__main__":
    sys.exit(main())
