"""Create a build context from a reviewed main commit, excluding local files."""

import argparse
import hashlib
import io
import json
import re
import subprocess
import tarfile
from pathlib import Path


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def prepare(source, destination, receipt):
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("source must be a full commit SHA")
    if git("rev-parse", source + "^{commit}").decode().strip() != source:
        raise ValueError("source must identify a commit")
    git("merge-base", "--is-ancestor", source, "origin/main")
    entries = git("ls-tree", "-rz", source).split(b"\0")
    names = []
    for entry in filter(None, entries):
        meta, encoded_name = entry.split(b"\t", 1)
        name = encoded_name.decode()
        path = Path(name)
        if meta.startswith(b"120000") or meta.startswith(b"160000"):
            raise ValueError("symlink/submodule context requires explicit review")
        if any(p in (".git", ".venv", ".aws", ".kube") for p in path.parts):
            raise ValueError("credential/runtime directory must not be in source")
        if (path.name == ".env" or path.name.startswith(".env.")) and path.name != ".env.example":
            raise ValueError("local environment file must not be in source")
        if path.suffix.lower() in (".pem", ".key", ".p12", ".pfx", ".tfstate"):
            raise ValueError("credential/state file must not be in source")
        names.append(name)
    archive = git("archive", "--format=tar", source)
    destination.mkdir(parents=True, exist_ok=False)
    with tarfile.open(fileobj=io.BytesIO(archive)) as tree:
        tree.extractall(destination, filter="data")
    result = {"source_sha": source, "source_archive_sha256": hashlib.sha256(archive).hexdigest(),
              "tracked_file_count": len(names), "files": names, "local_untracked_files_included": False}
    receipt.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_sha")
    parser.add_argument("context", type=Path)
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    try:
        result = prepare(args.source_sha, args.context, args.receipt)
    except (ValueError, OSError, subprocess.CalledProcessError):
        print("Reviewed tracked-source preparation failed.")
        return 1
    print(json.dumps({"source_sha": result["source_sha"], "tracked_file_count": result["tracked_file_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
