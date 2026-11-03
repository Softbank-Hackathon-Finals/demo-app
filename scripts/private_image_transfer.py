"""Copy a reviewed private image with temporary registry credentials only."""

import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys

IMAGE = "ghcr.io/softbank-hackathon-finals/demo-chat"
RECORD_ROOT = "docs/evidence/image-publication-2026-11-03"


class Failure(RuntimeError):
    pass


def run(args, phase, *, input_text=None):
    result = subprocess.run(args, input=input_text, text=True, capture_output=True, timeout=600)
    if result.returncode:
        # CLI errors may contain private registry/account names or auth details.
        raise Failure(f"{phase}_failed_exit_{result.returncode}")
    return result.stdout


def read_record(name):
    text = run(["git", "show", f"origin/main:{RECORD_ROOT}/{name}"], "reviewed_record")
    return json.loads(text)


def validate_record(receipt, scan, source_sha, digest):
    if not re.fullmatch(r"[a-f0-9]{40}", source_sha or ""):
        raise Failure("invalid_source_sha")
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", digest or ""):
        raise Failure("invalid_source_digest")
    if (receipt.get("image"), receipt.get("source_sha"), receipt.get("digest")) != (
        IMAGE, source_sha, digest
    ):
        raise Failure("reviewed_publication_mismatch")
    if not all(receipt.get(k) is True for k in (
        "scanned_before_publish", "registry_pull_verified", "runtime_packaging_probe_verified",
        "attestation_manifest_present",
    )):
        raise Failure("publication_evidence_incomplete")
    if scan.get("workflow_conclusion") != "success" or scan.get("scan_before_publish") is not True:
        raise Failure("publication_scan_not_successful")
    if scan.get("vulnerability_count") != 0 or scan.get("secret_match_count") != 0:
        raise Failure("publication_scan_has_findings")


def verify_layout(path, digest, expected_index):
    blobs = path / "blobs" / "sha256"
    inventory = {}
    for blob in blobs.iterdir():
        if not blob.is_file() or not re.fullmatch(r"[a-f0-9]{64}", blob.name):
            raise Failure("unexpected_oci_blob")
        with blob.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if checksum != blob.name:
            raise Failure("oci_blob_digest_mismatch")
        inventory[blob.name] = blob.stat().st_size
    index_path = blobs / digest.removeprefix("sha256:")
    if not index_path.is_file() or json.loads(index_path.read_text()) != expected_index:
        raise Failure("reviewed_index_mismatch")
    pending = [expected_index]
    while pending:
        document = pending.pop()
        descriptors = document.get("manifests", []) + document.get("layers", [])
        if "config" in document:
            descriptors.append(document["config"])
        for item in descriptors:
            identifier = item["digest"]
            if not re.fullmatch(r"sha256:[a-f0-9]{64}", identifier):
                raise Failure("unexpected_digest_algorithm")
            key = identifier.removeprefix("sha256:")
            if inventory.get(key) != item["size"]:
                raise Failure("oci_descriptor_missing_or_size_mismatch")
            if "manifest" in item["mediaType"] or "index" in item["mediaType"]:
                pending.append(json.loads((blobs / key).read_text()))
    return inventory


def validate_destination(repository, account):
    if not re.fullmatch(r"[0-9]{12}", account or ""):
        raise Failure("expected_account_missing")
    if repository != f"{account}.dkr.ecr.ap-northeast-2.amazonaws.com/bravo/demo-chat":
        raise Failure("private_repository_boundary_mismatch")


def existing_target_digest(tag):
    result = subprocess.run(
        ["aws", "ecr", "describe-images", "--repository-name", "bravo/demo-chat",
         "--image-ids", "imageTag=" + tag, "--output", "json"],
        text=True, capture_output=True, timeout=60,
    )
    if result.returncode:
        if "(ImageNotFoundException)" in result.stderr:
            return None
        raise Failure("destination_inventory_failed")
    images = json.loads(result.stdout)["imageDetails"]
    if len(images) != 1:
        raise Failure("destination_inventory_ambiguous")
    return images[0]["imageDigest"]


