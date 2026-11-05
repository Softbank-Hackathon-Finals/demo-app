import copy
import hashlib
import json
import subprocess

import pytest

from scripts import candidate_image as image
from scripts import prepare_candidate_source as source


@pytest.fixture
def candidate(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.chdir(repo)
    source.git("init", "-b", "main")
    source.git("config", "user.name", "Candidate fixture")
    source.git("config", "user.email", "candidate@example.invalid")
    for name, contents in {
        "Dockerfile": "FROM scratch\nUSER 10001\n",
        ".dockerignore": ".env*\n",
        "pyproject.toml": "[project]\nname='fixture'\n",
        "uv.lock": "version = 1\n",
        ".python-version": "3.13\n",
        "app/main.py": "VALUE = 'main'\n",
    }.items():
        path = repo / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(contents)
    source.git("add", ".")
    source.git("commit", "-m", "main")
    base = source.git("rev-parse", "HEAD").decode().strip()
    source.git("update-ref", "refs/remotes/origin/main", base)
    source.git("switch", "-c", "candidate")
    (repo / "app/main.py").write_text("VALUE = 'candidate'\n")
    source.git("add", ".")
    source.git("commit", "-m", "candidate")
    head = source.git("rev-parse", "HEAD").decode().strip()
    source.git("update-ref", "refs/candidates/42", head)
    return repo, base, head


def digest(base, head):
    return hashlib.sha256(source.git(
        "diff", "--binary", "--full-index", "--no-ext-diff", "--no-renames", base, head, "--"
    )).hexdigest()


def export(candidate, tmp_path, **changes):
    _, base, head = candidate
    args = dict(base=base, source=head, workflow=base, pull="42",
                diff_digest=digest(base, head), request_id="fixture-42",
                destination=tmp_path / "bundle/context")
    args.update(changes)
    return source.prepare(**args)


def test_unmerged_source_is_exported_without_local_files_or_candidate_controls(candidate, tmp_path):
    repo, base, head = candidate
    (repo / "app/main.py").write_text("LOCAL_MODIFICATION\n")
    (repo / ".env").write_text("LOCAL_ONLY\n")
    (repo / "Dockerfile").write_text("LOCAL_RECIPE\n")
    packet = export(candidate, tmp_path)
    context = tmp_path / "bundle/context"
    assert (context / "app/main.py").read_text() == "VALUE = 'candidate'\n"
    assert (context / "Dockerfile").read_text() == "FROM scratch\nUSER 10001\n"
    assert not (context / ".env").exists()
    assert packet["source_sha"] == head
    assert packet["workflow_sha"] == base
    assert packet["changed_files"] == ["app/main.py"]
    assert hashlib.sha256((context.parent / "candidate.diff").read_bytes()).hexdigest() == packet["diff_sha256"]


@pytest.mark.parametrize("changes,error", [
    ({"diff_digest": "0" * 64}, "diff_digest_mismatch"),
    ({"source": "HEAD"}, "full_commit_required"),
    ({"pull": "42;exit"}, "invalid_pull_request"),
    ({"request_id": "../../unsafe"}, "invalid_request_id"),
])
def test_wrong_input_binding_is_rejected_before_export(candidate, tmp_path, changes, error):
    with pytest.raises(ValueError, match=error):
        export(candidate, tmp_path, **changes)
    assert not (tmp_path / "bundle/context").exists()


def test_stale_pr_head_and_already_merged_candidate_are_rejected(candidate, tmp_path):
    _, base, head = candidate
    source.git("update-ref", "refs/candidates/42", base)
    with pytest.raises(ValueError, match="pull_head_mismatch"):
        export(candidate, tmp_path)
    source.git("update-ref", "refs/candidates/42", head)
    source.git("update-ref", "refs/remotes/origin/main", head)
    with pytest.raises(ValueError, match="candidate_must_be_unmerged"):
        export(candidate, tmp_path)


def test_candidate_workflow_cannot_be_the_trusted_control_revision(candidate, tmp_path):
    with pytest.raises(subprocess.CalledProcessError):
        export(candidate, tmp_path, workflow=candidate[2])


@pytest.mark.parametrize("name", ["Dockerfile", ".github/workflows/publish.yml", "app/.env", "app/key.pem", "scripts/probe.py"])
def test_candidate_cannot_change_build_publish_or_credential_paths(candidate, tmp_path, name):
    repo, base, _ = candidate
    target = repo / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("fixture\n")
    source.git("add", ".")
    source.git("commit", "-m", "disallowed path")
    head = source.git("rev-parse", "HEAD").decode().strip()
    source.git("update-ref", "refs/candidates/42", head)
    with pytest.raises(ValueError, match="candidate_control_or_unsafe_path"):
        export((repo, base, head), tmp_path)


def test_symlink_cannot_import_runner_files(candidate, tmp_path):
    repo, base, _ = candidate
    (repo / "app/link.py").symlink_to("../../outside.py")
    source.git("add", ".")
    source.git("commit", "-m", "symlink")
    head = source.git("rev-parse", "HEAD").decode().strip()
    source.git("update-ref", "refs/candidates/42", head)
    with pytest.raises(ValueError, match="non_regular_tree_entry"):
        export((repo, base, head), tmp_path)


def test_scan_requires_python_coverage_and_zero_findings():
    clean = {"Results": [{"Type": "python-pkg", "Class": "lang-pkgs"}]}
    image.scan_clean(clean)
    for invalid in ({}, {"Results": []}, {"Results": [{"Type": "debian", "Class": "os-pkgs"}]}):
        with pytest.raises(image.Failure):
            image.scan_clean(invalid)
    for key in ("Secrets", "Vulnerabilities", "Misconfigurations"):
        invalid = copy.deepcopy(clean)
        invalid["Results"][0][key] = [{"ID": "fixture-finding"}]
        with pytest.raises(image.Failure, match="scan_has_findings"):
            image.scan_clean(invalid)


def test_every_source_and_run_binding_is_checked():
    env = dict(BASE_SHA="a" * 40, SOURCE_SHA="b" * 40, WORKFLOW_SHA="c" * 40,
               DIFF_SHA256="d" * 64, REQUEST_ID="fixture", CANDIDATE_PR="42",
               GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2")
    packet = dict(repository=source.REPOSITORY, base_sha=env["BASE_SHA"],
                  source_sha=env["SOURCE_SHA"], workflow_sha=env["WORKFLOW_SHA"],
                  diff_sha256=env["DIFF_SHA256"], request_id="fixture", pull_request=42,
                  workflow_run_id="123", workflow_run_attempt="2")
    image.binding(packet, env)
    for key in packet:
        with pytest.raises(image.Failure):
            image.binding({**packet, key: "different"}, env)


def test_zero_exit_alone_cannot_claim_a_completed_runtime_probe():
    expected = dict(runtime_uid=10001, installer_modules_absent=True, uv_absent=True,
                    app_import_and_health_handlers="pass", external_service_integration_tested=False)
    image.probe_complete(json.dumps(expected) + "\n")
    for output in ("", "{}", "[]", "null", "PASS", json.dumps({**expected, "runtime_uid": 0}),
                   json.dumps({**expected, "app_import_and_health_handlers": "fail"}),
                   json.dumps({**expected, "installer_modules_absent": 1}),
                   json.dumps(expected) + "\n" + json.dumps(expected)):
        with pytest.raises(image.Failure):
            image.probe_complete(output)


@pytest.fixture
def layout(tmp_path):
    root = tmp_path / "layout"
    blobs = root / "blobs/sha256"
    blobs.mkdir(parents=True)

    def blob(data, media_type):
        content = json.dumps(data).encode()
        sha = hashlib.sha256(content).hexdigest()
        (blobs / sha).write_bytes(content)
        return {"mediaType": media_type, "digest": "sha256:" + sha, "size": len(content)}

    config = blob({"config": {"User": "10001", "Labels": {
        "org.opencontainers.image.revision": "a" * 40}}}, "application/vnd.oci.image.config.v1+json")
    runtime = blob({"config": config, "layers": []}, "application/vnd.oci.image.manifest.v1+json")
    runtime["platform"] = {"architecture": "amd64", "os": "linux"}
    attestation = blob({"config": config, "layers": []}, "application/vnd.oci.image.manifest.v1+json")
    attestation["platform"] = {"architecture": "unknown", "os": "unknown"}
    attestation["annotations"] = {"vnd.docker.reference.type": "attestation-manifest"}
    top = blob({"manifests": [runtime, attestation]}, "application/vnd.oci.image.index.v1+json")
    (root / "index.json").write_text(json.dumps({"manifests": [top]}))
    return root, top["digest"], config["digest"]


def test_oci_bytes_revision_user_and_link_are_checked(layout):
    root, index, config = layout
    image.inspect(root, index, "a" * 40)
    with pytest.raises(image.Failure, match="runtime_config_mismatch"):
        image.inspect(root, index, "b" * 40)
    link = root / "outside"
    link.symlink_to("/tmp")
    with pytest.raises(image.Failure, match="non_regular_oci_entry"):
        image.inspect(root, index, "a" * 40)
    link.unlink()
    (root / "blobs/sha256" / config[7:]).write_text("corrupted")
    with pytest.raises(image.Failure, match="oci_blob_digest_mismatch"):
        image.inspect(root, index, "a" * 40)
