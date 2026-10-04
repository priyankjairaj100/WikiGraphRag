#!/usr/bin/env python3
"""Exact-byte relocation of the acquired weight; no model execution."""
import hashlib
import json
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
RECEIPT = ROOT / "results/reader_asset_relocation_v16.json"
DESTINATION = Path("/workspace/scratch/bdef663e3dfc/wikigraph_v16_external/assets")
BLOCK = 1024 * 1024
WINDOW = 32 * BLOCK


def main():
    acquisition_path = ROOT / "results/reader_acquisition_v16.json"
    acquisition = json.loads(acquisition_path.read_text())
    source = Path(acquisition["external_final_path"])
    target = DESTINATION / source.name
    assert acquisition["status"] == "completed"
    assert source.is_file() and not source.is_symlink()
    assert not RECEIPT.exists() and not target.exists()
    DESTINATION.mkdir(parents=True, exist_ok=True)
    assert os.statvfs(DESTINATION).f_bavail * os.statvfs(DESTINATION).f_frsize > acquisition["final_bytes"] + WINDOW
    before = source.stat()
    r = {"schema_version": "reader_asset_relocation_v16", "status": "started",
         "started_unix_seconds": time.time(), "source_path": str(source), "target_path": str(target),
         "expected_bytes": acquisition["final_bytes"], "expected_sha256": acquisition["stored_file_sha256"],
         "acquisition_receipt_sha256": hashlib.sha256(acquisition_path.read_bytes()).hexdigest(),
         "relocator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
         "block_bytes": BLOCK, "flush_and_dontneed_interval_bytes": WINDOW,
         "source_replacement": "atomic_symlink_only_after_exact_target_verification",
         "memory_before_bytes": int(Path("/sys/fs/cgroup/memory.current").read_text()),
         "peak_memory_current_bytes": 0, "copied_bytes": 0, "model_loads": 0, "completion_calls": 0}
    def save():
        RECEIPT.write_text(json.dumps(r, indent=2) + "\n")
    save()
    def sample():
        r["peak_memory_current_bytes"] = max(r["peak_memory_current_bytes"],
            int(Path("/sys/fs/cgroup/memory.current").read_text()))
    try:
        checksum = hashlib.sha256()
        with source.open("rb", buffering=0) as incoming, target.open("xb", buffering=0) as outgoing:
            while True:
                block = incoming.read(BLOCK)
                if not block:
                    break
                written = outgoing.write(block)
                assert written == len(block)
                checksum.update(block)
                r["copied_bytes"] += len(block)
                sample()
                if r["copied_bytes"] % WINDOW == 0:
                    os.fsync(outgoing.fileno())
                    os.posix_fadvise(outgoing.fileno(), r["copied_bytes"] - WINDOW, WINDOW, os.POSIX_FADV_DONTNEED)
                    save()
            os.fsync(outgoing.fileno())
            os.posix_fadvise(outgoing.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        r["source_stream_sha256"] = checksum.hexdigest()
        assert r["copied_bytes"] == r["expected_bytes"] and checksum.hexdigest() == r["expected_sha256"]
        verify = hashlib.sha256()
        n = 0
        with target.open("rb", buffering=0) as stream:
            while True:
                block = stream.read(BLOCK)
                if not block:
                    break
                verify.update(block)
                n += len(block)
                sample()
                if n % WINDOW == 0:
                    os.posix_fadvise(stream.fileno(), n - WINDOW, WINDOW, os.POSIX_FADV_DONTNEED)
            os.posix_fadvise(stream.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
        r["target_bytes"] = n
        r["target_sha256"] = verify.hexdigest()
        assert n == r["expected_bytes"] and verify.hexdigest() == r["expected_sha256"]
        after = source.stat()
        assert (before.st_ino, before.st_size, before.st_mtime_ns) == (after.st_ino, after.st_size, after.st_mtime_ns)
        link = source.with_name(source.name + ".relocation_symlink")
        assert not link.exists() and not link.is_symlink()
        link.symlink_to(target)
        os.replace(link, source)
        assert source.is_symlink() and source.resolve() == target
        r["status"] = "completed"
        r["source_is_symlink"] = True
    except BaseException as error:
        r["status"] = "failed"
        r["error_type"] = type(error).__name__
        r["partial_target_retained"] = target.exists()
        raise
    finally:
        r["finished_unix_seconds"] = time.time()
        r["memory_after_bytes"] = int(Path("/sys/fs/cgroup/memory.current").read_text())
        r["shm_available_after_bytes"] = os.statvfs("/dev/shm").f_bavail * os.statvfs("/dev/shm").f_frsize
        save()
    print(json.dumps(r))


if __name__ == "__main__":
    main()
