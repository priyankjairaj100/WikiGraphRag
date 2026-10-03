Authored correction contracts, version 0.5

These ten miniature cases are deliberately constructed implementation controls.
They are not a natural benchmark, extraction experiment, or paper accuracy
result. Each mention has one authored resolved reading and one unresolved
reading. Every resolved reading scores 10, unresolved scores 0, CORRECTS scores
2, RESTATES scores 1, and a null link scores 0. Consequently all four methods
are expected to agree on these easy scores. Agreement demonstrates common
semantics; it cannot support a joint-decoding advantage.

Run from the project directory:
  python scripts/run_correction_diagnostics.py

The script deterministically regenerates ten input caches, their manifest and
eleven authored questions, decodes each cache using independent, iterative,
iterative_restarts and joint_exact, and writes all 44 predictions before opening
expectations.json. It then checks the separate authored expected contracts.
There are 40 case/method decodes and 544 individual contract assertions. Counts
are implementation checks, not independent experimental observations.

Cases

atomic_replacement: a source explicitly replaces its earlier complete CEO
assertion over the same event interval. No date boundary is introduced.

correction_chain: the third assertion replaces the second, whose correction of
the first remains operative. The first assertion never resurrects.

sibling_conflict: two explicit replacements target the same original assertion
with different values. Both replacement supports remain, yielding uncertainty.

unrelated_issuer: a different declared asserting authority attempts replacement.
The selected correction link is rejected; conflicting supports remain.

missing_certificate: an apparent CORRECTS candidate lacks the required explicit
replacement certificate. The candidate remains in the cache but cannot be
selected as a supported correction.

independent_corroboration: another assertion independently corroborates the
original value and refers to the same episode. Withdrawing the original leaves
this independent evidence intact. RESTATES alone never means copying.

explicit_copy_dependency: the otherwise parallel third assertion explicitly
depends solely on the withdrawn original support. Its declared essential
dependency makes it inactive, unlike independent corroboration.

neighboring_relation: the old source contains both CEO and CFO assertions. A
CEO correction withdraws only its target assertion. The CFO in that same source
remains answerable. This case contains separate CEO and CFO questions.

same_day_reference: old and correcting records share a calendar availability
day. The explicit target reference supports replacement without inventing an
intraday timestamp.

withdraw_before_date_closure: an independent RESTATES assertion carries an
incompatible exact start date relative to the original claim. Correction first
withdraws that original support and its temporal constraint; only then is the
active temporal network materialized. All four methods can select the authored
graph. Turning CORRECTS off in that fixed graph makes date closure infeasible.

The fixed-selection control sets selected CORRECTS links to null while keeping
all selected readings and other links. It measures the consequence of the
materialization rule. It is not a reoptimized baseline, training comparison,
or evidence that a correction-aware model inferred the links correctly.

All sources use synthetic:// URIs and authored text. Authority attributions,
reference spans, support dependencies and expected answers are authored. Exact
span validation checks integrity only. No source authority is inferred from a
URL, no semantic certificate is independently verified, and no model runs.

Reproducibility

Each prediction explicitly records the objective decomposition over active,
inactive and unresolved readings, active and inactive temporal links, correction
links and null choices. The shared historical-interpretation objective retains
inactive scores. Forty additional contracts verify these components sum to the
selected objective; this does not make that objective an active-answer utility.

Timing is omitted from the persisted decoder records because this experiment
does not evaluate speed or compute cost. Two consecutive local executions
produced byte-identical contents for all 15 JSON artifacts: ten caches,
manifest, questions, expectations and the two result files. All 544 contract
assertions passed on the final execution. The result ledger explicitly records
that no method-advantage or research-accuracy claim is made.
