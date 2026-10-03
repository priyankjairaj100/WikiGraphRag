"""Build the lean anonymous code/data supplement from an explicit include set."""

from __future__ import annotations

import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "wikigraphrag_code_and_data.zip"
PREFIX = "wikigraphrag-code-and-data"
MAX_BYTES = 50_000_000

REQUIRED = {
    "README.md",
    "LICENSE",
    "requirements.txt",
    "results/MANIFEST.json",
    "results/ragtime_heldout_tuning.json",
    "results/tempralm_topicality_audit.json",
    "results/temporal_rag_baselines_extractive.json",
    "results/temporal_rag_baselines_qwen.json",
    "data/cache/qwen_generations.jsonl",
    "data/cache/openai_generations.jsonl",
    "scripts/build_submission_archive.py",
}


def included_files() -> list[Path]:
    files: list[Path] = []
    for directory in ("wikigraphrag", "experiments", "scripts", "tests"):
        files.extend(
            path
            for path in (ROOT / directory).rglob("*")
            if path.is_file()
            and "__pycache__" not in path.parts
            and path.suffix not in {".pyc", ".pyo"}
        )
    files.extend(ROOT / name for name in ("README.md", "LICENSE", "requirements.txt"))
    files.extend(
        path
        for path in (ROOT / "data").glob("*/*")
        if path.is_file() and path.name in {"claims.jsonl", "meta.json", "queries.json"}
    )
    files.extend(
        ROOT / "data" / "cache" / name
        for name in ("openai_generations.jsonl", "qwen_generations.jsonl")
    )
    files.extend(path for path in (ROOT / "results").glob("*.json") if path.is_file())
    return sorted(set(files), key=lambda path: path.relative_to(ROOT).as_posix())


def build() -> Path:
    files = included_files()
    temporary = OUTPUT.with_suffix(".zip.tmp")
    with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = path.relative_to(ROOT).as_posix()
            info = zipfile.ZipInfo(f"{PREFIX}/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    temporary.replace(OUTPUT)

    with zipfile.ZipFile(OUTPUT) as archive:
        names = [info.filename for info in archive.infolist()]
        relative_names = {name.removeprefix(f"{PREFIX}/") for name in names}
        if archive.testzip() is not None:
            raise RuntimeError("archive CRC validation failed")
        if len(names) != len(set(names)):
            raise RuntimeError("archive contains duplicate paths")
        if not REQUIRED <= relative_names:
            raise RuntimeError(f"archive missing: {sorted(REQUIRED - relative_names)}")
        if any(PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts for name in names):
            raise RuntimeError("archive contains an unsafe path")
    if OUTPUT.stat().st_size >= MAX_BYTES:
        raise RuntimeError(f"archive exceeds {MAX_BYTES} bytes")
    print(f"Wrote {OUTPUT.name}: {len(files)} files, {OUTPUT.stat().st_size} bytes")
    return OUTPUT


if __name__ == "__main__":
    build()