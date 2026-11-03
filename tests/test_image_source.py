import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "image_source", Path(__file__).parents[1] / "scripts" / "prepare_image_source.py"
)
image_source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(image_source)


@pytest.fixture
def repository(tmp_path, monkeypatch):
    repo = tmp_path / "repository"
    repo.mkdir()
    monkeypatch.chdir(repo)
    image_source.git("init", "-b", "main")
    image_source.git("config", "user.name", "Source Test")
    image_source.git("config", "user.email", "source-test@example.invalid")
    (repo / "Dockerfile").write_text("FROM scratch\n")
    image_source.git("add", "Dockerfile")
    image_source.git("commit", "-m", "fixture")
    source = image_source.git("rev-parse", "HEAD").decode().strip()
    image_source.git("update-ref", "refs/remotes/origin/main", source)
    return repo, source


def test_context_ignores_untracked_environment_and_modified_local_source(repository, tmp_path):
    repo, source = repository
    (repo / ".env").write_text("PRIVATE_LOCAL_EXAMPLE\n")
    (repo / "Dockerfile").write_text("FROM local-modified-image\n")
    result = image_source.prepare(source, tmp_path / "context", tmp_path / "receipt.json")
    assert (tmp_path / "context" / "Dockerfile").read_text() == "FROM scratch\n"
    assert not (tmp_path / "context" / ".env").exists()
    assert result["files"] == ["Dockerfile"]
    assert json.loads((tmp_path / "receipt.json").read_text())["source_sha"] == source


def test_unreviewed_branch_commit_is_rejected(repository, tmp_path):
    repo, _ = repository
    (repo / "new.txt").write_text("unreviewed\n")
    image_source.git("add", "new.txt")
    image_source.git("commit", "-m", "not-on-remote-main")
    source = image_source.git("rev-parse", "HEAD").decode().strip()
    with pytest.raises(subprocess.CalledProcessError):
        image_source.prepare(source, tmp_path / "context", tmp_path / "receipt.json")
    assert not (tmp_path / "context").exists()


def test_tracked_environment_file_is_rejected(repository, tmp_path):
    repo, _ = repository
    (repo / ".env.production").write_text("TRACKED_EXAMPLE\n")
    image_source.git("add", ".env.production")
    image_source.git("commit", "-m", "unsafe-source-fixture")
    source = image_source.git("rev-parse", "HEAD").decode().strip()
    image_source.git("update-ref", "refs/remotes/origin/main", source)
    with pytest.raises(ValueError, match="environment"):
        image_source.prepare(source, tmp_path / "context", tmp_path / "receipt.json")


def test_short_or_symbolic_ref_is_rejected(repository, tmp_path):
    for source in ("main", "HEAD", "a" * 7):
        with pytest.raises(ValueError, match="full commit"):
            image_source.prepare(source, tmp_path / "context", tmp_path / "receipt.json")
