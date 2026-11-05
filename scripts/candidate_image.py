"""Seal candidates in the build job; verify/copy bytes without execution in publishers."""

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

if __package__:
    from .private_image_transfer import (
        IMAGE,
        Failure,
        existing_target_digest,
        run,
        validate_destination,
        verify_layout,
    )
else:
    from private_image_transfer import (
        IMAGE,
        Failure,
        existing_target_digest,
        run,
        validate_destination,
        verify_layout,
    )


def read(path):
    return json.loads(path.read_text())


def write(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def scan_clean(report):
    results = report.get("Results")
    if not isinstance(results, list) or not results:
        raise Failure("scan_coverage_missing")
    if not any(r.get("Type") == "python-pkg" and r.get("Class") == "lang-pkgs" for r in results):
        raise Failure("python_scan_coverage_missing")
    if any(r.get("Vulnerabilities") or r.get("Secrets") or r.get("Misconfigurations") for r in results):
        raise Failure("scan_has_findings")


def probe_complete(output):
    expected = {
        "runtime_uid": 10001,
        "installer_modules_absent": True,
        "uv_absent": True,
        "app_import_and_health_handlers": "pass",
        "external_service_integration_tested": False,
    }
    try:
        result = json.loads(output)
    except (ValueError, TypeError):
        raise Failure("runtime_probe_result_missing") from None
    if result != expected or any(type(result[k]) is not type(v) for k, v in expected.items()):
        raise Failure("runtime_probe_result_incomplete")


def inspect(path, digest, source_sha):
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest):
        raise Failure("invalid_index_digest")
    for node in [path, *path.rglob("*")]:
        mode = node.lstat().st_mode
        if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            raise Failure("non_regular_oci_entry")
    index = read(path / "blobs/sha256" / digest[7:])
    top = read(path / "index.json")
    if len(top.get("manifests", [])) != 1 or top["manifests"][0].get("digest") != digest:
        raise Failure("layout_root_mismatch")
    inventory = verify_layout(path, digest, index)
    platforms = index.get("manifests", [])
    runtime = [m for m in platforms if m.get("platform") == {"architecture": "amd64", "os": "linux"}]
    if len(runtime) != 1 or not any(m.get("annotations", {}).get("vnd.docker.reference.type") == "attestation-manifest" for m in platforms):
        raise Failure("runtime_or_attestation_missing")
    manifest = read(path / "blobs/sha256" / runtime[0]["digest"][7:])
    config = read(path / "blobs/sha256" / manifest["config"]["digest"][7:])["config"]
    if config.get("User") != "10001" or config.get("Labels", {}).get("org.opencontainers.image.revision") != source_sha:
        raise Failure("runtime_config_mismatch")
    return index, inventory


def binding(packet, env):
    expected = {"repository": "Softbank-Hackathon-Finals/demo-chat",
                "base_sha": env["BASE_SHA"], "source_sha": env["SOURCE_SHA"],
                "workflow_sha": env["WORKFLOW_SHA"], "diff_sha256": env["DIFF_SHA256"],
                "request_id": env["REQUEST_ID"], "pull_request": int(env["CANDIDATE_PR"])}
    if any(packet.get(k) != v for k, v in expected.items()):
        raise Failure("candidate_binding_mismatch")
    if packet.get("workflow_run_id") != env["GITHUB_RUN_ID"] or packet.get("workflow_run_attempt") != env["GITHUB_RUN_ATTEMPT"]:
        raise Failure("candidate_run_mismatch")


