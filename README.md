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

Current checkpoint **v0.12** completes the eight-URL source-access pilot. Six complete representations were recovered across two version histories. Plug Power supplies one direct-correction development control; OPERA changes the analysis scope and directly revises its conclusions. Both JAMA URLs returned HTTP 403. No certified nondirect propagation case or new natural QA comparison emerged, so further expansion of the current propagation contribution is stopped.

The next proposed experiment is paired-version, scope-aware reading with matched retrieval and a competent reader. This is a feasibility study, not a demonstrated algorithmic advance. All **416 unit tests pass**. The initial v0.11 import and original submission remain preserved.

Start with the [v0.12 research update](acl2027_temporal_state/docs/research_update_v12.txt), [current project state](acl2027_temporal_state/PROJECT_STATE.json), and [research decision](acl2027_temporal_state/docs/route_decision_v12.txt).

## Reproduce and verify

From the repository root:

```bash
python3 scripts/verify_repository.py
cd acl2027_temporal_state
PYTHONPATH=src python3 -m unittest discover -s tests -q
python3 scripts/verify_access_pilot_v12.py
```

The checkpoint's [reproduction guide](acl2027_temporal_state/docs/reproduce_v12.txt) distinguishes portable checks from checks requiring exact external working inputs. For the original implementation, use its [original README](original_submission/code-and-data/README.md); dependency installation and original experiment reruns are separate from the rework's unit tests.

Model weights, runtime binaries, temporary caches, unbundled third-party full-text captures and private review correspondence are outside this repository. Deliberately packaged replay caches and permitted SEC filing inputs are retained. Some historical provenance records contain the original execution paths; this import does not rewrite frozen evidence to pretend a different execution environment.

The original code retains its supplied [MIT license](original_submission/code-and-data/LICENSE) and source-data notices. This import does not assign a new blanket license to all research materials.

## Project update workflow

This repository is the designated destination for future source, paper, supplement, protocol, data, result and reproducibility updates made during this project. Completed updates should be committed and pushed here as part of the active work session, preserving history and recording actual verification. See [AGENTS.md](AGENTS.md).