def main():
    os.umask(0o077)
    phase = sys.argv[1]
    root = pathlib.Path(os.environ["RUNNER_TEMP"])
    source_sha, digest = os.environ["SOURCE_SHA"], os.environ["SOURCE_DIGEST"]
    receipt, scan = read_record("image-receipt.json"), read_record("scan-summary.json")
    validate_record(receipt, scan, source_sha, digest)
    expected_index = read_record("registry-index.json")
    auth = pathlib.Path(os.environ.get("DOCKER_CONFIG", str(pathlib.Path.home() / ".docker"))) / "config.json"
    source = root / "source-layout"
    if phase == "prepare":
        run(["git", "merge-base", "--is-ancestor", source_sha, "origin/main"], "source_not_on_main")
        print("reviewed_publication_binding=pass")
        return
    if phase == "download":
        run(["skopeo", "copy", "--all", "--preserve-digests", "--src-authfile", str(auth),
             "docker://" + IMAGE + "@" + digest, "oci:" + str(source)], "source_download")
        verify_layout(source, digest, expected_index)
        print("reviewed_source_index_and_all_blobs=pass")
        return
    if phase != "publish":
        raise Failure("unknown_phase")
    repository, account = os.environ["PRIVATE_ECR_REPOSITORY"], os.environ["EXPECTED_ACCOUNT_ID"]
    validate_destination(repository, account)
    identity = json.loads(run(["aws", "sts", "get-caller-identity", "--output", "json"], "identity"))
    if identity.get("Account") != account or ":assumed-role/bravo-private-image-publisher/" not in identity.get("Arn", ""):
        raise Failure("publisher_identity_mismatch")
    source_blobs = verify_layout(source, digest, expected_index)
    report = json.loads((root / "transfer-trivy.json").read_text())
    if not isinstance(report.get("Results"), list) or not report["Results"]:
        raise Failure("transfer_scan_evidence_missing")
    if any(r.get("Vulnerabilities") or r.get("Secrets") for r in report.get("Results", [])):
        raise Failure("transfer_scan_has_findings")
    existing = existing_target_digest("sha-" + source_sha)
    if existing is None:
        run(["skopeo", "copy", "--all", "--preserve-digests", "--dest-authfile", str(auth),
             "oci:" + str(source), "docker://" + repository + ":sha-" + source_sha], "private_copy")
    elif existing != digest:
        raise Failure("immutable_destination_tag_conflict")
    target = root / "destination-layout"
    run(["skopeo", "copy", "--all", "--preserve-digests", "--src-authfile", str(auth),
         "docker://" + repository + "@" + digest, "oci:" + str(target)], "destination_download")
    destination_blobs = verify_layout(target, digest, expected_index)
    if source_blobs != destination_blobs:
        raise Failure("destination_blob_inventory_mismatch")
    destination = repository + "@" + digest
    run(["docker", "pull", "--platform", "linux/amd64", destination], "destination_runtime_pull")
    info = json.loads(run(["docker", "image", "inspect", destination], "runtime_inspect"))[0]
    if info["Config"]["Labels"].get("org.opencontainers.image.revision") != source_sha or info["Config"].get("User") != "10001":
        raise Failure("runtime_revision_or_user_mismatch")
    probe = (root / "image-source/scripts/check_runtime_image.py").read_text()
    run(["docker", "run", "--rm", "--network", "none", "-i", "--platform", "linux/amd64",
         destination, ".venv/bin/python", "-"], "destination_runtime_probe", input_text=probe)
    result = {"source_image": IMAGE, "source_sha": source_sha, "digest": digest,
              "destination_registry": "aws-ecr-private", "repository_name": "bravo/demo-chat",
              "account_and_publisher_boundary_matches": True, "oidc_session_seconds": 900,
              "vulnerability_count": 0, "secret_match_count": 0,
              "index_and_attestation_blobs_identical": True, "verified_blob_count": len(source_blobs),
              "destination_pull_verified": True, "runtime_probe_verified": True,
              "organization_visibility_changed": False, "cluster_pull_verified": False}
    (root / "private-transfer-receipt.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except (Failure, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
        print(json.dumps({"transfer": "NOT_VERIFIED", "error_code": str(error) if isinstance(error, Failure) else "invalid_local_state"}))
        raise SystemExit(1) from None
