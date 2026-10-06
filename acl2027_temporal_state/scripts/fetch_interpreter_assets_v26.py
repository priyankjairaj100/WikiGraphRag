#!/usr/bin/env python3
"""Fetch exact native assets outside Git. Preserve failures and verified resumes."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def hash_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(2**20), b""):
            h.update(block)
    return h


def release_owned_cache(root):
    # Large managed writes can leave recoverable sibling files in this directory.
    # Advise only files inside this exclusively owned asset directory.
    for path in root.iterdir():
        if path.is_file():
            with path.open("rb") as stream:
                os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)


def acquire(root, kind, spec):
    dst = root / spec["filename"]
    partial = root / (spec["filename"] + ".partial")
    started = time.monotonic()
    result = {"kind": kind, "url": spec["url"], "started_at_unix": time.time(),
              "expected_bytes": spec["bytes"], "expected_sha256": spec["sha256"]}
    try:
        if dst.exists():
            if dst.stat().st_size != spec["bytes"] or hash_file(dst).hexdigest() != spec["sha256"]:
                raise ValueError("existing_asset_mismatch")
            result.update(status="already_verified", bytes=dst.stat().st_size, sha256=spec["sha256"])
            return result
        size = partial.stat().st_size if partial.exists() else 0
        if size > spec["bytes"]:
            raise ValueError("partial_oversized")
        h = hash_file(partial) if size else hashlib.sha256()
        result.update(resume_bytes=size, resume_sha256=h.hexdigest())
        release_owned_cache(root)
        if shutil.disk_usage(root).free < spec["bytes"] - size + 2**30:
            raise RuntimeError("disk_reserve")
        headers = {"Range": f"bytes={size}-"} if size else {}
        request = urllib.request.Request(spec["url"], headers=headers)
        with urllib.request.urlopen(request, timeout=30) as source:
            if size and (source.status != 206 or not source.headers.get("Content-Range", "").startswith(f"bytes {size}-")):
                raise RuntimeError("range_resume_not_honored")
            with partial.open("ab" if size else "xb") as output:
                checkpoint = size
                while True:
                    if time.monotonic() - started > 1800:
                        raise RuntimeError("download_wall_limit")
                    current = int(Path("/sys/fs/cgroup/memory.current").read_text())
                    if current >= 7516192768:
                        output.flush()
                        os.fsync(output.fileno())
                        release_owned_cache(root)
                        if int(Path("/sys/fs/cgroup/memory.current").read_text()) >= 7516192768:
                            raise RuntimeError("memory_reserve")
                    block = source.read(2**20)
                    if not block:
                        break
                    size += len(block)
                    if size > spec["bytes"]:
                        raise ValueError("download_oversized")
                    output.write(block)
                    h.update(block)
                    if size - checkpoint >= 32 * 2**20:
                        output.flush()
                        os.fsync(output.fileno())
                        release_owned_cache(root)
                        checkpoint = size
                output.flush()
                os.fsync(output.fileno())
        result.update(bytes=size, sha256=h.hexdigest())
        if size != spec["bytes"] or h.hexdigest() != spec["sha256"]:
            raise ValueError("download_hash_or_size_mismatch")
        os.replace(partial, dst)
        release_owned_cache(root)
        result["status"] = "verified"
        return result
    except Exception as error:
        result.update(status="failed", error_type=type(error).__name__, error=str(error),
                      retained_partial_bytes=partial.stat().st_size if partial.exists() else 0)
        raise
    finally:
        result["elapsed_seconds"] = time.monotonic() - started
        record = root / ("acquisition_%s_%d.json" % (kind, time.time_ns()))
        with record.open("x") as stream:
            json.dump(result, stream, indent=2)
            stream.write("\n")
        print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", required=True)
    args = parser.parse_args()
    assets = Path(args.assets).resolve()
    if assets.is_relative_to(ROOT.parent):
        raise ValueError("assets_must_be_outside_repository")
    assets.mkdir(parents=True, exist_ok=True)
    config = json.loads((ROOT / "configs/interpreter_runtime_v26.json").read_text())
    for kind in ["runtime", "model"]:
        acquire(assets, kind, config[kind])
    directory = assets / "runtime"
    if not directory.exists():
        directory.mkdir()
        with tarfile.open(assets / config["runtime"]["filename"]) as archive:
            archive.extractall(directory, filter="data")


if __name__ == "__main__":
    main()
