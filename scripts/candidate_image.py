"""Seal candidates in the build job; verify/copy bytes without execution in publishers."""

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

ARCHITECTURES = ("amd64", "arm64")
PLATFORMS = ["linux/" + arch for arch in ARCHITECTURES]

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


def scan_summary(report):
    """Expose package advisories and counts, never matched secrets or source snippets."""
    results = report.get("Results")
    if not isinstance(results, list) or not results or not all(isinstance(r, dict) for r in results):
        return {"scan_report": "unavailable"}
    vulnerabilities = [v for r in results for v in r.get("Vulnerabilities", [])]
    fields = ("VulnerabilityID", "PkgName", "InstalledVersion", "FixedVersion", "Severity")
    return {
        "scan_report": "available",
        "vulnerability_count": len(vulnerabilities),
        "vulnerabilities": [{k: str(v.get(k, ""))[:256] for k in fields} for v in vulnerabilities[:200]],
        "vulnerability_list_truncated": len(vulnerabilities) > 200,
        "secret_match_count": sum(len(r.get("Secrets", [])) for r in results),
        "misconfiguration_count": sum(len(r.get("Misconfigurations", [])) for r in results),
        "raw_matches_or_snippets_included": False,
    }


def probe_complete(output):
    """Require the expected self-report; candidate imports can forge this output."""
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
    runtimes = runtime_manifests(index)
    attestations = [m for m in platforms if m.get("annotations", {}).get("vnd.docker.reference.type") == "attestation-manifest"]
    if len(platforms) != 4 or len(attestations) != 2:
        raise Failure("runtime_or_attestation_missing")
    for arch, runtime in runtimes.items():
        bound = [m for m in attestations if m["annotations"].get("vnd.docker.reference.digest") == runtime["digest"]]
        if len(bound) != 1 or bound[0].get("platform") != {"architecture": "unknown", "os": "unknown"}:
            raise Failure("runtime_attestation_binding_mismatch")
        manifest = read(path / "blobs/sha256" / runtime["digest"][7:])
        config = read(path / "blobs/sha256" / manifest["config"]["digest"][7:])
        if (config.get("architecture") != arch or config.get("os") != "linux"
                or config.get("config", {}).get("User") != "10001"
                or config.get("config", {}).get("Labels", {}).get("org.opencontainers.image.revision") != source_sha):
            raise Failure("runtime_config_mismatch")
    return index, inventory


def runtime_manifests(index):
    runtimes = {}
    for arch in ARCHITECTURES:
        matches = [m for m in index.get("manifests", [])
                   if m.get("platform", {}).get("os") == "linux"
                   and m["platform"].get("architecture") == arch
                   and m["platform"].get("variant", "") in (("", "v8") if arch == "arm64" else ("",))]
        if len(matches) != 1:
            raise Failure("runtime_platform_missing_or_duplicate")
        runtimes[arch] = matches[0]
    return runtimes


def platform_scans(root, layout, index):
    """Require each report to name the exact architecture's image configuration."""
    for arch, runtime in runtime_manifests(index).items():
        report = read(root / f"candidate-trivy-{arch}.json")
        manifest = read(layout / "blobs/sha256" / runtime["digest"][7:])
        metadata = report.get("Metadata", {})
        config = metadata.get("ImageConfig", {})
        if (metadata.get("ImageID") != manifest["config"]["digest"]
                or config.get("architecture") != arch or config.get("os") != "linux"):
            raise Failure("scan_platform_binding_mismatch")
        scan_clean(report)


def prepare_scans(root, layout, index):
    """Give archive scanners one exact manifest; platform flags may be ignored."""
    for arch, runtime in runtime_manifests(index).items():
        destination = root / f"candidate-scan-{arch}"
        destination.mkdir()
        blobs = destination / "blobs/sha256"
        blobs.mkdir(parents=True)
        manifest = read(layout / "blobs/sha256" / runtime["digest"][7:])
        for descriptor in [runtime, manifest["config"], *manifest.get("layers", [])]:
            name = descriptor["digest"][7:]
            shutil.copyfile(layout / "blobs/sha256" / name, blobs / name)
        write(destination / "oci-layout", {"imageLayoutVersion": "1.0.0"})
        write(destination / "index.json", {"schemaVersion": 2, "manifests": [runtime]})
        verify_layout(destination, runtime["digest"], manifest)


