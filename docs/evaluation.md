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

## Local result (2026-07-25)

On CPython 3.12.3 / Linux with Git 2.43.0, the 17-case corpus (eleven material signature/taxonomy
patterns plus refactor, ambiguity, cross-file move, no-Python, mixed-parse, and
critical-prioritization cases) produced 88.24% material precision (15 of 17 predicted categories),
100% supported-pattern recall (15 of 15), 100% evidence-anchor correctness, 100% required
obligation-concept match, and zero fabricated evidence references. Every high/critical finding had a
linked obligation, and every candidate test remained explicitly unverified.

Precision is below the recall figure because two cases emit an `ordering_change` finding beyond their
minimal expected labels: the `side effect` case, where inserting `notify(user)` ahead of the return
genuinely does change the call sequence, and the `dependency arguments` case, where only a keyword
argument was added and the extra finding is a false positive. Precision stays above the 80% release
threshold. The labels are deliberately not widened to absorb them — adding `ordering_change` to the
expected sets would reward the output rather than measure it, the same reasoning as the 2026-07-17
fixture-label review.

The deterministic performance suite covers a 100-symbol AST fixture, a 500-symbol mass-rename
matching fixture, and a warmed full-service fixture with 40 files, 3,000 changed lines, and 100
symbols, with five-, two-, and five-second ceilings respectively. They completed in approximately
0.028, 1.047, and 0.275 seconds in the local timing run. The complete automated suite reports 95.00%
overall branch-aware coverage across 297 passing tests and one skip, with at least 90% branch
coverage in every critical module — the lowest is `renderer.py` at 90.48%, and the `ast_diff/`
analysis package, added to the critical set on 2026-07-25, is at 91.86%. No live LLM call or
credential was used.

The 2026-07-19 entry this replaces recorded 100% material precision. That figure did not hold for the
reviewed 17-case corpus at any commit since the corpus was introduced; the two `ordering_change`
findings above are present in the reviewed goldens from `af7479d` onward. Treat it as a recording
error in the earlier note, not a regression.

The fixture-label review removed a state-transition expectation from the retry-predicate case because
the assignment itself was unchanged; retaining it would have rewarded a false positive. These numbers
describe only the bounded synthetic corpus and are not a claim about arbitrary repositories.
