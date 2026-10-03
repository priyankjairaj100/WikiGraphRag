"""Validate the code/data supplement from a clean extraction."""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "wikigraphrag_code_and_data.zip"
PREFIX = "wikigraphrag-code-and-data/"
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".jsonl", ".toml", ".yaml", ".yml", ".ini", ".cfg"}
FORBIDDEN_PREFIXES = (
    "data/longbench/",
    "data/cache/embeddings/",
    "data/wikidata/cache/",
    "data/multihop_real/cache/",
    "data/realprose/cache/",
    "data/realwiki/cache/",
)
REPOSITORY_HOSTS = {
    "github.com",
    "www.github.com",
    "gitlab.com",
    "www.gitlab.com",
    "bitbucket.org",
    "osf.io",
    "zenodo.org",
    "www.zenodo.org",
    "huggingface.co",
    "anonymous.4open.science",
    "codeberg.org",
}
_HOME_NAME = Path.home().name
_IDENTITY_TOKENS = {_HOME_NAME, _HOME_NAME.split("-", 1)[-1]}
IDENTITY = re.compile("|".join(re.escape(token) for token in _IDENTITY_TOKENS if token), re.IGNORECASE)
ABSOLUTE_PATH = re.compile(
    r"(?:[A-Za-z]:[\\/](?:Users|Documents|repos?|workspaces?)[\\/]|/(?:Users|home)/[^\s\"']+)",
    re.IGNORECASE,
)
URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
SECRETS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8", errors="replace")


def _run(command: list[str], cwd: Path, marker: str) -> None:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, check=False)
    if completed.returncode != 0 or marker not in completed.stdout:
        raise RuntimeError(f"failed: {command}\n{completed.stdout}\n{completed.stderr}")


def validate() -> None:
    with zipfile.ZipFile(ARCHIVE) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("CRC failure")
        names = [info.filename for info in archive.infolist()]
        if len(names) != len(set(names)) or not all(name.startswith(PREFIX) for name in names):
            raise RuntimeError("invalid archive path structure")
        relative_names = {name.removeprefix(PREFIX) for name in names}
        if any(name.startswith(FORBIDDEN_PREFIXES) for name in relative_names):
            raise RuntimeError("raw or embedding cache was bundled")
        if any("__pycache__" in PurePosixPath(name).parts or name.endswith((".pyc", ".pyo")) for name in relative_names):
            raise RuntimeError("Python build artifacts were bundled")

        for info in archive.infolist():
            relative = info.filename.removeprefix(PREFIX)
            if Path(relative).suffix.lower() not in TEXT_SUFFIXES and relative != "requirements.txt":
                continue
            text = _decode(archive.read(info))
            if IDENTITY.search(text) or ABSOLUTE_PATH.search(text) or any(pattern.search(text) for pattern in SECRETS):
                raise RuntimeError(f"anonymous-content scan failed: {relative}")
            for url in URL.findall(text):
                if urllib.parse.urlsplit(url.rstrip(".,);]}" )).hostname in REPOSITORY_HOSTS:
                    raise RuntimeError(f"repository link found: {relative}: {url}")

        with tempfile.TemporaryDirectory(prefix="wikigraphrag-review-") as temporary:
            archive.extractall(temporary)
            package_root = Path(temporary) / PREFIX.rstrip("/")
            _run([sys.executable, "-m", "experiments.run_tempralm_audit"], package_root, '"wikidata"')
            _run([sys.executable, "-m", "experiments.build_bundle", "--verify"], package_root, "ALL VERIFIED")
            _run([sys.executable, "tests/test_detector.py"], package_root, "10/10 passed")
            _run([sys.executable, "scripts/make_paper_tables.py"], package_root, "wrote table6_money.tex")
            for name in (
                "table1_detection.tex",
                "table2_llm_baseline.tex",
                "table3_ablation.tex",
                "table4_retrieval.tex",
                "table6_money.tex",
            ):
                generated = package_root / "overleaf_src" / "tables" / name
                canonical = ROOT / "overleaf_src" / "tables" / name
                if generated.read_bytes() != canonical.read_bytes():
                    raise RuntimeError(f"generated table differs: {name}")

    print(f"PASS: {len(names)} files, {ARCHIVE.stat().st_size} bytes, CRC/anonymity/links/replay/tests/tables verified")


if __name__ == "__main__":
    validate()