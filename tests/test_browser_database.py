"""The optional reset helper must reject arbitrary connection configuration."""

import json
import os

import pytest

from tests.browser_database import BrowserDatabase


def test_fixture_rejects_platform_without_owner_checks(monkeypatch):
    monkeypatch.delattr(os, "getuid", raising=False)
    with pytest.raises(ValueError, match="POSIX"):
        BrowserDatabase("not-opened-or-connected")


@pytest.mark.parametrize(
    "fault",
    [
        "symlink",
        "public_file",
        "public_dir",
        "wrong_prefix",
        "ordinary_database",
        "remote_override",
        "invalid_database",
    ],
)
def test_fixture_rejects_nonisolated_reset_target(tmp_path, fault):
    root = tmp_path / ("other" if fault == "wrong_prefix" else "bravo-browser-pg-owned")
    root.mkdir(mode=0o700)
    path = root / "fixture.json"
    config = {"database": "bravo_browser_" + "a" * 16}
    if fault == "ordinary_database":
        config["database"] = "postgres"
    elif fault == "invalid_database":
        config["database"] = "bravo_browser_' ; anything"
    elif fault == "remote_override":
        config["host"] = "remote.invalid"
    path.write_text(json.dumps(config))
    path.chmod(0o600)
    if fault == "symlink":
        target = root / "real.json"
        path.rename(target)
        path.symlink_to(target)
    elif fault == "public_file":
        path.chmod(0o644)
    elif fault == "public_dir":
        root.chmod(0o755)
    with pytest.raises(ValueError):
        BrowserDatabase(str(path))
