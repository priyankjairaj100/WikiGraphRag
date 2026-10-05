"""Compile the v21 theory sources to a new output directory; no model calls."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper"
INPUTS = (
    "theory_v21.tex", "theory_core_v21.tex", "theory_algorithm_v21.tex",
    "theory_related_v21.tex", "theory_references_v21.bib",
)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    destination = args.output_dir.resolve()
    pdf = destination / "theory_v21.pdf"
    receipt = destination / "theory_render_manifest_v21.json"
    if pdf.exists() or receipt.exists():
        parser.error("refusing to overwrite an existing PDF or build receipt; use a new output directory")
    for name in ("latexmk", "pdflatex", "bibtex"):
        if not shutil.which(name):
            parser.error(f"required executable unavailable: {name}")
    for name in INPUTS:
        if not (PAPER / name).is_file():
            parser.error(f"missing source: {name}")
    env = dict(os.environ)
    # Stabilize TeX timestamps; a different TeX/font distribution may still differ.
    env["SOURCE_DATE_EPOCH"] = str(int(datetime(2026, 10, 5, tzinfo=timezone.utc).timestamp()))
    env["FORCE_SOURCE_DATE"] = "1"
    with tempfile.TemporaryDirectory(prefix="wikigraph_theory_v21_") as temporary:
        command = ["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error",
                   f"-outdir={temporary}", "theory_v21.tex"]
        run = subprocess.run(command, cwd=PAPER, env=env, text=True, capture_output=True)
        if run.returncode:
            raise SystemExit(run.stdout[-12000:] + run.stderr[-4000:])
        log = (Path(temporary) / "theory_v21.log").read_text(errors="replace")
        warnings = [line for line in log.splitlines()
                    if any(token in line for token in ("Warning", "Overfull", "Underfull"))]
        if any("undefined" in line.lower() for line in warnings):
            raise SystemExit("unresolved TeX references: " + "\n".join(warnings))
        destination.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(temporary) / "theory_v21.pdf", pdf)
    record = {
        "schema_version": 1,
        "artifact": "theory_v21.pdf",
        "source_type": "compiled_LaTeX_theory_working_manuscript",
        "acl_template_claim": False,
        "source_date_epoch": env["SOURCE_DATE_EPOCH"],
        "build_command": "latexmk -pdf -interaction=nonstopmode -halt-on-error theory_v21.tex (temporary output directory)",
        "pdflatex_version": subprocess.run(["pdflatex", "--version"], text=True,
                                            capture_output=True, check=True).stdout.splitlines()[0],
        "inputs": [{"path": f"paper/{name}", "sha256": digest(PAPER / name)} for name in INPUTS],
        "build_script_sha256": digest(Path(__file__)),
        "pdf_sha256": digest(pdf),
        "pdf_bytes": pdf.stat().st_size,
        "tex_warnings": warnings,
        "visual_review": "separate review required; compilation does not establish page quality",
        "model_calls": 0,
        "network_calls": 0,
        "bitwise_reproducibility": "timestamps pinned; identical TeX and font environment also required",
    }
    receipt.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({"pdf": str(pdf), "receipt": str(receipt), "warnings": warnings}, indent=2))


if __name__ == "__main__":
    main()
