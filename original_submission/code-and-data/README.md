# WikiGraphRAG (re-implementation)

Lifecycle-aware structured memory for retrieval-augmented generation.

Code and data to reproduce every table and figure in the paper *"Reading the Latest,
Not the Loudest: Lifecycle-Aware Structured Memory for Retrieval-Augmented Generation."*

## Core idea

A zero-LLM, text-driven **supersession detector** adds a typed edge `c' -> c` whenever a
newer claim changes the *value* of the same `(subject, relation)` key. A **lifecycle-aware
retriever** then filters predicted-superseded claims before top-k selection so the reader sees
the *current* value instead of the loudest (most relevant) one.

## Layout

```
wikigraphrag/
  core/      claim + graph data structures
  parse/     sentence spans, FRAME / VALUES / PHRASES extraction, LLM normalization
  detect/    the three signals + Algorithm 1 (supersession detection)
  retrieve/  embeddings, exact cosine scoring, BM25, hybrid, lifecycle filtering
  graphrag/  typed-PPR, faithful HippoRAG / GraphRAG / RAPTOR baselines
  readers/   local Qwen2.5-3B reader + replayable cache, EM scoring
  eval/      detection / retrieval (SER, AEP) / QA metrics + bootstrap statistics
  data/      dataset builders (FreshRAG, Wikidata, TempLAMA, prose, Wikipedia, multi-hop)
experiments/ scripts that regenerate every table and figure
tests/       Proposition 1 / Lemma 1 checks, detector goldens, signal ablations
data/        released benchmarks (claims.jsonl per dataset) + cached LLM generations
results/     one JSON result card per experiment + SHA-256 MANIFEST.json
```

## Setup

Python 3.11.

```
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Readers run **fully local** (Qwen2.5-3B-Instruct, fp16) on a single NVIDIA GPU. Every model
call is cached to `data/cache/`, so all reported numbers **replay with no GPU and no API key**.

Reference runs used Windows 11 Enterprise (build 10.0.26200), Python 3.11.15, an Intel
Core i7-13800H with 63.8 GB RAM, and an NVIDIA RTX 2000 Ada laptop GPU with 8 GB VRAM.
`requirements.txt` pins the complete software environment; the paper supplement records the
key package versions, random seeds, run counts, and final hyperparameters.

## Reproduce

Every headline number maps to a result card in `results/`, and the mapping is checked by
SHA-256:

```
python -m experiments.build_bundle --verify     # re-hashes data + results -> "ALL VERIFIED"
python scripts/build_submission_archive.py      # rebuilds the lean anonymous reviewer ZIP
python scripts/validate_submission_archive.py   # clean-extraction integrity/replay audit
```

Regenerate individual results (each writes its `results/*.json`):

```
python -m experiments.run_detection      # detection F1            (Table 1)
python -m experiments.run_judge          # zero-LLM vs LLM judge   (Table 2)
python -m experiments.run_ablation       # signal ablation         (Table 3)
python -m experiments.run_retrieval      # SER / AEP               (Table 4)
python -m experiments.run_qa             # current-answer EM       (Table 6)
python -m experiments.run_asof           # as-of (value-at-time-T)
python -m experiments.run_temporal_rag_baselines --reader extractive  # TempRALM/RAG-Time
python -m experiments.run_ragtime_tuning # held-out alpha/half-life selection
python -m experiments.run_history_sweep  # history-length sweep
python -m experiments.run_multihop       # interval composition across hops
```

## Data

Each benchmark ships as `data/<name>/claims.jsonl` (the released, SHA-256-hashed data behind
every result) plus `meta.json`; the Qwen generation cache in `data/cache/` lets the QA,
judge, and normalization experiments replay offline. The public LongBench release used only
for the static-corpus check (QuALITY) is not bundled here to keep the archive small; download
it separately to re-run that single check.

## License

MIT (see `LICENSE`). Redistributed datasets derive from public sources (Wikidata CC0, the
published TempLAMA benchmark, and public Wikipedia text).
