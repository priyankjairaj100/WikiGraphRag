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

## Current research status

Current checkpoint **v0.14** completes a structural retrieval and reader comparison: **40 native completions**, comprising eight authored controls and 32 answers over eight development questions from two histories. Joint content, scope and citation correctness is **1/8, 0/8, 1/8 and 1/8** for ordinary, paired, parent and closure contexts. Qwen3.5-4B Q5_K_M is a failed reader upgrade candidate in the matched-context comparison; two output-limit failures are retained.

The corpus covers 550 PDF pages and 2,306 chunks. A separate **48-context source-coverage diagnostic** finds no complete-evidence improvement from the pinned compact dense encoder or RRF: each covers 1/8 questions, versus 2/8 for paired BM25 and 3/8 with structural closure. No new QA calls are attributed to this diagnostic. **484 unit tests, 1,348 main-study checks and 983 dense-execution checks pass.** Mechanical checks do not establish semantic correctness.

Start with the [working paper PDF](acl2027_temporal_state/paper/retrieval_reader_upgrade_v14.pdf), [v0.14 research update](acl2027_temporal_state/docs/research_update_v14.txt), and [current project state](acl2027_temporal_state/PROJECT_STATE.json). Editable paper source, bibliography and a claim ledger are included. This is a development draft, not a submission-ready paper.

The next gate is a typed source-binding reader that verifies the year, unit, entity and version attached to each value, followed by retrieval of witnesses that distinguish answer-changing alternatives. A selector scaffold passes 14 authored tests; automatic proposal, verification and natural benefit remain unestablished. Twelve fresh filing objects and their Inline XBRL links have been acquired and audited for source feasibility, without creating an admitted benchmark. The earlier nondirect-propagation route remains stopped.

## Reproduce and verify

From the repository root:

```bash
python3 scripts/verify_repository.py
cd acl2027_temporal_state
PYTHONPATH=src python3 -m unittest discover -s tests -q
python3 scripts/verify_structured_study_v14.py
```

The checkpoint's [reproduction guide](acl2027_temporal_state/docs/reproduce_v14.txt) distinguishes portable checks from checks requiring exact external working inputs. For the original implementation, use its [original README](original_submission/code-and-data/README.md); dependency installation and original experiment reruns are separate from the rework's unit tests.

Model weights, runtime binaries, temporary caches, unbundled third-party full-text captures and private review correspondence are outside this repository. Deliberately packaged replay caches and permitted SEC filing inputs are retained. Some historical provenance records contain the original execution paths; this import does not rewrite frozen evidence to pretend a different execution environment.

The original code retains its supplied [MIT license](original_submission/code-and-data/LICENSE) and source-data notices. This import does not assign a new blanket license to all research materials.

## Project update workflow

This repository is the designated destination for future source, paper, supplement, protocol, data, result and reproducibility updates made during this project. Completed updates should be committed and pushed here as part of the active work session, preserving history and recording actual verification. See [AGENTS.md](AGENTS.md).
