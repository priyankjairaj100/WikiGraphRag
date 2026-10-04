"""Independent authored fake-/proc tests; no natural process/source inspection."""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("group_rss_under_audit", ROOT / "scripts/process_group_rss_v16.py")
rss = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rss)


def status(outer, inner, group, *, value="32768 kB", state="S (sleeping)"):
    parent = 91000 if outer == 92000 else 92000
    outer_group = 92000 if outer in (92000, 92001) else 90000
    text = (f"Name:\tauthored\nPid:\t{outer}\nPPid:\t{parent}\nState:\t{state}\n"
            f"NSpid:\t{outer} {inner}\nNSpgid:\t{outer_group} {group}\nNSsid:\t{outer_group} {group}\n")
    return text + (f"VmRSS:\t{value}\n" if value is not None else "")


def stat(outer, parent, group, session, *, starttime=12345):
    fields = ["S"] + ["0"] * 49
    for index, value in [(1, parent), (2, group), (3, session), (19, starttime)]:
        fields[index] = str(value)
    return str(outer) + " (authored worker) " + " ".join(fields) + "\n"


class OwnedRSSAuditTests(unittest.TestCase):
    def proc_fixture(self):
        temporary = tempfile.TemporaryDirectory(dir="/dev/shm")
        base = Path(temporary.name)
        (base / "self/ns").mkdir(parents=True)
        (base / "self/status").write_text(status(91000, os.getpid(), os.getpgrp(), value="8192 kB"))
        (base / "self/ns/pid").symlink_to("pid:[authored_same]")
        (base / "91000/task/91000").mkdir(parents=True)
        (base / "91000/task/91000/children").write_text("92000\n")
        for outer, inner in [(92000, 71), (92001, 72)]:
            (base / str(outer) / "ns").mkdir(parents=True)
            (base / str(outer) / "status").write_text(status(outer, inner, 71))
            (base / str(outer) / "stat").write_text(stat(outer, 91000 if outer == 92000 else 92000, 92000, 92000))
            (base / str(outer) / "ns/pid").symlink_to("pid:[authored_same]")
        anchor = rss.bind_group(71, proc_root=base)
        self.assertTrue(anchor["telemetry_ok"], anchor)
        return temporary, base, anchor

    def test_outer_pid_does_not_select_inner_group(self):
        temporary, base, anchor = self.proc_fixture()
        with temporary:
            result = rss.read_group_rss(71, anchor=anchor, proc_root=base)
            self.assertTrue(result["telemetry_ok"], result)
            self.assertEqual(result["rss_bytes"], 64 * 2**20)
            self.assertEqual({m["outer_pid"] for m in result["members"]}, {92000, 92001})
            wrong = rss.read_group_rss(92000, anchor=anchor, proc_root=base)
            self.assertFalse(wrong["telemetry_ok"])

    def test_different_namespace_with_same_inner_group_is_excluded(self):
        temporary, base, anchor = self.proc_fixture()
        with temporary:
            p = base / "92001/ns/pid"
            p.unlink()
            p.symlink_to("pid:[authored_other]")
            (base / "92001/stat").write_text(stat(92001, 93000, 93000, 93000))
            result = rss.read_group_rss(71, anchor=anchor, proc_root=base)
            self.assertTrue(result["telemetry_ok"], result)
            self.assertEqual(result["rss_bytes"], 32 * 2**20)

    def test_missing_live_rss_fails_closed(self):
        temporary, base, anchor = self.proc_fixture()
        with temporary:
            (base / "92000/status").write_text(status(92000, 71, 71, value=None))
            self.assertFalse(rss.read_group_rss(71, anchor=anchor, proc_root=base)["telemetry_ok"])

    def test_malformed_live_rss_fails_closed(self):
        for bad in ["NaN kB", "32 MB", "-1 kB", "0 kB", ""]:
            temporary, base, anchor = self.proc_fixture()
            with temporary, self.subTest(bad=bad):
                (base / "92000/status").write_text(status(92000, 71, 71, value=bad))
                self.assertFalse(rss.read_group_rss(71, anchor=anchor, proc_root=base)["telemetry_ok"])

    def test_unreadable_owned_member_cannot_yield_partial_success(self):
        temporary, base, anchor = self.proc_fixture()
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path == base / "92000/status":
                raise PermissionError("authored denied status")
            return original(path, *args, **kwargs)
        with temporary, patch.object(Path, "read_text", read):
            self.assertFalse(rss.read_group_rss(71, anchor=anchor, proc_root=base)["telemetry_ok"])

    def test_unreadable_namespace_with_malformed_group_is_not_excluded(self):
        temporary, base, anchor = self.proc_fixture()
        original = os.readlink
        def readlink(path, *args, **kwargs):
            if Path(path) == base / "92000/ns/pid":
                raise PermissionError("authored denied namespace")
            return original(path, *args, **kwargs)
        with temporary, patch.object(os, "readlink", readlink):
            (base / "92000/status").write_text(status(92000, 71, 71).replace("NSpgid:\t92000 71", "NSpgid:\tbad"))
            self.assertFalse(rss.read_group_rss(71, anchor=anchor, proc_root=base)["telemetry_ok"])

    def test_known_zombie_can_have_zero_rss(self):
        parsed = rss.parse_status(status(92000, 71, 71, value=None, state="Z (zombie)"))
        self.assertEqual(parsed["rss_bytes"], 0)

    def test_malformed_namespace_map_fails_closed(self):
        temporary, base, anchor = self.proc_fixture()
        with temporary:
            text = status(92000, 71, 71).replace("NSpgid:\t92000 71", "NSpgid:\tbad 71")
            (base / "92000/status").write_text(text)
            self.assertFalse(rss.read_group_rss(71, anchor=anchor, proc_root=base)["telemetry_ok"])

    def test_anchor_requires_actual_direct_child_parent(self):
        temporary, base, _ = self.proc_fixture()
        with temporary:
            text = status(92000, 71, 71).replace("PPid:\t91000", "PPid:\t99999")
            (base / "92000/status").write_text(text)
            self.assertFalse(rss.bind_group(71, proc_root=base)["telemetry_ok"])

    def test_missing_children_interface_uses_exact_outer_parent(self):
        temporary, base, _ = self.proc_fixture()
        with temporary:
            (base / "91000/task/91000/children").unlink()
            anchor = rss.bind_group(71, proc_root=base)
            self.assertTrue(anchor["telemetry_ok"], anchor)
            self.assertEqual(anchor["outer_pid"], 92000)
            self.assertEqual(anchor["direct_child_binding_method"],
                             "exact_outer_ppid_scan_children_interface_absent")

    def test_denied_children_interface_does_not_fall_back(self):
        temporary, base, _ = self.proc_fixture()
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path == base / "91000/task/91000/children":
                raise PermissionError("authored denied children")
            return original(path, *args, **kwargs)
        with temporary, patch.object(Path, "read_text", read):
            anchor = rss.bind_group(71, proc_root=base)
            self.assertFalse(anchor["telemetry_ok"])
            self.assertEqual(anchor["error_type"], "PermissionError")

    def test_outer_parent_scan_excludes_unrelated_inner_pid_collision(self):
        temporary, base, _ = self.proc_fixture()
        original = Path.read_text
        def read(path, *args, **kwargs):
            if path == base / "93000/status":
                raise AssertionError("unrelated status must not be read")
            return original(path, *args, **kwargs)
        with temporary, patch.object(Path, "read_text", read):
            (base / "91000/task/91000/children").unlink()
            (base / "93000/ns").mkdir(parents=True)
            (base / "93000/stat").write_text(stat(93000, 99999, 93000, 93000))
            (base / "93000/status").write_text(status(93000, 71, 71))
            (base / "93000/ns/pid").symlink_to("pid:[unrelated]")
            anchor = rss.bind_group(71, proc_root=base)
            self.assertTrue(anchor["telemetry_ok"], anchor)
            self.assertEqual(anchor["outer_pid"], 92000)

    def test_anchor_retains_descendants_after_leader_disappears(self):
        temporary, base, anchor = self.proc_fixture()
        with temporary:
            shutil.rmtree(base / "92000")
            result = rss.read_group_rss(71, anchor=anchor, proc_root=base)
            self.assertTrue(result["telemetry_ok"], result)
            self.assertEqual(result["rss_bytes"], 32 * 2**20)
            self.assertEqual(result["members"][0]["inner_pid"], 72)

    def test_leader_pid_reuse_fails_closed(self):
        temporary, base, anchor = self.proc_fixture()
        with temporary:
            (base / "92000/stat").write_text(stat(92000, 91000, 92000, 92000, starttime=12346))
            self.assertFalse(rss.read_group_rss(71, anchor=anchor, proc_root=base)["telemetry_ok"])


if __name__ == "__main__":
    unittest.main()
