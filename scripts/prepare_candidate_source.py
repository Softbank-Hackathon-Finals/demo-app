"""Export a pinned, unmerged PR as data; never execute candidate control files."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath

if __package__:
    from .prepare_image_source import git
else:
    from prepare_image_source import git

REPOSITORY = "Softbank-Hackathon-Finals/demo-app"  # renamed from demo-chat (11/8); GHCR/ECR names stay
DEPENDENCIES = {"pyproject.toml", "uv.lock", ".python-version"}
RECIPE = {"Dockerfile", ".dockerignore"}


def sha(value):
    if not re.fullmatch(r"[a-f0-9]{40}", value):
        raise ValueError("full_commit_required")
    if git("rev-parse", value + "^{commit}").decode().strip() != value:
        raise ValueError("commit_mismatch")
    return value


def allowed(name):
    path = PurePosixPath(name)
    return (not path.is_absolute() and ".." not in path.parts
            and (name in DEPENDENCIES or name.startswith("app/"))
            and not any(p.startswith(".") for p in path.parts if p != ".python-version")
            and path.suffix.lower() not in {".pem", ".key", ".p12", ".pfx", ".tfstate"}
            and not any(ord(c) < 32 or c == "\\" for c in name))


def prepare(base, source, workflow, pull, diff_digest, request_id, destination):
    for value in (base, source, workflow):
        sha(value)
    if not re.fullmatch(r"[1-9][0-9]{0,9}", pull):
        raise ValueError("invalid_pull_request")
    if not re.fullmatch(r"[a-f0-9]{64}", diff_digest):
        raise ValueError("invalid_diff_digest")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", request_id):
        raise ValueError("invalid_request_id")
    git("merge-base", "--is-ancestor", base, "origin/main")
    git("merge-base", "--is-ancestor", workflow, "origin/main")
    git("merge-base", "--is-ancestor", base, source)
    if git("rev-parse", f"refs/candidates/{pull}").decode().strip() != source:
        raise ValueError("pull_head_mismatch")
    merged = subprocess.run(["git", "merge-base", "--is-ancestor", source, "origin/main"],
                            capture_output=True)
    if merged.returncode != 1:
        raise ValueError("candidate_must_be_unmerged")
    diff = git("diff", "--binary", "--full-index", "--no-ext-diff", "--no-renames", base, source, "--")
    if hashlib.sha256(diff).hexdigest() != diff_digest:
        raise ValueError("diff_digest_mismatch")
    changed = [s.decode() for s in git("diff", "--name-only", "-z", "--no-renames", base, source, "--").split(b"\0") if s]
    if not changed or not all(allowed(s) for s in changed):
        raise ValueError("candidate_control_or_unsafe_path")
    files = {}
    # No tar extraction, filters, submodules, hooks or candidate scripts on runner.
    for revision, select in ((source, allowed), (workflow, lambda n: n in RECIPE)):
        for entry in filter(None, git("ls-tree", "-rz", revision).split(b"\0")):
            meta, raw_name = entry.split(b"\t", 1)
            name = raw_name.decode()
            mode, kind, identifier = meta.decode().split()
            if mode not in ("100644", "100755") or kind != "blob":
                raise ValueError("non_regular_tree_entry")
            if select(name):
                data = git("cat-file", "blob", identifier)
                if len(data) > 8 * 1024 * 1024:
                    raise ValueError("source_file_too_large")
                files[name] = data
    if not (DEPENDENCIES | RECIPE).issubset(files) or "app/main.py" not in files:
        raise ValueError("required_build_input_missing")
    if sum(map(len, files.values())) > 32 * 1024 * 1024:
        raise ValueError("source_context_too_large")
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    manifest = {"schema_version": 1, "repository": REPOSITORY, "base_sha": base,
                "source_sha": source, "workflow_sha": workflow, "pull_request": int(pull),
                "diff_sha256": diff_digest, "request_id": request_id,
                "changed_files": changed,
                "files": {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
                "trusted_recipe_files": sorted(RECIPE), "candidate_executed_on_runner": False}
    (destination.parent / "candidate-source.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (destination.parent / "candidate.diff").write_bytes(diff)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ("base", "source", "workflow", "pull", "diff", "request"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--context", type=Path, required=True)
    a = p.parse_args()
    try:
        prepare(a.base, a.source, a.workflow, a.pull, a.diff, a.request, a.context)
    except (ValueError, OSError, subprocess.CalledProcessError):
        print("candidate_source=REJECTED")
        return 1
    print("candidate_source=PINNED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