def binding(packet, env):
    expected = {"repository": "Softbank-Hackathon-Finals/demo-app",
                "base_sha": env["BASE_SHA"], "source_sha": env["SOURCE_SHA"],
                "workflow_sha": env["WORKFLOW_SHA"], "diff_sha256": env["DIFF_SHA256"],
                "request_id": env["REQUEST_ID"], "pull_request": int(env["CANDIDATE_PR"])}
    if any(packet.get(k) != v for k, v in expected.items()):
        raise Failure("candidate_binding_mismatch")
    producer = env["BUILD_RUN_ATTEMPT"]
    current = env["GITHUB_RUN_ATTEMPT"]
    if (not re.fullmatch(r"[1-9][0-9]*", producer)
            or not re.fullmatch(r"[1-9][0-9]*", current)
            or int(producer) > int(current)
            or packet.get("workflow_run_id") != env["GITHUB_RUN_ID"]
            or packet.get("workflow_run_attempt") != producer):
        raise Failure("candidate_run_mismatch")


def main():
    root = Path(os.environ["RUNNER_TEMP"])
    bundle = root / "candidate-bundle"
    phase = sys.argv[1]
    if phase == "scan-summary":
        summary = {}
        for arch in ARCHITECTURES:
            path = root / f"candidate-trivy-{arch}.json"
            summary[arch] = scan_summary(read(path)) if path.is_file() else {"scan_report": "unavailable"}
        write(root / "candidate-scan-summary.json", summary)
        print(json.dumps(summary))
        return
    digest = os.environ["BUILD_DIGEST"]
    source_sha = os.environ["SOURCE_SHA"]
    if phase == "prepare-scans":
        index, _ = inspect(bundle / "layout", digest, source_sha)
        prepare_scans(root, bundle / "layout", index)
        print("architecture_scan_inputs=PASS")
        return
    if phase == "seal":
        packet = read(bundle / "candidate-source.json")
        packet.update(workflow_run_id=os.environ["GITHUB_RUN_ID"],
                      workflow_run_attempt=os.environ["GITHUB_RUN_ATTEMPT"])
        binding(packet, os.environ)
        index, _ = inspect(bundle / "layout", digest, source_sha)
        platform_scans(root, bundle / "layout", index)
        probe = Path("scripts/check_runtime_image.py").read_text()
        for arch in ARCHITECTURES:
            tag = "demo-candidate:probe-" + arch
            run(["skopeo", "copy", "--override-os", "linux", "--override-arch", arch,
                 "oci:" + str(bundle / "layout"), "docker-daemon:" + tag], "load_probe")
            output = run(["docker", "run", "--rm", "--platform", "linux/" + arch,
                 "--network", "none", "--read-only", "--cap-drop=ALL",
                 "--security-opt=no-new-privileges", "--pids-limit=128", "--memory=512m", "-i",
                 "--entrypoint", ".venv/bin/python", tag, "-"],
                "isolated_runtime_probe", input_text=probe)
            probe_complete(output)
        packet.update(image=IMAGE, digest=digest, scanned_before_publish=True,
                      scanned_platforms=PLATFORMS, runtime_probe_platforms=PLATFORMS,
                      vulnerability_count=0, secret_match_count=0,
                      runtime_packaging_probe_verified=False,
                      runtime_probe_evidence="candidate_process_self_report",
                      runtime_probe_self_report="pass",
                      runtime_probe_in_build_job_only=True)
        write(bundle / "candidate-receipt.json", packet)
        print("candidate_build_scan=PASS")
        return
    packet = read(bundle / "candidate-receipt.json")
    binding(packet, os.environ)
    artifact_id = os.environ["BUILD_ARTIFACT_ID"]
    if not re.fullmatch(r"[1-9][0-9]*", artifact_id):
        raise Failure("build_artifact_identity_missing")
    if (packet.get("digest") != digest or packet.get("image") != IMAGE
            or packet.get("scanned_before_publish") is not True
            or packet.get("scanned_platforms") != PLATFORMS
            or packet.get("runtime_probe_platforms") != PLATFORMS
            or packet.get("runtime_packaging_probe_verified") is not False
            or packet.get("runtime_probe_evidence") != "candidate_process_self_report"
            or packet.get("runtime_probe_self_report") != "pass"):
        raise Failure("publication_binding_mismatch")
    if hashlib.sha256((bundle / "candidate.diff").read_bytes()).hexdigest() != packet["diff_sha256"]:
        raise Failure("candidate_diff_mismatch")
    index, source_blobs = inspect(bundle / "layout", digest, source_sha)
    if phase == "verify":
        print("candidate_binding_and_all_bytes=PASS")
        return
    platform_scans(root, bundle / "layout", index)
    auth = Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))) / "config.json"
    tag = "candidate-" + packet["workflow_run_id"] + "-" + packet["workflow_run_attempt"]
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
              "build_artifact_id": artifact_id, "publisher_run_attempt": os.environ["GITHUB_RUN_ATTEMPT"],
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
