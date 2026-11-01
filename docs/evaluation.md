# Evaluation

The MVP evaluation corpus is repository-local, deterministic, and derived from the supported
taxonomy. Fixtures cover boundary/default/error/retry/authorization/output/state/dependency/
side-effect/ordering changes, a return-annotation signature change, a changed module constant that
is not state, stable refactors, ambiguous syntax, a function move between files that both remain,
no-Python changes, parse failure, and bounded oversized input.

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

## Local result (corpus 2026-10-19, re-measured with the suite 2026-11-01)

On CPython 3.12.3 / Linux with Git 2.43.0, the 18-case corpus (eleven material signature/taxonomy
patterns plus refactor, ambiguity, module-constant, cross-file move, no-Python, mixed-parse, and
critical-prioritization cases) produced 100.00% material precision (16 of 16 predicted categories),
100% supported-pattern recall (16 of 16), 100% evidence-anchor correctness (16 of 16), 100% required
obligation-concept match (16 of 16), and zero fabricated evidence references. Every high/critical
finding had a linked obligation, and every candidate test remained explicitly unverified. The corpus
now emits no prediction outside its reviewed expected labels. The 2026-10-31 re-measurements for
0.4.0, 0.4.1, 0.5.0, 0.6.0, and 0.6.1, and the 2026-11-01 ones for 0.6.2, 0.7.0, 0.8.0, 0.8.1,
0.8.2, 0.9.0, 0.10.0, 0.11.0, 0.11.1, 0.12.0, and 0.13.0, found the same figures. Of 0.4.1's
corrections only the output-contract one reaches the corpus: the `return annotation signature` case
is now worded as a change to typing alone, at 0.80 confidence instead of 0.879, and keeps its
category, risk, and obligations. 0.5.0 gives every case a verdict, 3 `pass`, 12 `warn`, and 3
`review_required`, and a `null` baseline, without changing a finding, risk, confidence, or
obligation. 0.6.0 changes no golden: its baseline lives in the GitHub Action's comment. Nor do 0.6.1
and 0.6.2. The corpus's one dependency case changes a call on a client passed in as a parameter,
which stays a dependency interaction; no case changes a `print`; and the `state assignment` case
changes the value it writes to `state`, so its state word comes from the assignment that changed.
0.7.0 adds test skeletons beside the analysis and changes none, nor do 0.8.0, which serves them as a
tool, 0.8.1, which changes how the package is built, and 0.8.2, which changes where a new test file
goes. 0.9.0 compares API schemas, which no case contains, and changes one golden line: the `no
changed Python source` case's limitation now says the diff held no included changed Python source or
API schema. 0.10.0 compares the configuration files a repository names, which no case does, and
changes none. 0.11.0 groups the findings calls connect, and calls connect the findings of no case:
every golden changes in its `schema_version`, now 1.3, and an empty `change_groups` alone. 0.11.1
changes only how the brief writes a code span, and no golden. 0.12.0 reads the commits up to the
base for tests that changed with the code; each corpus repository has a single commit there, too few
to make any a candidate, and no golden changes. 0.13.0 groups a changed module variable with the
changed code that reads it; in the `module constant` case the function reading `ROOTS` did not
change, so no case gains a group, and every golden changes in its `schema_version`, now 1.4, alone.

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
matching fixture, a warmed full-service fixture with 40 files, 3,000 changed lines, and 100 symbols,
a chain of calls on instances across a call graph at its module limit, and a variable 4,990
functions read across a graph at its module limit, with five-, two-, five-, and five-second ceilings
for the first four and a one-second ceiling on grouping the last. They completed in approximately
0.02, 0.41, 0.47, 0.82, and 0.48 seconds in the 0.13.0 timing run, across six runs in which the
fourth took 0.81 to 0.85 and the last 0.48 to 0.51, of which grouping takes a tenth of a second and
building its 500-module graph the rest. 0.12.0's code, timed beside it, took 0.02, 0.41, 0.47, and
0.81, the fourth 0.79 to 0.86, so the four fixtures both have take as long as before. 0.12.0's own
run, three hours earlier, timed 0.02, 0.43, 0.47, and 0.81, the last 0.78 to 0.85, beside 0.11.1's
code at 0.02, 0.41, 0.50, and 0.81, the last 0.80 to 0.81: the one bounded `git log` 0.12.0 adds to
an analysis with tests costs no more than the runs vary. 0.11.1's own run, 45 minutes earlier, timed
0.02, 0.41, 0.48, and 0.81 beside 0.11.0's code at 0.02, 0.41, 0.47, and 0.80, since 0.11.1 changes
only how the brief writes a code span. 0.11.0's own run, half an hour before that, timed 0.02, 0.42,
0.47, and 0.81 beside 0.10.0's code at 0.02, 0.45, 0.39, and 0.82: the full-service fixture, which
has no tests, now builds the call graph to group its findings, at 0.08 seconds of its five-second
ceiling. 0.10.0's own run, an hour before that, timed 0.02, 0.42, 0.39, and 0.81, as 0.9.0's code
did beside it, since 0.10.0 reads only the configuration files a repository names and the fixtures
name none. 0.9.0's own run, half an hour before that, timed 0.02, 0.41, 0.38, and 0.80 beside
0.8.2's code at 0.02, 0.41, 0.38, and 0.79, so reading changed YAML and JSON apart from the Python
files left the fixtures' cost as it was. 0.8.2's own run, an hour before that on a busier machine,
timed 0.02, 0.50, 0.45, and 0.93 beside 0.8.1's code at 0.02, 0.49, 0.44, and 0.95, and 0.8.1's own,
earlier that afternoon, 0.02, 0.43, 0.40, and 0.82 beside 0.8.0's code at 0.02, 0.43, 0.39, and
0.81. 0.8.0's own run, earlier that afternoon on a busier machine, timed 0.02, 0.44, 0.40, and 0.86
beside 0.7.0's code at 0.02, 0.47, 0.41, and 0.88, so its new tool left the analysis's cost as it
was; at noon 0.7.0's own run timed 0.02, 0.42, 0.39, and 0.82 beside 0.6.2's code at 0.02, 0.43,
0.38, and 0.82. 0.6.2's own run, that morning, timed 0.02, 0.42, 0.38, and 0.83 seconds beside
0.6.1's code at 0.02, 0.42, 0.39, and 0.80, so its two corrections cost no more than the runs vary.
0.6.1's own run, the evening before, timed 0.02, 0.44, 0.40, and 0.88 seconds, and 0.6.0's 0.02,
0.45, 0.42, and 0.89; one 0.6.0 run under load took 1.30 seconds for the last, and 0.5.0 timed 0.02,
0.42, 0.38, and 0.79 on code 0.6.0 left unchanged. The 0.4.0 run, earlier on 2026-10-31, measured
0.04, 1.52, 0.37, and 1.84 seconds, and `v0.3.0` the same 1.5 seconds for the mass-rename fixture
then: the machine was slower at that hour, not the code, and that fixture is back at a fifth of its
ceiling, as on 2026-07-26.

