#!/usr/bin/env python3
"""Fetch two pinned ACL build files into an external cache."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", required=True, type=Path)
    parser.add_argument("--source-dir", type=Path,
                        help="Use an existing upstream clone instead of downloading.")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    repository = project.parent
    cache = args.cache_dir.expanduser().resolve()
    if cache == repository or repository in cache.parents:
        parser.error("Use a cache directory outside the Git repository.")
    manifest = json.loads((project / "paper/acl_style_v26/UPSTREAM.json").read_text())
    commit = manifest["commit"]
    records = [r for r in manifest["files"]
               if r["path"] in {"acl.sty", "acl_natbib.bst"}]
    if {r["path"] for r in records} != {"acl.sty", "acl_natbib.bst"}:
        raise ValueError("The required upstream file list is incomplete.")
    pending = []
    for record in records:
        name = record["path"]
        target = cache / name
        if target.is_symlink():
            raise ValueError(f"Refuse a symbolic link: {target}")
        if target.exists():
            data = target.read_bytes()
        elif args.source_dir:
            data = (args.source_dir / name).read_bytes()
        else:
            url = f"https://raw.githubusercontent.com/acl-org/acl-style-files/{commit}/{name}"
            request = urllib.request.Request(url, headers={"User-Agent": "ACL-build-dependency/1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
        if len(data) != record["bytes"] or digest(data) != record["sha256"]:
            raise ValueError(f"Upstream hash mismatch or conflicting cache file: {name}")
        pending.append((target, data))
    cache.mkdir(parents=True, exist_ok=True)
    for target, data in pending:
        if target.exists():
            continue
        with tempfile.NamedTemporaryFile(dir=cache, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(data)
        try:
            # Both hashes were checked before any file was written.
            if target.exists():
                if target.read_bytes() != data:
                    raise ValueError(f"Concurrent cache change: {target}")
            else:
                os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    print(json.dumps({"cache_dir": str(cache), "commit": commit,
                      "verified_files": [r["path"] for r in records]}, indent=2))


if __name__ == "__main__":
    main()
