"""Run via the image's Python stdin, without external services or credentials."""

import asyncio
import importlib.util
import json
import os
import pathlib
import shutil

import boto3
import psycopg.pq
import urllib3

from app.config import Settings
from app.main import STATIC_DIR, create_app


async def check():
    assert os.getuid() == 10001
    for installer in ("pip", "ensurepip", "setuptools"):
        assert importlib.util.find_spec(installer) is None, installer
    assert shutil.which("uv") is None
    assert not list(pathlib.Path("/usr/local/lib").glob("python*/site-packages/pip*"))
    assert not list(pathlib.Path("/usr/local/lib").glob("python*/ensurepip"))
    assert urllib3.__version__ == "2.8.0"
    assert psycopg.pq.version() > 0
    assert boto3.__version__
    assert (STATIC_DIR / "index.html").is_file()

    # Stub only the external dependencies; exercise imports, factory, lifespan,
    # and health handlers from the actual final image as its configured UID.
    settings = Settings(
        app_env="image-check",
        db_host="unused.invalid",
        db_port=5432,
        db_name="unused",
        db_secret_id="unused",
        s3_bucket="unused",
        s3_endpoint_url=None,
        s3_force_path_style=False,
        admin_token=None,
    )
    app = create_app(settings, store=object(), storage=object())
    async with app.router.lifespan_context(app):
        for path in ("/healthz", "/readyz"):
            route = next(route for route in app.routes if getattr(route, "path", None) == path)
            assert await route.endpoint() == {"ok": True}
    print(json.dumps({
        "runtime_uid": os.getuid(),
        "installer_modules_absent": True,
        "uv_absent": True,
        "app_import_and_health_handlers": "pass",
        "external_service_integration_tested": False,
    }, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(check())