def main():
    root = Path(os.environ["RUNNER_TEMP"])
    bundle = root / "candidate-bundle"
    phase = sys.argv[1]
    digest = os.environ["BUILD_DIGEST"]
    source_sha = os.environ["SOURCE_SHA"]
    if phase == "seal":
        packet = read(bundle / "candidate-source.json")
        packet.update(workflow_run_id=os.environ["GITHUB_RUN_ID"],
                      workflow_run_attempt=os.environ["GITHUB_RUN_ATTEMPT"])
        binding(packet, os.environ)
        inspect(bundle / "layout", digest, source_sha)
        scan_clean(read(root / "candidate-trivy.json"))
        run(["skopeo", "copy", "--override-os", "linux", "--override-arch", "amd64",
             "oci:" + str(bundle / "layout"), "docker-daemon:demo-candidate:probe"], "load_probe")
        probe = Path("scripts/check_runtime_image.py").read_text()
        output = run(["docker", "run", "--rm", "--network", "none", "--read-only", "--cap-drop=ALL",
             "--security-opt=no-new-privileges", "--pids-limit=128", "--memory=512m", "-i",
             "--entrypoint", ".venv/bin/python", "demo-candidate:probe", "-"],
            "isolated_runtime_probe", input_text=probe)
        probe_complete(output)
        packet.update(image=IMAGE, digest=digest, scanned_before_publish=True,
                      vulnerability_count=0, secret_match_count=0,
                      runtime_packaging_probe_verified=True,
                      runtime_probe_in_build_job_only=True)
        write(bundle / "candidate-receipt.json", packet)
        print("candidate_build_scan=PASS")
        return
    packet = read(bundle / "candidate-receipt.json")
    binding(packet, os.environ)
    if (packet.get("digest") != digest or packet.get("image") != IMAGE
            or packet.get("scanned_before_publish") is not True
            or packet.get("runtime_packaging_probe_verified") is not True):
        raise Failure("publication_binding_mismatch")
    if hashlib.sha256((bundle / "candidate.diff").read_bytes()).hexdigest() != packet["diff_sha256"]:
        raise Failure("candidate_diff_mismatch")
    index, source_blobs = inspect(bundle / "layout", digest, source_sha)
    if phase == "verify":
        print("candidate_binding_and_all_bytes=PASS")
        return
    scan_clean(read(root / "candidate-trivy.json"))
    auth = Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))) / "config.json"
    tag = "candidate-" + os.environ["GITHUB_RUN_ID"] + "-" + os.environ["GITHUB_RUN_ATTEMPT"]
    previous = None
    if phase == "ghcr":
        destination = IMAGE
    elif phase == "ecr":
        destination, account = os.environ["PRIVATE_ECR_REPOSITORY"], os.environ["EXPECTED_ACCOUNT_ID"]
        validate_destination(destination, account)
        identity = json.loads(run(["aws", "sts", "get-caller-identity", "--output", "json"], "identity"))
        if identity.get("Account") != account or ":assumed-role/bravo-private-image-publisher/" not in identity.get("Arn", ""):
            raise Failure("publisher_identity_mismatch")
        # Compare the published private GHCR bytes before making an ECR write.
        ghcr = root / "ghcr-readback"
        run(["skopeo", "copy", "--all", "--preserve-digests", "--src-authfile", str(auth),
             "docker://" + IMAGE + "@" + digest, "oci:" + str(ghcr)], "ghcr_download")
        if verify_layout(ghcr, digest, index) != source_blobs:
            raise Failure("ghcr_byte_mismatch")
        previous = existing_target_digest(tag)
        if previous is not None and previous != digest:
            raise Failure("immutable_destination_tag_conflict")
    else:
        raise Failure("unknown_phase")
    if previous is None:
        run(["skopeo", "copy", "--all", "--preserve-digests", "--dest-authfile", str(auth),
             "--digestfile", str(root / "pushed-digest"), "oci:" + str(bundle / "layout"),
             "docker://" + destination + ":" + tag], "publish")
        if (root / "pushed-digest").read_text().strip() != digest:
            raise Failure("published_digest_mismatch")
    target = root / (phase + "-verified")
    run(["skopeo", "copy", "--all", "--preserve-digests", "--src-authfile", str(auth),
         "docker://" + destination + "@" + digest, "oci:" + str(target)], "destination_download")
    if verify_layout(target, digest, index) != source_blobs:
        raise Failure("destination_byte_mismatch")
    result = {**packet, "destination": "ghcr-private" if phase == "ghcr" else "aws-ecr-private",
              "tag": tag, "all_blobs_identical": True, "verified_blob_count": len(source_blobs),
              "candidate_executed_in_publisher": False, "rebuilt": False,
              "t15_rehearsal_verified": False, "t31_deployment_verified": False}
    write(root / (phase + "-candidate-receipt.json"), result)
    print(json.dumps({"destination": result["destination"], "digest": digest,
                      "all_blobs_identical": True, "rebuilt": False}))


if __name__ == "__main__":
    try:
        main()
    except (Failure, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"candidate": "NOT_VERIFIED", "error": str(error) if isinstance(error, Failure) else "invalid_input"}))
        raise SystemExit(1) from None
