Lyft correction record illustration, version 0.5

This directory contains one convenience-selected development example: the
reported FY2024 adjusted EBITDA margin expansion forecast was corrected from
approximately 500 to approximately 50 basis points year over year, with Gross
Bookings as denominator. The example concerns a forecast record, not realized
financial performance. It provides zero temporal QA predictions and zero gold
answers. It is not an extraction-accuracy or method-comparison benchmark.

Evidence and annotation path

Eight current public HTTP responses were captured. Receipts, observed hashes,
limited exact excerpts, and deterministic source derivations retain provenance.
Current SEC original/amended submission records and the explicit amendment
identify a correction relation. They do not establish exact historical first
public availability or prove current response bytes were unchanged historically.

A source-only conversational agent produced the annotation without a pinned
model revision. Full annotation input fingerprints, the annotation output hash,
source representation hashes and configuration hashes are retained. The
annotation is model-assisted, not independently human-adjudicated. Exact quote
validation establishes source occurrence rather than semantic entailment.

The annotated sources are projected into two compact bundles of exact excerpts
with explicitly synthetic separators. Replay configuration records a span map
back to parent source offsets and hashes. This projection is declared: surrounding
document context is not silently represented as present in a compact excerpt.
Both bundles and the complete observed annotation context are declared in every
variant, including the certificate-omitted control. There is no claim that an
annotation made after seeing the amendment came from a blind original prefix.

The two caches have an identical underlying scored candidate graph and differ
only in correction-policy certification. Scores are authored admission
preferences, not learned contextual scores or probabilities. The certificate-
enabled variant withdraws the old record; the omitted variant keeps both. Four
decoder methods run each variant, yielding eight record decodes and forty
representation checks. These checks preserve forecast modality, unknown event
endpoints, the corrected record and identical base cache content. All pass.

The operational information cutoff is the retrieval day, 2026-10-02. No fiscal
calendar boundary, exact daily observation, historical intraday order or event
transition on the correction date is invented. An explicit record-reference
direction is distinct from a verified historical knowledge-time cutoff.

Replay

From the project root:
  python scripts/run_correction_source_demo.py

The illustration replays offline from the packaged evidence excerpts and
annotation. Results are written to results/correction_source_demo_v05.json.
This does not rerun a pinned extraction model. The four source-demo replay JSON
outputs were reproduced byte-for-byte during verification.

Full external release text and response bodies are analysis-only working data
and are excluded from the distributed checkpoint. The included capture and
derivation scripts can retrieve current public responses separately; retrieved
bytes may change and require new hashes. See source_manifest.json for capture
receipts, exclusions and historical-availability gaps.

No pinned extractor or scorer has executed. No QA score, natural method gain,
historical retrieval result or algorithmic novelty is claimed. This history and
its connected records are development-visible and excluded from an untouched
test split.
