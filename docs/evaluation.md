# Evaluation

The MVP evaluation corpus is repository-local, deterministic, and derived from the supported
taxonomy. Fixtures cover boundary/default/error/retry/authorization/output/state/dependency/
side-effect/ordering changes, a return-annotation signature change, stable refactors, ambiguous
syntax, a function move between files that both remain, no-Python changes, parse failure, and bounded
oversized input.

Metrics are calculated from machine-readable expected categories and evidence anchors:

- material finding precision;
- supported-pattern recall;
- evidence correctness and fabricated evidence count;
- required obligation-concept match;
- candidate-coverage wording;
- deterministic preprocessing latency;
- structured call count and input size.

Each case also has a complete canonical JSON golden with analysis IDs and repository refs/commits
normalized. Contract changes must update these goldens intentionally and record the reason in
`docs/decisions.md`.

Release thresholds are at least 80% material precision, at least 70% supported-pattern recall, zero
fabricated evidence references, an obligation for every high/critical behavior, and no candidate
described as verified coverage. The corpus is intentionally small and synthetic; it does not validate
dynamic behavior or external business contracts.

## Local result (corpus 2026-07-25, suite re-measured 2026-07-26)

On CPython 3.12.3 / Linux with Git 2.43.0, the 17-case corpus (eleven material signature/taxonomy
patterns plus refactor, ambiguity, cross-file move, no-Python, mixed-parse, and
critical-prioritization cases) produced 100.00% material precision (15 of 15 predicted categories),
100% supported-pattern recall (15 of 15), 100% evidence-anchor correctness, 100% required
obligation-concept match, and zero fabricated evidence references. Every high/critical finding had a
linked obligation, and every candidate test remained explicitly unverified. The corpus now emits no
prediction outside its reviewed expected labels.

The previous measurement on this same corpus was 88.24% precision (15 of 17). The two surplus
predictions were both `ordering_change` findings produced by comparing `statement_order` tuples for
inequality when those tuples embed expression content, so a pure content edit synthesized a phantom
reordering. Ordering is now defined as a permutation, and both surplus findings disappeared: the
`dependency arguments` case, where only a keyword argument was added, was a false positive outright,
and the `side effect` case, where `notify(user)` was inserted ahead of the return, was redundant with
the `side_effect_change` that the same insertion already produces. Recall did not move, and no
genuine finding was lost. The expected labels were not widened to absorb the old output — the fix
went into the analyzer, consistent with the 2026-07-17 fixture-label review. See
`docs/decisions.md`, "Output-affecting corrections (2026-07-25, statement ordering)".

The deterministic performance suite covers a 100-symbol AST fixture, a 500-symbol mass-rename
matching fixture, and a warmed full-service fixture with 40 files, 3,000 changed lines, and 100
symbols, with five-, two-, and five-second ceilings respectively. They completed in approximately
0.01, 0.41, and 0.29 seconds in the 2026-07-26 timing run. The complete automated suite reports
95.62% overall branch-aware coverage across 516 passing tests and one skip, with at least 90% branch
coverage in every critical module — the lowest is `obligations.py` at 90.48%, followed by
`renderer.py` at 90.91%, `git_diff/` at 91.75%, and the `ast_diff/` package at 91.86%. `cli.py` and
`coverage_map.py`, added to the critical set on 2026-07-25, are both at 100%. The suite ran with no
`ANTHROPIC_API_KEY` in the environment: no live LLM call or credential was used, and none is
required.

The corpus figures are dated 2026-07-25 and still hold after the 2026-07-26 review fixes:
`tests/fixtures/golden/canonical_outputs.json` is byte-identical across them, and precision,
recall, evidence anchors, and obligation concepts are all computed from exactly those pinned
analyses. See `docs/decisions.md`, "Output-affecting corrections (2026-07-26)".

An earlier 2026-07-19 entry also recorded 100% material precision, but that figure was never measured
on the reviewed 17-case corpus: the two `ordering_change` findings are present in the reviewed
goldens from `af7479d` onward, so the note was a recording error. The 100% above is different in
kind — it is measured against unchanged expected labels after the analyzer stopped emitting the two
surplus findings.

The fixture-label review removed a state-transition expectation from the retry-predicate case because
the assignment itself was unchanged; retaining it would have rewarded a false positive. These numbers
describe only the bounded synthetic corpus and are not a claim about arbitrary repositories.

## Real pull requests

`docs/real-pr-validation.md` records what the analyzer says on real merged pull requests from
public repositories, produced by the opt-in `scripts/validate_real_prs.py` harness. Those are
**observations on unlabeled data, not precision measurements** — the diffs are not reviewed to
the standard this corpus requires, and real-PR cases are deliberately not added to the goldens.
The measured numbers are the ones above.

