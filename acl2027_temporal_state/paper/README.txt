V0.11 DEVELOPMENT ADDENDUM

results_update_v11.tex, results_numeric_v11.tex and claim_ledger_v11.json record the source-support API, 30 annotation-conditioned lookups, zero complete-source admissions, and two native selected-passage responses that both fail the strict schema. No normalized labels, partial answer salvage, natural method advantage, historical replay or compiled PDF. Previous manuscript sources and ledgers remain unchanged.

V0.10 DEVELOPMENT ADDENDUM

results_update_v10.tex and claim_ledger_v10.json describe bounded correction discovery and two excerpt-conditioned support illustrations. Six policy states show identical direct/dependency active sets when both dependent conclusions are explicitly corrected. Zero full-prefix admissions or new model QA predictions. Eight claims are artifact-bound; no compiled PDF or visual layout verification. All earlier drafts and ledgers remain unchanged.

V0.9 DEVELOPMENT ADDENDUM

results_update_v09.tex and results_numeric_v09.tex report the matched scorer diagnostic and four-history annotation extension; claim_ledger_v09.json binds ten claims to exact artifacts. Three of four authored labels are matched by 4B in both orders, despite zero flips; no natural accuracy or method advantage. One source reference artifact remains blocked. No PDF compilation or visual layout verification was attempted this turn.

The v0.7 and v0.8 drafts and ledgers remain unchanged. The instructions below describe the historical v0.7 skeleton; its zero-stream statement is historical, superseded by the v0.9 state above.

W01 manuscript skeleton, v0.7
===========================

The working research title is Joint Interpretation of Time-Varying Factual
Records. This is an exploratory methods/data draft for an ACL 2027 research
target, not a submission-ready paper or an asserted ACL format.

Primary editable source:
  paper/methods_draft_v07.tex
Claim-to-evidence mapping:
  paper/claim_ledger_v07.json
Verified reference metadata and targeted comparison:
  paper/references_v07.bib
  docs/related_work_update_v07.txt
Compilation status:
  No PDF was produced. The installed pdflatex binary could not find
  pdflatex.fmt; kpsewhich also found neither article.cls nor latex.ltx.
  A complete TeX installation is required for compilation and visual QA.

The draft reports only the explicitly identified completed v0.6 observations.
Ongoing v0.7 candidate/backend/scoring outcomes are intentionally not filled in.
Its evaluation section is prospective. It does not invent numerical gains,
human gold annotations, candidate recall, citations, or methodological novelty.
The targeted related-work section credits temporal memory, dynamic RAG, joint
constrained temporal prediction, temporal constraint networks and truth
maintenance. Ten verified references are cited; remaining review gaps stay
explicit. An arXiv citation is not relabelled a peer-reviewed publication.

The baseline is strong: it freezes maximum net-unary readings, then optimizes
all links exactly conditional on them. The active-support objective remains
an ablation with an observed correction-suppression failure. Both points must
remain explicit in later manuscript revisions.

Candidate recall and residual full-context ambiguity are different questions.
The proposed next study must not use full-context judgments as an oracle to
remove all contextually falsifiable hypotheses before inference. Local-window
generation versus a strong full-context baseline is a prospective controlled
comparison, not a result. The new frozen source-stream protocol specifies
controlled delivery prefixes of exact document versions as the primary task.
Delivery, retrieval, publication and event time remain separate. Historical
replay is a separately certified slice. Zero histories are complete under the
new protocol; no existing results or cutoffs are retroactively admitted.

Compile from the project root, sending intermediates to a temporary directory:
  mkdir -p ../tmp/v07/paper_build
  pdflatex -interaction=nonstopmode -halt-on-error -output-directory=../tmp/v07/paper_build paper/methods_draft_v07.tex
  bibtex ../tmp/v07/paper_build/methods_draft_v07
  pdflatex -interaction=nonstopmode -halt-on-error -output-directory=../tmp/v07/paper_build paper/methods_draft_v07.tex
  pdflatex -interaction=nonstopmode -halt-on-error -output-directory=../tmp/v07/paper_build paper/methods_draft_v07.tex

Before a paper claim is promoted, complete independent natural evaluation,
reference-judgment provenance, candidate-coverage checks, a strong contextual
model comparison, uncertainty/cost analysis, and a verified related-work audit.
The ledger distinguishes implemented contracts, diagnostic observations and
proposals. Its hashes identify the exact evidence snapshot used for this draft.
