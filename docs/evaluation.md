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

## Local result (corpus 2026-10-19, re-measured with the suite 2026-11-02)

On CPython 3.12.3 / Linux with Git 2.43.0, the 18-case corpus (eleven material signature/taxonomy
patterns plus refactor, ambiguity, module-constant, cross-file move, no-Python, mixed-parse, and
critical-prioritization cases) produced 100.00% material precision (16 of 16 predicted categories),
100% supported-pattern recall (16 of 16), 100% evidence-anchor correctness (16 of 16), 100% required
obligation-concept match (16 of 16), and zero fabricated evidence references. Every high/critical
finding had a linked obligation, and every candidate test remained explicitly unverified. The corpus
now emits no prediction outside its reviewed expected labels. The 2026-10-31 re-measurements for
0.4.0, 0.4.1, 0.5.0, 0.6.0, and 0.6.1, and the 2026-11-01 ones for 0.6.2, 0.7.0, 0.8.0, 0.8.1,
0.8.2, 0.9.0, 0.10.0, 0.11.0, 0.11.1, 0.12.0, and 0.13.0, and the 2026-11-02 ones for 0.14.0,
0.15.0, and 0.16.0, the 2026-11-04 one for 0.17.0, the 2026-11-06 one for 0.18.0, and the
2026-11-07 one for 0.19.0, found the same figures. Of 0.4.1's corrections only the output-contract
one reaches the corpus: the `return annotation signature` case is now worded as a change to typing
alone, at 0.80 confidence instead of 0.879, and keeps its category, risk, and obligations. 0.5.0
gives every case a
verdict, 3 `pass`, 12 `warn`, and 3 `review_required`, and a `null` baseline, without changing a
finding, risk, confidence, or obligation. 0.6.0 changes no golden: its baseline lives in the GitHub
Action's comment. Nor do 0.6.1 and 0.6.2. The corpus's one dependency case changes a call on a
client passed in as a parameter, which stays a dependency interaction; no case changes a `print`;
and the `state assignment` case changes the value it writes to `state`, so its state word comes from
the assignment that changed. 0.7.0 adds test skeletons beside the analysis and changes none, nor do
0.8.0, which serves them as a tool, 0.8.1, which changes how the package is built, and 0.8.2, which
changes where a new test file goes. 0.9.0 compares API schemas, which no case contains, and changes
one golden line: the `no changed Python source` case's limitation now says the diff held no included
changed Python source or API schema. 0.10.0 compares the configuration files a repository names,
which no case does, and changes none. 0.11.0 groups the findings calls connect, and calls connect
the findings of no case: every golden changes in its `schema_version`, now 1.3, and an empty
`change_groups` alone. 0.11.1 changes only how the brief writes a code span, and no golden. 0.12.0
reads the commits up to the base for tests that changed with the code; each corpus repository has a
single commit there, too few to make any a candidate, and no golden changes. 0.13.0 groups a changed
module variable with the changed code that reads it; in the `module constant` case the function
reading `ROOTS` did not change, so no case gains a group, and every golden changes in its
`schema_version`, now 1.4, alone. 0.14.0 names the tests a coverage run saw execute the changed
lines, and no case supplies a coverage run, so no golden changes. 0.15.0 names the test files a
change can affect, and no case's repository has one: every golden changes in its `schema_version`,
now 1.5, and gains a `test_selection` that selects no test file, but for the `no changed Python
source` case, whose changed README runs the whole suite. 0.16.0 reads code only a type checker
reads, imports in step with the code using them, and a reworded exception message as changing
nothing, and a call into the package's own code as internal, and no golden changes. 0.17.0 reads
a callable's parameter annotations alone, a module's or a plain class's annotations and type
variables, and a module's version string as changing nothing, and no golden changes: the `return
annotation signature` case changes a return annotation, which stays an output contract change.
0.18.0 changes no golden: its new warning reads a coverage run's test contexts, which no case
supplies, and nothing else it changes runs in a deterministic analysis. 0.19.0 writes a Python
finding's obligations from what its evidence changed, and 15 of the 18 goldens change in them alone:
their text, number, and priority, 37 obligations becoming 21, and the summary's count of them and
the overall confidence it weighs by them. No finding, risk, evidence item, or verdict moves. Two
required obligation concepts that named scenarios no longer fitting their change were revised, as
`docs/decisions.md` records, and all 16 match. The `ambiguous metaprogrammed syntax` case, a change
no structural rule names, now quotes the lines that changed instead of a fallback scenario.

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

CI times the performance suite on its Ubuntu and Windows legs. The macOS leg leaves it out, since
the speed of its Intel runner varies too much for wall-clock ceilings: its slowest attempts missed
ceilings that every other leg met, in code the change being tested had not touched.

The deterministic performance suite covers a 100-symbol AST fixture, a 500-symbol mass-rename
matching fixture, a warmed full-service fixture with 40 files, 3,000 changed lines, and 100 symbols,
a chain of calls on instances across a call graph at its module limit, a variable 4,990 functions
read across a graph at its module limit, and a test selection following 999 test files through a
chain of 999 modules at its file limit, with five-, two-, five-, and five-second ceilings for the
first four, a one-second ceiling on grouping the fifth, and a five-second ceiling on the last. They
completed in approximately 0.02, 0.48, 0.62, 0.83, 0.55, and 0.81 seconds in the 0.19.0 timing run,
across six runs in which the third took 0.59 to 0.67 and the fourth 0.82 to 1.31. 0.18.0's code,
timed beside it, took 0.02, 0.48, 0.61, 0.96, 0.54, and 0.81, the third 0.58 to 0.64 and the fourth
0.83 to 1.10: where 0.19.0's code runs, recording what each delta changed and writing obligations
from it, the two differ by at most 0.01 seconds, and the fourth fixture, whose code it does not
change, varied with the machine. 0.18.0's own run, a day earlier, timed 0.02, 0.45, 0.58, 0.81,
0.51, and 0.77, the third 0.57 to 0.63 and the fourth 0.79 to 0.88, beside 0.17.0's code at 0.03,
0.43, 0.58, 0.80, 0.49, and 0.75: 0.18.0 changed no code the fixtures run. 0.17.0's own run, two
days before that, timed 0.02, 0.43, 0.57, 0.78, 0.47, and 0.74, the third 0.57 to 0.61 and the
fourth 0.76 to 0.85, beside 0.16.0's code at 0.02, 0.41, 0.53, 0.78, 0.47, and 0.74, the third 0.52
to 0.54: each of the full-service fixture's 80 extractions then also
walked its file once more, for what its annotations can read. Before that walk took the place of
three, the fixture took 0.61 beside 0.16.0's 0.53. 0.16.0's own run, two days before that, timed
0.02, 0.43, 0.55, 0.82, 0.53, and 0.80, the third 0.51 to 0.63 and the fourth 0.76 to 0.93,
beside 0.15.0's code at 0.02, 0.46, 0.51, 0.83, 0.49, and 0.79, the third 0.47 to 0.52: each of the full-service fixture's 80 extractions then also walked
its file once for the names it mentions, reads, and imports, and a module took a second
fingerprint without its imports. Before those walks were folded into one, and the rarer ones kept
to a file that writes `TYPE_CHECKING` or a body that raises, the fixture took 0.66 beside 0.15.0's
0.48. 0.15.0's own run, earlier that day,
timed 0.02, 0.41, 0.49, 0.80, 0.49, and 0.75, the fourth 0.79 to 0.90 and the last 0.73 to 0.76,
beside 0.14.0's code at 0.02, 0.43, 0.49, 0.80, and 0.50 on the five fixtures it has, the fourth
0.79 to 0.86: the full-service fixture has no test file, so the selection every analysis now makes
reads none there. 0.14.0's own run, an hour and a half earlier, timed 0.02, 0.42, 0.48, 0.81, and 0.48,
the fourth 0.80 to 0.84 and the last 0.48 to 0.50, beside 0.13.0's code at 0.02, 0.42, 0.48, 0.81,
and 0.49, the fourth 0.79 to 0.84 and the last 0.49 each time: no fixture names a coverage run, so
0.14.0 reads no test contexts in them, and they take as long as before. 0.13.0's own run, the
evening before, timed 0.02, 0.41, 0.47, 0.82, and 0.48, the fourth 0.81 to 0.85 and the last 0.48 to
0.51, of which grouping takes a tenth of a second and building its 500-module graph the rest, beside
0.12.0's code at 0.02, 0.41, 0.47, and 0.81, the fourth 0.79 to 0.86, so the four fixtures both have
took as long as before. 0.12.0's own run, three hours earlier, timed 0.02, 0.43, 0.47, and 0.81, the
last 0.78 to 0.85, beside 0.11.1's code at 0.02, 0.41, 0.50, and 0.81, the last 0.80 to 0.81: the
one bounded `git log` 0.12.0 adds to an analysis with tests costs no more than the runs vary.
0.11.1's own run, 45 minutes earlier, timed 0.02, 0.41, 0.48, and 0.81 beside 0.11.0's code at 0.02,
0.41, 0.47, and 0.80, since 0.11.1 changes only how the brief writes a code span. 0.11.0's own run,
half an hour before that, timed 0.02, 0.42, 0.47, and 0.81 beside 0.10.0's code at 0.02, 0.45, 0.39,
and 0.82: the full-service fixture, which has no tests, now builds the call graph to group its
findings, at 0.08 seconds of its five-second ceiling. 0.10.0's own run, an hour before that, timed
0.02, 0.42, 0.39, and 0.81, as 0.9.0's code did beside it, since 0.10.0 reads only the configuration
files a repository names and the fixtures name none. 0.9.0's own run, half an hour before that,
timed 0.02, 0.41, 0.38, and 0.80 beside 0.8.2's code at 0.02, 0.41, 0.38, and 0.79, so reading
changed YAML and JSON apart from the Python files left the fixtures' cost as it was. 0.8.2's own
run, an hour before that on a busier machine, timed 0.02, 0.50, 0.45, and 0.93 beside 0.8.1's code
at 0.02, 0.49, 0.44, and 0.95, and 0.8.1's own, earlier that afternoon, 0.02, 0.43, 0.40, and 0.82
beside 0.8.0's code at 0.02, 0.43, 0.39, and 0.81. 0.8.0's own run, earlier that afternoon on a
busier machine, timed 0.02, 0.44, 0.40, and 0.86 beside 0.7.0's code at 0.02, 0.47, 0.41, and 0.88,
so its new tool left the analysis's cost as it was; at noon 0.7.0's own run timed 0.02, 0.42, 0.39,
and 0.82 beside 0.6.2's code at 0.02, 0.43, 0.38, and 0.82. 0.6.2's own run, that morning, timed
0.02, 0.42, 0.38, and 0.83 seconds beside 0.6.1's code at 0.02, 0.42, 0.39, and 0.80, so its two
corrections cost no more than the runs vary. 0.6.1's own run, the evening before, timed 0.02, 0.44,
0.40, and 0.88 seconds, and 0.6.0's 0.02, 0.45, 0.42, and 0.89; one 0.6.0 run under load took 1.30
seconds for the last, and 0.5.0 timed 0.02, 0.42, 0.38, and 0.79 on code 0.6.0 left unchanged. The
0.4.0 run, earlier on 2026-10-31, measured 0.04, 1.52, 0.37, and 1.84 seconds, and `v0.3.0` the same
1.5 seconds for the mass-rename fixture then: the machine was slower at that hour, not the code, and
that fixture is back at a fifth of its ceiling, as on 2026-07-26.

The ceilings also hold on modest hardware. On 2026-11-08 the suite ran six times at 6889c53 on a
Raspberry Pi 5 (four Cortex-A76 cores at 2.4 GHz, Debian 13, Python 3.13.5), unthrottled at 56 to
60 °C, and every fixture met its ceiling in every run. The intervals the six assertions compare took
0.05, 0.99, 0.47, 1.80, 0.30, and 1.90 seconds as medians, 1.8 to 3.0 times the development
machine's 0.02, 0.46, 0.26, 0.73, 0.10, and 0.77 that morning. The mass-rename fixture comes
closest, at half of its two-second ceiling, and no other reaches two fifths of its own. Those
intervals leave out what each test builds before its clock starts, which the call-phase figures
above include; on the Pi those were 0.05, 0.99, 0.97, 2.02, 1.11, and 1.91.

The complete automated suite reports 98.29% overall branch-aware coverage across 2,049 passing tests
and one skip, the real-Hermes discovery test, with at least 90% branch coverage in every critical
module — the lowest is `path_policy.py` at 92.42%, followed by `semantic_interpreter.py` at 92.97%,
`git_diff/` at 93.16%, and `config.py` at 94.74%. `cli.py`, `coverage_map.py`, and `baseline.py`,
added to the critical set on 2026-10-31, are at 100%, as are `skeletons.py`, added with test
skeletons, `change_groups.py`, added with connected changes, and `selection.py`, added with test
selection. `schema_diff/`, added with API schemas and reading configuration files too, is at 98.91%,
`mcp_server.py`, added on 2026-10-05, at 98.21%, and `ast_diff/`, whose comparison 0.16.0, 0.17.0,
and 0.19.0 extend, at 97.01%. `obligations.py`, which 0.19.0 writes from evidence, is at 98.61%, and
`evidence_scenarios.py`, new in it and outside the critical set, at 100%. The suite ran with no
`ANTHROPIC_API_KEY` in the environment: no live LLM call or credential was used, and none is
required.

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

`docs/benchmark.md` measures the analyzer on 120 real pull requests from twenty public
repositories, against the labels of `benchmark/cases.json`. For the first 90, a model wrote one for
every Python symbol they change before reading the analyzer's output, and a person reviewed all
251, correcting 42. For the last 30, two models labeled each of their 53 symbols blind and apart,
and the person settled every label that is scored. The 90 development cases are the ones the
analyzer is changed against, and the 30 held-out cases, labeled and reviewed before its next
change, will measure that change on code it was not designed from. Their baseline, in
deterministic mode with 0.19.0 on 2026-11-08: every one of the 37 held-out behavior changes got a
finding, 30 of them in an expected category, and none of the 10 unchanged symbols got one; 37 of
the 72 scored findings, and 37 of the 45 that name a behavior category, are in an expected one.
Their labels list more categories per behavior change than development's, 3.43 against 2.17, so
these figures do not compare with development's 48 of 262 and 48 of 128. The previous held-out
split, which has since joined development, gave the second round's baseline, in deterministic mode
with 0.16.0 on 2026-11-03: every one of its 44 behavior changes got a finding, 16 of them in an
expected category, and 18 of its 29 unchanged symbols got one too; 16 of the 107 scored findings,
and 16 of the 50 that name a behavior category, are in an expected one. The second round's
correction, released in 0.17.0 and measured on them once it was designed, leaves 14 of those 29
symbols with a finding and 16 of 103 scored findings in an expected category, and every behavior
change keeps its finding and its category. These are the numbers the corpus above does not give.
With a model, `openai/gpt-oss-120b` on the development cases, the findings stayed as they were but
for two wrong ones, and a blind review judged 6 of 20 of the test obligations the model adds
useful, and none of 20 deterministic ones.

`docs/real-pr-validation.md` records what the analyzer says on other real merged pull requests,
produced by the opt-in `scripts/validate_real_prs.py` harness. Those are **observations on
unlabeled data, not precision measurements**, and real-PR cases are not added to the goldens.


## Test selection against mutants

`scripts/mutate_select.py` measures `select`, which names the test files a change can affect,
against faults it plants one at a time in a repository's product code: a test that catches one in a
file `select` left out is a miss. It runs the analyzed repository's tests, so it stays out of the
package, and appends each mutant to a JSONL file that its `summary` subcommand reads. On
2026-11-07, 41 mutants of four repositories gave no miss. For distiller's 12, `select` chose 62% of
its test files on average, and for claim-ledger's 15, 80%. This repository's 6 and meal-manager's 8
could show none, since `select` chose all their test files for every mutant. See
`docs/decisions.md`, 2026-11-07.

## The Claude Code skill

`evals/` measures `skills/semantic-diff/SKILL.md` with `claude plugin eval`: nine cases, each a
repository built by `evals/fixture.sh` and a question a user would ask, run as full Claude Code
sessions with the plugin's real server and graded on the tool calls and the answer. On 2026-11-07,
three runs a case, with the plugin and without it:

| Case | With the plugin | Without it |
| --- | --- | --- |
| `triggers-in-spanish` | 1.00, 1.00, 1.00 | 0.67 |
| `triggers-in-english` | 1.00, 1.00, 1.00 | 0.47 |
| `stale-local-main` | 1.00, 1.00, 1.00 | 0.50 |
| `diverged-main` | 1.00, 1.00, 1.00 | 0.44 |
| `uncommitted-edits` | 1.00, 1.00, 1.00 | 0.60 |
| `unverified-candidates` | 1.00, 1.00, 1.00 | 0.40 |
| `no-unapproved-skeletons` | 1.00, 1.00, 1.00 | 0.25 |
| `path-outside-workspace` | 1.00, 1.00, 1.00 | 0.60 |
| `large-diff-by-include` | 1.00, 1.00, 1.00 | 0.75 |

The scores without the plugin are means of three runs, none of which passed. `uncommitted-edits`
was run again, at 0.82 USD, after a judge's false failure turned one of its graders into a regex.
Its first run had found that the skill did not load for "What does my change do?", which its
description now covers. The suite cost 8.69 USD; see `docs/decisions.md`, 2026-11-07.
