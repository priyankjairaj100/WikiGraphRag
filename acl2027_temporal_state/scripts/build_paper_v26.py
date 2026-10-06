#!/usr/bin/env python3
"""Build the v26 scaffold with verified ACL files and record format checks.

Example, from the research directory:
  python3 scripts/build_paper_v26.py \\
    --style-cache /tmp/acl-style-v26 \\
    --output-dir /tmp/interpretation-v26-build

The output directory must be new and outside the repository.
No submission action occurs. A successful build does not establish research readiness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture(command: list[str], *, cwd: Path, env: dict[str, str]) -> str:
    result = subprocess.run(command, cwd=cwd, env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"Command failed ({result.returncode}): {command[0]}\n{result.stdout[-6000:]}")
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--style-cache", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--style-source", type=Path,
                        help="Optional existing official style clone; hashes remain mandatory.")
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[1]
    repository = project.parent
    output = args.output_dir.expanduser().resolve()
    cache = args.style_cache.expanduser().resolve()
    for name, path in (("output", output), ("style cache", cache)):
        if path == repository or repository in path.parents:
            parser.error(f"Keep the {name} outside the repository.")
    if output.exists():
        parser.error("Use a new output directory; previous build artifacts are preserved.")
    required = ["latexmk", "pdflatex", "bibtex", "pdfinfo", "pdffonts", "pdftotext"]
    missing = [name for name in required if not shutil.which(name)]
    if missing:
        parser.error("Missing build tools: " + ", ".join(missing))
    fetch = [sys.executable, str(project / "scripts/fetch_acl_style_v26.py"),
             "--cache-dir", str(cache)]
    if args.style_source:
        fetch.extend(["--source-dir", str(args.style_source.expanduser().resolve())])
    env = os.environ.copy()
    dependency_receipt = json.loads(capture(fetch, cwd=project, env=env))
    output.mkdir(parents=True)
    source_names = ["interpretation_v26.tex", "interpretation_v26.bib"]
    for name in source_names:
        shutil.copyfile(project / "paper" / name, output / name)
    # Fix PDF clock values for repeatable builds under the same installed TeX toolchain.
    env["SOURCE_DATE_EPOCH"] = "1791244800"
    env["FORCE_SOURCE_DATE"] = "1"
    env["TZ"] = "UTC"
    env["TEXINPUTS"] = str(cache) + os.pathsep
    env["BSTINPUTS"] = str(cache) + os.pathsep
    command = ["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error",
               "-file-line-error", "-pdflatex=pdflatex -no-shell-escape %O %S",
               "interpretation_v26.tex"]
    try:
        build_log = capture(command, cwd=output, env=env)
    except RuntimeError as error:
        (output / "build_failure.txt").write_text(str(error), encoding="utf-8")
        raise
    (output / "build_stdout.txt").write_text(build_log, encoding="utf-8")
    stem = output / "interpretation_v26"
    info = capture(["pdfinfo", str(stem.with_suffix(".pdf"))], cwd=output, env=env)
    fonts = capture(["pdffonts", str(stem.with_suffix(".pdf"))], cwd=output, env=env)
    capture(["pdftotext", "-layout", str(stem.with_suffix(".pdf")),
             str(stem.with_suffix(".txt"))], cwd=output, env=env)
    (output / "pdfinfo.txt").write_text(info, encoding="utf-8")
    (output / "pdffonts.txt").write_text(fonts, encoding="utf-8")
    auxiliary = stem.with_suffix(".aux").read_text(encoding="utf-8")
    marker = re.search(r"\\newlabel\{v26-main-end\}\{\{[^{}]*\}\{(\d+)\}", auxiliary)
    if not marker:
        raise RuntimeError("Missing main-content end marker; cannot verify page limit.")
    last_content_page = int(marker.group(1))
    font_rows = [re.search(r"\s+(yes|no)\s+(yes|no)\s+(yes|no)\s+\d+\s+\d+\s*$", line)
                 for line in fonts.splitlines()[2:] if line.strip()]
    fonts_embedded = bool(font_rows) and all(row and row.group(1) == "yes" for row in font_rows)
    page_size = re.search(r"Page size:\s+([0-9.]+) x ([0-9.]+) pts", info)
    a4 = bool(page_size and abs(float(page_size.group(1)) - 595.276) < 1
              and abs(float(page_size.group(2)) - 841.89) < 1)
    tex = (output / "interpretation_v26.tex").read_text(encoding="utf-8")
    latex_log = stem.with_suffix(".log").read_text(encoding="utf-8", errors="replace")
    unresolved_references = bool(re.search(r"(?:Citation|Reference) .* undefined|There were undefined", latex_log))
    overfull = re.findall(r"Overfull \\[hv]box[^\n]*", latex_log)
    checks = {
        "official_pinned_style_hashes_verified": True,
        "review_style_requested": r"\usepackage[review]{acl}" in tex,
        "last_main_content_page": last_content_page,
        "short_paper_content_limit": 4,
        "within_content_limit": last_content_page <= 4,
        "a4": a4,
        "all_fonts_embedded": fonts_embedded,
        "undefined_citations_or_references": unresolved_references,
        "overfull_box_warnings": overfull,
        "pending_results_markers": len(re.findall(r"\\pending\{", tex)),
    }
    receipt = {
        "artifact_role": "development scaffold; not a submission package",
        "submission_ready": False,
        "research_claims_validated_by_build": False,
        "style_dependency": dependency_receipt,
        "source_sha256": {name: sha256(output / name) for name in source_names},
        "pdf_sha256": sha256(stem.with_suffix(".pdf")),
        "source_date_epoch": env["SOURCE_DATE_EPOCH"],
        "command": command,
        "checks": checks,
        "tool_versions": {name: capture([name, "--version"], cwd=output, env=env).splitlines()[:2]
                          for name in ("pdflatex", "bibtex")},
    }
    (output / "build_receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output_dir": str(output), "pdf": str(stem.with_suffix('.pdf')),
                      "checks": checks, "submission_ready": False}, indent=2))
    if not (checks["review_style_requested"] and checks["within_content_limit"]
            and a4 and fonts_embedded and not unresolved_references and not overfull):
        raise SystemExit("Build completed, but a required format check failed. Inspect the receipt.")


if __name__ == "__main__":
    main()
