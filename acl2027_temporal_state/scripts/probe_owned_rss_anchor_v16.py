#!/usr/bin/env python3
"""Authored real-subprocess audit of namespace-aware owned-group RSS.

Only this probe's own small Python children are launched and signalled.
No language model, natural source or validator execution is used.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import resource
import selectors
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
ALLOCATION_BYTES = 40 * 2**20

CHILD = r'''
import json,os,pathlib,time
memory=bytearray(40*2**20)
for offset in range(0,len(memory),4096): memory[offset]=71
status=pathlib.Path('/proc/self/status').read_text()
fields={line.split(':',1)[0]:line.split(':',1)[1].strip() for line in status.splitlines() if ':' in line}
print(json.dumps({'inner_pid':os.getpid(),'inner_pgid':os.getpgrp(),
 'outer_pid':int(os.readlink('/proc/self')),'pid_namespace':os.readlink('/proc/self/ns/pid'),
 'NSpid':[int(x) for x in fields['NSpid'].split()],
 'NSpgid':[int(x) for x in fields['NSpgid'].split()],
 'self_RSS_bytes':int(fields['VmRSS'].split()[0])*1024,'allocation_bytes':len(memory)}),flush=True)
while True: time.sleep(.1)
'''


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def strict_status_rss(text):
    matches = re.findall(r"^VmRSS:\s+([0-9]+) kB\s*$", text, re.M)
    if len(matches) != 1 or int(matches[0]) <= 0:
        raise ValueError("live_RSS_missing_malformed_or_nonpositive")
    return int(matches[0]) * 1024


def legacy_function(path):
    tree = ast.parse(Path(path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "rss_bytes")
    namespace = {"Path": Path, "re": re}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["rss_bytes"]


def limits():
    resource.setrlimit(resource.RLIMIT_AS, (128 * 2**20,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def run(helper_path, helper_function, output):
    helper_path, output = Path(helper_path).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("probe_receipt_exists_no_overwrite")
    helper_hash = sha(helper_path)
    spec = importlib.util.spec_from_file_location("rss_helper_under_review", helper_path)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    if sha(helper_path) != helper_hash:
        raise ValueError("helper_changed_during_import")
    scanner = getattr(helper, helper_function)
    legacy_path = ROOT / "scripts/run_full_dts_v16_2.py"
    old_rss = legacy_function(legacy_path)
    r = {"schema_version": "owned_rss_anchor_real_subprocess_probe_v16", "status": "started",
         "probe_sha256": sha(__file__), "helper_path": str(helper_path.relative_to(ROOT)),
         "helper_sha256": helper_hash, "helper_function": helper_function,
         "legacy_driver_sha256": sha(legacy_path), "started_unix_seconds": time.time(),
         "authored_child_allocation_bytes": ALLOCATION_BYTES,
         "model_loads": 0, "natural_source_reads": 0, "samples": []}
    parent_status = Path("/proc/self/status").read_text()
    r["parent"] = {"inner_pid": os.getpid(), "outer_pid": int(os.readlink("/proc/self")),
                   "pid_namespace": os.readlink("/proc/self/ns/pid"),
                   "direct_self_RSS_bytes": strict_status_rss(parent_status),
                   "legacy_inner_pid_RSS_bytes": old_rss(os.getpid())}
    p = subprocess.Popen([sys.executable, "-u", "-c", CHILD], stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, start_new_session=True, preexec_fn=limits)
    r["popen_inner_pid"] = p.pid
    try:
        anchor = helper.bind_group(p.pid)
        r["group_anchor"] = anchor
        assert anchor["telemetry_ok"], anchor
        with selectors.DefaultSelector() as select:
            select.register(p.stdout, selectors.EVENT_READ)
            if not select.select(10):
                raise TimeoutError("authored_child_ready_timeout")
        line = p.stdout.readline()
        child = json.loads(line)
        r["child"] = child
        assert child["inner_pid"] == child["inner_pgid"] == p.pid
        assert child["NSpid"][-1] == child["NSpgid"][-1] == p.pid
        assert child["NSpid"][0] == child["outer_pid"]
        assert child["pid_namespace"] == r["parent"]["pid_namespace"]
        for _ in range(3):
            assert p.poll() is None
            direct = strict_status_rss(Path(f'/proc/{child["outer_pid"]}/status').read_text())
            snapshot = scanner(p.pid, anchor=anchor)
            r["samples"].append({"direct_outer_pid_RSS_bytes": direct,
                                 "legacy_inner_pid_RSS_bytes": old_rss(p.pid),
                                 "namespace_aware_snapshot": snapshot})
            assert direct >= ALLOCATION_BYTES
            assert snapshot["telemetry_ok"] and snapshot["group_members_observed"]
            assert snapshot["ownership_anchor"]["namespace_identity"] == child["pid_namespace"]
            members = snapshot["members"]
            leader = [m for m in members if m["outer_pid"] == child["outer_pid"]]
            assert len(leader) == 1 and leader[0]["inner_pid"] == p.pid
            assert all(m["inner_pgid"] == p.pid for m in members)
            assert snapshot["rss_bytes"] >= ALLOCATION_BYTES
            assert abs(leader[0]["rss_bytes"] - direct) <= 2**20
            time.sleep(0.1)
        r["status"] = "passed"
        r["namespace_mismatch_reproduced"] = child["outer_pid"] != p.pid
        r["legacy_read_differed_from_direct_in_all_samples"] = all(
            x["legacy_inner_pid_RSS_bytes"] != x["direct_outer_pid_RSS_bytes"] for x in r["samples"])
    except BaseException as error:
        r["status"] = "failed"
        r["error_type"] = type(error).__name__
        r["failure_code"] = str(error) if isinstance(error, (ValueError, TimeoutError)) else "probe_assertion_or_runtime_failure"
        raise
    finally:
        if p.poll() is None:
            # p.pid is the child's PGID in OUR namespace. Scanned outer/proc
            # PIDs are never signal targets.
            r["termination_signal_target_inner_pgid"] = p.pid
            os.killpg(p.pid, signal.SIGTERM)
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(timeout=5)
        r["child_returncode"] = p.returncode
        r["finished_unix_seconds"] = time.time()
        r["helper_unchanged"] = sha(helper_path) == r["helper_sha256"]
        if not r["helper_unchanged"]:
            r["status"] = "inconclusive_helper_changed_during_probe"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(r, indent=2) + "\n")
    return r


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--helper", type=Path, required=True)
    p.add_argument("--function", required=True)
    p.add_argument("--output", type=Path, required=True)
    a = p.parse_args()
    result = run(a.helper, a.function, a.output)
    print(json.dumps({"status": result["status"], "samples": len(result["samples"])}))


if __name__ == "__main__":
    main()
