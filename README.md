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

## Current checkpoint: v0.21 — certificate theory and local empirical handoff

The [theory manuscript](acl2027_temporal_state/paper/theory_v21.pdf) now contains full proofs for complete scoped answer certificates, conditional soundness/completeness, selective-risk bounds, exact finite selection, counterexamples and a restricted classical cover guarantee. [Editable TeX](acl2027_temporal_state/paper/theory_v21.tex), [prior-art audit](acl2027_temporal_state/docs/theory_prior_art_audit_v21.txt) and [claim ledger](acl2027_temporal_state/paper/claim_ledger_v21.json) distinguish the domain-specific integration from established theory.

The new supplied-map reference solver passes **36 targeted tests and 9 authored controls**. A separate model-role adversarial review is recorded. **No new model or natural-QA experiment ran.** Semantic assumptions, methodological novelty and empirical improvement remain unestablished.

The empirical program has started from that handoff. Its win condition, track order, and stop rules are in the [v22 plan](acl2027_temporal_state/docs/empirical_program_v22.txt). The [non-anticipating controller](acl2027_temporal_state/src/temporal_state/certificate_controller_v22.py) is the shared acquisition harness. The [typed-fact comparison](acl2027_temporal_state/src/temporal_state/typed_binding_v22.py) and its [development census](acl2027_temporal_state/results/typed_binding_census_v22.json) are structural checks on the twelve previously inspected filings, not a natural-QA result. Semantic verification, coverage estimation, and the confirmatory runner are still required.

Start with the [local contract](acl2027_temporal_state/docs/local_empirical_contract_v21.txt), [implementation map](acl2027_temporal_state/docs/theory_implementation_map_v21.txt), [reproduction commands](acl2027_temporal_state/docs/reproduce_theory_v21.txt) and [handoff](acl2027_temporal_state/docs/NEXT_CHAT_HANDOFF_v21.txt).

## Previous checkpoint: v0.20 — partial source reconciliation and prepared reader correction

All **326 entries** now have exact source/record joins and preserved registry overlays. A fixed nine-entry pilot received separate model-role review: **27 supported and 27 unresolved field correspondences**. All nine entity and population bindings remain unresolved; no record has all six fields resolved. The remaining 317 entries have 1,902 pending fields. No evidence-pack admission or author release is claimed.

The new consumer resolves 1,057 witness records and retains **68 unresolved selector variants**, a compatibility limit to fix separately. A per-completion capture component passes 22 invented controls; real provider durability and native integration remain untested. **16 matched development inputs** are prepared for a policy clarification comparison, with neutral IDs and balanced order. No new model run has occurred. **1,477 unit tests pass.**

Read the [v20 update](acl2027_temporal_state/docs/research_update_v20.txt), [handoff](acl2027_temporal_state/docs/NEXT_CHAT_HANDOFF_v20.txt), [reproduction guide](acl2027_temporal_state/docs/reproduce_v20.txt) and [application receipt](acl2027_temporal_state/results/question_free_registry_application_v20.json). The reader remains at the receipt-backed v19.1 result of 2/8; the original raw-native availability gap below remains unresolved. No QA gain, retrieval novelty or ACL readiness is claimed.

## Previous checkpoint: v0.19 — authored reader failure and source annotations

The 9B reader completed all **eight fictional controls**, after a separately reviewed transport amendment, but answered only **2/8 correctly** with no output-limit failures. The authored readiness gate failed. The first interrupted attempt remains preserved in separate result receipts.

All **326 source occurrences across 21 bundles** now have initial source-only annotations: 47 reviewed and 279 reviewed with unknowns. The projection and annotation checks preserve exact occurrence coverage, 1,125 witnesses and 160 page links. Registry reconciliation, substantive semantic admission and author release remain pending. The offline joint scorer has passed authored and independent controls. **1,410 unit tests pass**; no natural QA has run.

**Evidence availability:** original v19/v19.1 raw native outputs and traces disappeared after grading. The cause is unknown. Public result receipts, code and source annotations survive; independent v19.1 terminal verification and raw reanalysis remain unavailable. Surviving private source artifacts and prior recovery archives are saved separately.

Read the [v19 handoff](acl2027_temporal_state/docs/NEXT_CHAT_HANDOFF_v19.txt), [working addendum](acl2027_temporal_state/paper/reader_readiness_update_v19.pdf), [reproduction limits](acl2027_temporal_state/docs/reproduce_v19.txt) and [availability incident](acl2027_temporal_state/results/workspace_artifact_loss_v19.json). No stronger-reader, retrieval, held-out or ACL-readiness claim follows from this checkpoint.

## Previous checkpoint: v0.18 — source identity and authored tokenizer readiness

All **40 additional dependency views** are now available across **39 original successes and one separately recorded caption recovery**. The prior101 table/neighbor views remain available. Separate recovery completes34 identity candidate views; **12 literal cover identity cards** are admitted from38 witnesses. These cards do not establish table-level entity or period scope.

All **21 bundles pass the amended interface shape checks**, preserving exact source blocks,19 blank exposures and every original registry. Replaying the old interface still gives10 passes and11 failures. Complete semantic evidence packs and author release remain pending.

The pinned **9B model loaded and tokenized all eight fictional requests**, after a separately reviewed logging-only amendment exposed the required zero rollback state. The original observability failure is preserved. **No new generation or natural QA has run.** Reader adequacy and retrieval advantage remain unestablished. **1,278 tests pass.**

Read the [v18 handoff](acl2027_temporal_state/docs/NEXT_CHAT_HANDOFF_v18.txt), [project state](acl2027_temporal_state/PROJECT_STATE.json), [three-page addendum](acl2027_temporal_state/paper/reader_readiness_update_v18.pdf), [reproduction guide](acl2027_temporal_state/docs/reproduce_v18.txt) and [next completion gate](acl2027_temporal_state/docs/reference_free_completion_gate_next_v18.txt). Exact source-bearing recovery files are saved separately; original submission and frozen prior results remain unchanged.

## Previous checkpoint: v0.17 — source evidence and reader feasibility

The fixed source pool now has qualified inspection views for **101/101 selected blocks (21 tables and 80 neighbors)**, across the original run and two separately recorded recoveries. The original result was **90/101 admitted**. Identity views cover **33/34 candidates**, with one telemetry failure retained, plus four separate SLB literal-cover views.

Full-DTS validation produced **nine clean source attempts, one incomplete taxonomy closure and two telemetry failures**. All **21 candidate bundles** are saved, with **40 additional dependencies** still awaiting rendering and semantic review. Eleven bundles retain interface failures caused by blank source blocks. **1,181 tests pass.**

**No new natural QA gain is claimed.** The new reader reached partial loading, then its application memory watchdog stopped it before readiness; tokenizations and completions remain zero. Reader feasibility, complete evidence-pack admission and independently reviewed questions are the next gates.

Read the [v17 handoff](acl2027_temporal_state/docs/NEXT_CHAT_HANDOFF_v17.txt), [project state](acl2027_temporal_state/PROJECT_STATE.json), [working addendum](acl2027_temporal_state/paper/reader_preparation_study_v17.pdf) and [reproduction guide](acl2027_temporal_state/docs/reproduce_v17.txt). Original failures, previous checkpoints and the original submission remain preserved. Exact source-bearing artifacts are saved separately; full third-party captures and model weights are outside Git.

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
