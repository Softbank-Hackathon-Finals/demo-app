import copy
import hashlib
import json

import pytest

from scripts.private_image_transfer import (
    IMAGE,
    Failure,
    publication_root,
    validate_destination,
    validate_record,
    verify_layout,
)


def publication():
    root = "docs/evidence/image-publication-2026-11-03/"
    with open(root + "image-receipt.json") as stream:
        receipt = json.load(stream)
    with open(root + "scan-summary.json") as stream:
        scan = json.load(stream)
    return receipt, scan


def test_only_reviewed_repository_source_and_digest_are_accepted():
    receipt, scan = publication()
    validate_record(receipt, scan, receipt["source_sha"], receipt["digest"])
    for field, value in (("image", IMAGE + "-other"), ("source_sha", "a" * 40),
                         ("digest", "sha256:" + "b" * 64)):
        changed = {**receipt, field: value}
        with pytest.raises(Failure, match="mismatch"):
            validate_record(changed, scan, receipt["source_sha"], receipt["digest"])


def test_failed_or_missing_publication_evidence_is_rejected():
    receipt, scan = publication()
    for changed in ({**receipt, "scanned_before_publish": False},
                    {**receipt, "runtime_packaging_probe_verified": False}):
        with pytest.raises(Failure):
            validate_record(changed, scan, receipt["source_sha"], receipt["digest"])
    for changed in ({**scan, "vulnerability_count": 1}, {**scan, "secret_match_count": 1},
                    {**scan, "workflow_conclusion": "failure"}):
        with pytest.raises(Failure):
            validate_record(receipt, changed, receipt["source_sha"], receipt["digest"])


def test_exact_private_destination_only():
    expected = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/bravo/demo-chat"
    validate_destination(expected, "000000000000")
    for altered in (expected + "-other", expected + "/nested", expected.replace("000000000000", "111111111111"),
                    expected.replace("ap-northeast-2", "us-east-1")):
        with pytest.raises(Failure):
            validate_destination(altered, "000000000000")


def layout(tmp_path):
    blobs = tmp_path / "blobs/sha256"
    blobs.mkdir(parents=True)

    def blob(data, media_type):
        raw = json.dumps(data).encode()
        digest = hashlib.sha256(raw).hexdigest()
        (blobs / digest).write_bytes(raw)
        return {"mediaType": media_type, "digest": "sha256:" + digest, "size": len(raw)}

    config = blob({"architecture": "amd64"}, "application/vnd.oci.image.config.v1+json")
    layer = blob({"fixture": "layer"}, "application/vnd.oci.image.layer.v1.tar")
    manifest = blob({"schemaVersion": 2, "config": config, "layers": [layer]},
                    "application/vnd.oci.image.manifest.v1+json")
    index = {"schemaVersion": 2, "manifests": [manifest]}
    descriptor = blob(index, "application/vnd.oci.image.index.v1+json")
    return descriptor["digest"], index, blobs, layer


def test_layout_verifies_all_referenced_bytes(tmp_path):
    digest, index, _, _ = layout(tmp_path)
    assert len(verify_layout(tmp_path, digest, index)) == 4


def test_tampered_layer_is_rejected(tmp_path):
    digest, index, blobs, layer = layout(tmp_path)
    (blobs / layer["digest"].removeprefix("sha256:")).write_bytes(b"tampered")
    with pytest.raises(Failure, match="digest_mismatch"):
        verify_layout(tmp_path, digest, index)


def test_missing_referenced_layer_is_rejected(tmp_path):
    digest, index, blobs, layer = layout(tmp_path)
    (blobs / layer["digest"].removeprefix("sha256:")).unlink()
    with pytest.raises(Failure, match="missing_or_size"):
        verify_layout(tmp_path, digest, index)


def test_different_reviewed_index_is_rejected(tmp_path):
    digest, index, _, _ = layout(tmp_path)
    changed = copy.deepcopy(index)
    changed["manifests"] = []
    with pytest.raises(Failure, match="reviewed_index_mismatch"):
        verify_layout(tmp_path, digest, changed)


@pytest.mark.parametrize("root", ["docs/evidence/image-publication-2026-11-03", "docs/evidence/image-publication-2026-11-08"])
def test_publication_root_accepts_only_reviewed_dated_locations(root):
    assert publication_root(root) == root


@pytest.mark.parametrize("root", ["../other", "HEAD:secret", "docs/evidence/image-publication-2026-11-08/../other", "docs/evidence/image-publication-2026-02-30", "docs/evidence/image-publication-2026-11-08\n"])
def test_publication_root_rejects_other_paths(root):
    with pytest.raises(Failure):
        publication_root(root)


def test_new_publication_is_separate_from_legacy_default():
    from scripts.private_image_transfer import RECORD_ROOT
    assert RECORD_ROOT == "docs/evidence/image-publication-2026-11-03"
    root = "docs/evidence/image-publication-2026-11-08/"
    with open(root + "image-receipt.json") as stream:
        receipt = json.load(stream)
    with open(root + "scan-summary.json") as stream:
        scan = json.load(stream)
    validate_record(receipt, scan, receipt["source_sha"], receipt["digest"])
    old, _ = publication()
    with pytest.raises(Failure, match="mismatch"):
        validate_record(receipt, scan, old["source_sha"], old["digest"])
