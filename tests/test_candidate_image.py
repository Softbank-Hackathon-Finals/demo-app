import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

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


def test_scan_failure_summary_omits_secret_matches_and_source_snippets():
    report = {"Results": [{"Target": "private-source-path", "Secrets": [
        {"Match": "synthetic-secret-value", "Code": {"Lines": [{"Content": "synthetic-source"}]}}],
        "Vulnerabilities": [{"VulnerabilityID": "CVE-2026-109518", "PkgName": "apt",
                             "InstalledVersion": "3.0.3", "FixedVersion": "3.0.4", "Severity": "HIGH",
                             "Description": "private-extra-field"}]}]}
    summary = image.scan_summary(report)
    assert summary["secret_match_count"] == 1
    assert summary["vulnerability_count"] == 1
    assert summary["vulnerabilities"][0]["FixedVersion"] == "3.0.4"
    rendered = json.dumps(summary)
    for forbidden in ("private-source-path", "synthetic-secret-value", "synthetic-source", "private-extra-field"):
        assert forbidden not in rendered
    # Diagnostics do not change the publication gate's rejection.
    with pytest.raises(image.Failure):
        image.scan_clean(report)


@pytest.mark.parametrize("report", [{}, {"Results": []}, {"Results": [None]}])
def test_missing_scan_report_is_not_reported_clean(report):
    assert image.scan_summary(report) == {"scan_report": "unavailable"}


def test_every_source_and_run_binding_is_checked():
    env = dict(BASE_SHA="a" * 40, SOURCE_SHA="b" * 40, WORKFLOW_SHA="c" * 40,
               DIFF_SHA256="d" * 64, REQUEST_ID="fixture", CANDIDATE_PR="42",
               GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="2", BUILD_RUN_ATTEMPT="2")
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


@pytest.fixture
def bundle(layout, tmp_path, monkeypatch):
    root, index, _ = layout
    packet_root = tmp_path / "candidate-bundle"
    packet_root.mkdir()
    shutil.copytree(root, packet_root / "layout")
    diff = b"synthetic candidate diff\n"
    (packet_root / "candidate.diff").write_bytes(diff)
    env = dict(RUNNER_TEMP=str(tmp_path), BASE_SHA="b" * 40, SOURCE_SHA="a" * 40,
               WORKFLOW_SHA="c" * 40, DIFF_SHA256=hashlib.sha256(diff).hexdigest(),
               REQUEST_ID="fixture", CANDIDATE_PR="42", BUILD_DIGEST=index,
               GITHUB_RUN_ID="123", GITHUB_RUN_ATTEMPT="1", BUILD_RUN_ATTEMPT="1",
               BUILD_ARTIFACT_ID="456", EXPECTED_ACCOUNT_ID="123456789012",
               PRIVATE_ECR_REPOSITORY="123456789012.dkr.ecr.ap-northeast-2.amazonaws.com/bravo/demo-chat")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    packet = dict(repository=source.REPOSITORY, base_sha=env["BASE_SHA"], source_sha=env["SOURCE_SHA"],
                  workflow_sha=env["WORKFLOW_SHA"], diff_sha256=env["DIFF_SHA256"],
                  request_id="fixture", pull_request=42)
    image.write(packet_root / "candidate-source.json", packet)
    image.write(tmp_path / "candidate-trivy.json", {"Results": [{"Type": "python-pkg", "Class": "lang-pkgs"}]})
    return packet_root, packet, index


def test_forged_probe_stdout_cannot_issue_independent_verification(bundle, monkeypatch):
    root, _, _ = bundle
    expected = dict(runtime_uid=10001, installer_modules_absent=True, uv_absent=True,
                    app_import_and_health_handlers="pass", external_service_integration_tested=False)
    # A candidate can emit the whole expected report and exit before any checks.
    forged = subprocess.check_output([sys.executable, "-c",
        "print(" + repr(json.dumps(expected)) + "); raise SystemExit(0)"], text=True)
    monkeypatch.setattr(image, "run", lambda *args, **kwargs: forged)
    monkeypatch.setattr(sys, "argv", ["candidate_image.py", "seal"])
    image.main()
    receipt = image.read(root / "candidate-receipt.json")
    assert receipt["runtime_packaging_probe_verified"] is False
    assert receipt["runtime_probe_evidence"] == "candidate_process_self_report"
    assert receipt["runtime_probe_self_report"] == "pass"


@pytest.mark.parametrize("phase", ["ghcr", "ecr"])
def test_publisher_partial_rerun_keeps_producer_digest_and_tag(bundle, monkeypatch, phase):
    root, packet, digest_value = bundle
    packet.update(workflow_run_id="123", workflow_run_attempt="1", image=image.IMAGE,
                  digest=digest_value, scanned_before_publish=True,
                  runtime_packaging_probe_verified=False,
                  runtime_probe_evidence="candidate_process_self_report", runtime_probe_self_report="pass")
    image.write(root / "candidate-receipt.json", packet)
    monkeypatch.setattr(sys, "argv", ["candidate_image.py", phase])
    effects = []
    reads = []

    def inventory(tag):
        reads.append(tag)
        return digest_value if effects else None

    def transport(args, operation, **kwargs):
        if operation == "identity":
            return json.dumps({"Account": "123456789012",
                               "Arn": "arn:aws:sts::123456789012:assumed-role/bravo-private-image-publisher/fixture"})
        if operation in ("ghcr_download", "destination_download"):
            shutil.copytree(root / "layout", Path(args[-1][4:]), dirs_exist_ok=True)
        elif operation == "publish":
            effects.append(args[-1])
            if len(effects) == 1:
                # ECR write committed, but the client received no success response.
                raise image.Failure("publish_response_lost")
            (root.parent / "pushed-digest").write_text(digest_value)
        else:
            raise AssertionError(operation)
        return ""

    monkeypatch.setattr(image, "existing_target_digest", inventory)
    monkeypatch.setattr(image, "run", transport)
    with pytest.raises(image.Failure, match="publish_response_lost"):
        image.main()
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    image.main()
    receipt = image.read(root.parent / (phase + "-candidate-receipt.json"))
    assert receipt["tag"] == "candidate-123-1"
    assert receipt["digest"] == digest_value
    assert receipt["workflow_run_attempt"] == "1"
    assert receipt["publisher_run_attempt"] == "2"
    assert receipt["build_artifact_id"] == "456"
    assert receipt["rebuilt"] is False
    assert all(effect.endswith(":candidate-123-1") for effect in effects)
    if phase == "ecr":
        assert len(effects) == 1
        assert reads == ["candidate-123-1", "candidate-123-1"]
    count = len(effects)
    monkeypatch.setenv("GITHUB_RUN_ID", "other-run")
    with pytest.raises(image.Failure, match="candidate_run_mismatch"):
        image.main()
    assert len(effects) == count