The complete automated suite reports 97.73% overall branch-aware coverage across 1,409 passing tests
and one skip, the real-Hermes discovery test, with at least 90% branch coverage in every critical
module — the lowest is `path_policy.py` at 92.42%, followed by `semantic_interpreter.py` at 92.62%,
`git_diff/` at 93.10%, and `config.py` at 94.44%. `cli.py`, `coverage_map.py`, and `baseline.py`,
added to the critical set on 2026-10-31, are at 100%, as are `skeletons.py`, added with test
skeletons, and `change_groups.py`, added with connected changes. `schema_diff/`, added with API
schemas and reading configuration files too, is at 98.91%, and `mcp_server.py`, added on 2026-10-05,
at 98.21%. The suite ran with no `ANTHROPIC_API_KEY` in the environment: no live LLM call or
credential was used, and none is required.

The 17-case figures, dated 2026-07-25, held after the 2026-07-26 review fixes:
`tests/fixtures/golden/canonical_outputs.json` was byte-identical across them, and precision,
recall, evidence anchors, and obligation concepts are all computed from exactly those pinned
analyses. See `docs/decisions.md`, "Output-affecting corrections (2026-07-26)". The 2026-10-19
`module constant` case added one predicted and one expected category, and left the other seventeen
goldens unchanged; see "Output-affecting corrections (2026-10-19, assignments named for state)".

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

