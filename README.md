# WikiGraphRAG

Research repository for **Reading the Latest, Not the Loudest: Lifecycle-Aware Structured Memory for Retrieval-Augmented Generation** and its ongoing ACL 2027 rework.

## Project materials

| Material | Location |
|---|---|
| Original submitted paper | [paper.pdf](original_submission/paper.pdf) |
| Original supplementary material | [supplement.pdf](original_submission/supplement.pdf) |
| Original reproducibility checklist | [reproducibility_checklist.pdf](original_submission/reproducibility_checklist.pdf) |
| Original source, datasets, cached generations and results | [code-and-data](original_submission/code-and-data/) |
| Original code archive and audit | [reference](acl2027_temporal_state/reference/) |
| Current ACL 2027 research source and tests | [acl2027_temporal_state](acl2027_temporal_state/) |
| Working LaTeX paper, bibliography and claim ledgers | [paper](acl2027_temporal_state/paper/) |
| Research protocols, supplements and progress reports | [docs](acl2027_temporal_state/docs/) |
| Experiment data and provenance | [data](acl2027_temporal_state/data/) |
| Saved results and verification records | [results](acl2027_temporal_state/results/) |
| Planning documents | [planning](planning/) |

The original package is retained unchanged as a historical submission. Its README and result claims describe that submission; later audits and limitations are recorded in the rework. The original paper and supplement were supplied as PDFs; their editable TeX sources were not available in this workspace. The rework's available LaTeX sources are included.

## Current checkpoint: v0.16 — paused

Saved at the user's request on 4 October 2026. Start the next chat with [NEXT_CHAT_HANDOFF_v16.txt](acl2027_temporal_state/docs/NEXT_CHAT_HANDOFF_v16.txt) and [PROJECT_STATE.json](acl2027_temporal_state/PROJECT_STATE.json).

This increment adds complete structural views for **12 filings / 23,651 numeric occurrences**, an amended **21-of-24-table candidate pool covering all 12 files**, the acquired-evidence binding interface and native reader driver, taxonomy recovery, and a six-page **draft** preparation addendum. Original failures and amendments remain separate.

**No new natural QA or retrieval result is claimed.** The new reader's authored attempt stopped before model loading; all eight requests remain unattempted. Full-DTS attempts retain 12 prelaunch refusals and a later 12-worker DOM failure. The repaired v16.3 runner and natural source rendering are unexecuted. A namespace-aware memory helper passed a real allocation control; final audit closure and protocol binding remain pending. The full v16 test suite and final paper visual review were deferred when the user paused the session.

Public source, protocols, tests, results and draft paper are saved here. Exact source-bearing run artifacts are preserved separately in the private recovery archive described in the handoff; model weights are reproducible from their pinned acquisition record. No experiments continue in the background.

## Previous completed checkpoint: v0.15

Current checkpoint **v0.15** adds a bounded typed reader for numeric facts and their reported entity, period, unit, dimensions and physical source version. After a separately frozen ASCII-support amendment, all **12 filing objects and 23,651 fact occurrences** are processed. **23,519 common supported numeric values agree exactly** with the pinned Arelle transformation adapter; **16 nil and 116 unsupported facts** remain separate. Ten typed-dimension bindings remain unresolved, leaving **23,509 facts** that pass both local numeric and reported-binding layers.

The original run rejected six ASCII-declared files and left 14,778 reader occurrences missing. Its code, protocol and full-denominator result are preserved beside the amendment. **610 unit tests**, a replay of 68 earlier controls against the amended reader, **96 source-derived lookup checks**, and independent record/byte audits are documented. These are engineering and source-only checks, with **zero new natural QA predictions**; they do not establish full taxonomy conformance or a stronger natural-language reader.

Start with the [typed-reader addendum](acl2027_temporal_state/paper/typed_reader_study_v15.pdf), [research update](acl2027_temporal_state/docs/research_update_v15.txt), and [project state](acl2027_temporal_state/PROJECT_STATE.json). The [next algorithmic proposal](acl2027_temporal_state/docs/algorithmic_next_stage_v16_proposal.txt) tests retrieval of evidence that distinguishes competing year/unit/entity/version bindings against strong structured and generic sufficiency baselines. Automatic proposal, verification and natural benefit remain unestablished.

The [v0.14 reader and retrieval results](acl2027_temporal_state/paper/retrieval_reader_upgrade_v14.pdf) remain negative development findings. The earlier nondirect-propagation route stays stopped. Current PDFs are working research addenda, not a submission-ready ACL manuscript.

## Reproduce and verify

From the repository root:

```bash
python3 scripts/verify_repository.py
cd acl2027_temporal_state
mkdir -p ../../tmp
PYTHONDONTWRITEBYTECODE=1 TMPDIR=../../tmp PYTHONPATH=EXTERNAL_ARELLE_RUNTIME:src python3 -m unittest discover -s tests -q
python3 scripts/verify_structured_study_v14.py  # Retained v14 metadata audit
```

The checkpoint's [reproduction guide](acl2027_temporal_state/docs/reproduce_v15.txt) distinguishes portable checks from checks requiring exact external working inputs. For the original implementation, use its [original README](original_submission/code-and-data/README.md); dependency installation and original experiment reruns are separate from the rework's unit tests.

Model weights, runtime binaries, temporary caches, unbundled third-party full-text captures and private review correspondence are outside this repository. Deliberately packaged replay caches and permitted SEC filing inputs are retained. Some historical provenance records contain the original execution paths; this import does not rewrite frozen evidence to pretend a different execution environment.

The original code retains its supplied [MIT license](original_submission/code-and-data/LICENSE) and source-data notices. This import does not assign a new blanket license to all research materials.

## Project update workflow

This repository is the designated destination for future source, paper, supplement, protocol, data, result and reproducibility updates made during this project. Completed updates should be committed and pushed here as part of the active work session, preserving history and recording actual verification. See [AGENTS.md](AGENTS.md).
