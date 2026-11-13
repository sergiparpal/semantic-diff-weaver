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
0.15.0, and 0.16.0, the 2026-11-04 one for 0.17.0, the 2026-11-06 one for 0.18.0, the 2026-11-07
one for 0.19.0, the 2026-11-08 ones for 0.19.1, 0.19.2, and 0.19.3, the 2026-11-09 one for 0.19.4,
the 2026-11-10 one for 0.20.0, the 2026-11-11 one for 0.20.1, and the 2026-11-12 one for 0.21.0,
found the same figures. Of 0.4.1's
corrections only the output-contract one reaches the corpus: the `return annotation signature` case
is now worded as a change to typing alone, at 0.80 confidence instead of 0.879, and keeps its
category, risk, and obligations. 0.5.0 gives every case a
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
no structural rule names, now quotes the lines that changed instead of a fallback scenario. 0.19.1
changes no golden: what it ships differs from 0.19.0 only in the Action's job summary and the
skills' descriptions, outside the analysis. Nor does 0.19.2, which discards a model's prose that
copies what the repository showed it, since no case runs with a model. Nor does 0.19.3: no case
runs with a model, so no model's claim merges into a finding, and parsing without the analyzed
code's warnings changes only what reached stderr. Nor does 0.19.4, whose two corrections act on a
model's claims and on the retry of a model's call, for the same reason. Nor does 0.20.0, whose
three rules name changes no case makes: no case changes `__all__` or only adds constants, the
`module constant` case changes a constant's value, and the change no rule names in the
`ambiguous metaprogrammed syntax` case lies in a function without a `return`, so it feeds no
returned value. Nor does 0.20.1, which discards a model's obligations of type `review`, since no
case runs with a model. Nor do 0.21.0's five rules: no case removes a symbol, sets a class's
attribute or one a method keeps on its instance, or assigns a value longer than 250 characters, and
the two cases that change an assignment write names that mean no retry, `ROOTS` and the
`state assignment` case's `state`, which keeps its category.

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
completed in approximately 0.07, 0.72, 1.06, 1.36, 0.81, and 1.32 seconds in the 0.21.0 timing run,
across six runs in which the third took 0.96 to 1.20 and the fourth 1.20 to 1.43. 0.20.1's code,
timed beside it, took 0.07, 0.76, 1.06, 1.24, 0.79, and 1.25, the third 0.99 to 1.12 and the fourth
1.21 to 1.42. 0.21.0 records what each assignment writes and digests a value cut at 250 characters;
the fourth and sixth medians differ within the runs' ranges, and the round's own pull request, timed
beside 0.20.1's code the same morning, had them the other way, 1.39 and 1.38 against 1.42 and 1.49.
0.20.1's own run, the day before, timed 0.06, 0.66, 0.97, 1.25, 0.73, and 1.16 seconds, across six
runs in which the third took 0.93 to 1.22 and the fourth 1.17 to 1.53, on a machine
about 1.5 times slower than the day before. 0.20.0's code, timed beside it, took 0.06, 0.66, 0.95,
1.17, 0.74, and 1.15, the third 0.90 to 1.01 and the fourth 1.15 to 1.19: 0.20.1 changes no code a
deterministic analysis runs. An earlier run, in which two of the third fixture's six took 1.90 and
2.19 seconds, was repeated. 0.20.0's own run, the day before, timed 0.03, 0.44, 0.60, 0.78, 0.47,
and 0.74, the third 0.59 to 0.64 and the fourth 0.77 to 0.81, beside 0.19.4's code at 0.02, 0.44,
0.57, 0.78, 0.47, and 0.74, the third 0.55 to 0.59 and the fourth 0.77 to 0.78: 0.20.0 finds, in
each function it extracts, the lines that feed the value it returns. Before #108 read them from a
function's statements alone, the third took 0.67 to 0.68 in the same runs. The first fixture runs
first in its session; timed after another, it took 0.023 seconds beside 0.19.4's 0.020. 0.19.4's own
run, the day before, timed 0.02, 0.45, 0.57, 0.78, 0.48, and 0.75, the third 0.56 to 0.58 and the
fourth 0.77 to 0.79, beside 0.19.3's code at 0.02, 0.45, 0.59,
0.80, 0.49, and 0.75, the third 0.58 to 0.60 and the fourth 0.78 to 0.87: 0.19.4 changes no code a
deterministic analysis runs. 0.19.3's own run, the day before, timed 0.02, 0.46, 0.58, 0.78, 0.49,
and 0.76, the third 0.57 to 0.65 and the fourth 0.77 to 0.88, beside 0.19.2's code at 0.02, 0.46,
0.59, 0.80, 0.51, and 0.77, the third 0.58 to 0.65 and the fourth 0.79 to 0.82: the fixtures parse
through the warnings filter 0.19.3 puts around each parse, and the two differ by at most 0.02
seconds, within the runs' spread. 0.19.2's own run, earlier that day on a busier machine, timed
0.03, 0.51, 0.67, 0.87, 0.55, and 0.82, the third 0.63 to 0.70 and the fourth 0.87 to 0.93, beside
0.19.1's code at 0.02, 0.52, 0.65, 0.87, 0.55, and 0.83, the third 0.63 to 0.69 and the fourth 0.86
to 0.90: 0.19.2 changes no code a deterministic analysis runs. 0.19.1's own run, earlier that day,
timed 0.02, 0.46, 0.59, 0.80, 0.51, and 0.77, the third 0.58 to 0.62 and the fourth 0.78 to 0.84,
beside 0.19.0's code at 0.02, 0.46, 0.60, 0.81, 0.50, and 0.78, the third 0.59 to 0.62 and the
fourth 0.80 to 0.83: 0.19.1 changes no code the fixtures run. 0.19.0's own run, a day earlier, timed
0.02, 0.48, 0.62, 0.83, 0.55, and 0.81, the third 0.59 to 0.67 and the fourth 0.82 to 1.31, beside
0.18.0's code at 0.02, 0.48, 0.61, 0.96, 0.54, and 0.81, the third 0.58 to 0.64 and the fourth 0.83
to 1.10: where 0.19.0's code runs, recording what each delta changed and writing obligations from
it, the two differ by at most 0.01 seconds, and the fourth fixture, whose code it does not change,
varied with the machine. 0.18.0's own run, a day earlier, timed 0.02, 0.45, 0.58, 0.81, 0.51, and
0.77, the third 0.57 to 0.63 and the fourth 0.79 to 0.88, beside 0.17.0's code at 0.03, 0.43, 0.58,
0.80, 0.49, and 0.75: 0.18.0 changed no code the fixtures run. 0.17.0's own run, two days before
that, timed 0.02, 0.43, 0.57, 0.78, 0.47, and 0.74, the third 0.57 to 0.61 and the fourth 0.76 to
0.85, beside 0.16.0's code at 0.02, 0.41, 0.53, 0.78, 0.47, and 0.74, the third 0.52 to 0.54: each
of the full-service fixture's 80 extractions then also walked its file once more, for what its
annotations can read. Before that walk took the place of three, the fixture took 0.61 beside
0.16.0's 0.53. 0.16.0's own run, two days before that, timed 0.02, 0.43, 0.55, 0.82, 0.53, and 0.80,
the third 0.51 to 0.63 and the fourth 0.76 to 0.93, beside 0.15.0's code at 0.02, 0.46, 0.51, 0.83,
0.49, and 0.79, the third 0.47 to 0.52: each of the full-service fixture's 80 extractions then also
walked its file once for the names it mentions, reads, and imports, and a module took a second
fingerprint without its imports. Before those walks were folded into one, and the rarer ones kept to
a file that writes `TYPE_CHECKING` or a body that raises, the fixture took 0.66 beside 0.15.0's
0.48. 0.15.0's own run, earlier that day, timed 0.02, 0.41, 0.49, 0.80, 0.49, and 0.75, the fourth
0.79 to 0.90 and the last 0.73 to 0.76, beside 0.14.0's code at 0.02, 0.43, 0.49, 0.80, and 0.50 on
the five fixtures it has, the fourth 0.79 to 0.86: the full-service fixture has no test file, so the
selection every analysis now makes reads none there. 0.14.0's own run, an hour and a half earlier,
timed 0.02, 0.42, 0.48, 0.81, and 0.48, the fourth 0.80 to 0.84 and the last 0.48 to 0.50, beside
0.13.0's code at 0.02, 0.42, 0.48, 0.81, and 0.49, the fourth 0.79 to 0.84 and the last 0.49 each
time: no fixture names a coverage run, so 0.14.0 reads no test contexts in them, and they take as
long as before. 0.13.0's own run, the evening before, timed 0.02, 0.41, 0.47, 0.82, and 0.48, the
fourth 0.81 to 0.85 and the last 0.48 to 0.51, of which grouping takes a tenth of a second and
building its 500-module graph the rest, beside 0.12.0's code at 0.02, 0.41, 0.47, and 0.81, the
fourth 0.79 to 0.86, so the four fixtures both have took as long as before. 0.12.0's own run, three
hours earlier, timed 0.02, 0.43, 0.47, and 0.81, the last 0.78 to 0.85, beside 0.11.1's code at
0.02, 0.41, 0.50, and 0.81, the last 0.80 to 0.81: the one bounded `git log` 0.12.0 adds to an
analysis with tests costs no more than the runs vary. 0.11.1's own run, 45 minutes earlier, timed
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

The ceilings also hold on modest hardware. On 2026-11-08 the suite ran six times at 6889c53 on a
Raspberry Pi 5 (four Cortex-A76 cores at 2.4 GHz, Debian 13, Python 3.13.5), unthrottled at 56 to
60 °C, and every fixture met its ceiling in every run. The intervals the six assertions compare took
0.05, 0.99, 0.47, 1.80, 0.30, and 1.90 seconds as medians, 1.8 to 3.0 times the development
machine's 0.02, 0.46, 0.26, 0.73, 0.10, and 0.77 that morning. The mass-rename fixture comes
closest, at half of its two-second ceiling, and no other reaches two fifths of its own. Those
intervals leave out what each test builds before its clock starts, which the call-phase figures
above include; on the Pi those were 0.05, 0.99, 0.97, 2.02, 1.11, and 1.91.

The complete automated suite reports 98.32% overall branch-aware coverage across 2,337 passing tests
and one skip, the real-Hermes discovery test, with at least 90% branch coverage in every critical
module — the lowest is `path_policy.py` at 92.42%, followed by `git_diff/` at 93.16%,
`semantic_interpreter.py`, which 0.19.2, 0.19.4, and 0.20.1 extend, at 94.23%, and `config.py` at
94.74%. `cli.py`, `coverage_map.py`, and `baseline.py`, added to the critical set on 2026-10-31, are
at 100%, as are `skeletons.py`, added with test skeletons, `change_groups.py`, added with connected
changes, and `selection.py`, added with test selection. `schema_diff/`, added with API schemas and
reading configuration files too, is at 98.91%, `mcp_server.py`, added on 2026-10-05, at 98.21%, and
`ast_diff/`, whose comparison 0.16.0, 0.17.0, 0.19.0, 0.20.0, and 0.21.0 extend, at 97.18%, up from
97.09%. `obligations.py`, which 0.19.0 writes from evidence, is at 98.61%, and
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

`docs/benchmark.md` measures the analyzer on 180 real pull requests from thirty public repositories,
against the labels of `benchmark/cases.json`. For the first 90, a model wrote one for every Python
symbol they change before reading the analyzer's output, and a person reviewed all 251, correcting
42. For the last 90, two models labeled each of their 240 symbols blind and apart, and the person
settled every label that is scored. The 150 development cases are the ones the analyzer is changed
against, and the 30 held-out cases, labeled and reviewed before its next change, measured that
change on code it was not designed from. Their baseline, in deterministic mode with 0.21.0 on
2026-11-13: every one of the 44 held-out behavior changes got a finding, 36 of them in an expected
category, and 2 of the 43 unchanged symbols got one; 44 of the 86 scored findings, and 44 of the 68
that name a behavior category, are in an expected one, against development's 188 of 403 and 188 of
304. The fifth round's correction, designed from those 150 development cases and measured once on
the held-out ones, reads no limit as a retry and a function as its nested functions change: held
out, 37 of the 44 behavior changes get a finding in an expected category and 45 of 86 scored
findings are in one, still with 2 of the 43 unchanged symbols given a finding; in development, 154
of 199 and 193 of 406. The held-out split before them, which has since joined development, gave
the fourth round's baseline, in deterministic mode with 0.20.1 on 2026-11-11: 41 of its 42 behavior
changes got a finding, 22 of them in an expected category, and 5 of its 38 unchanged symbols got
one; 32 of the 74 scored findings, and 32
of the 49 that name a behavior category, are in an expected one, against the development cases' of
the time 124 of 322 and 124 of 225. The fourth round's correction, designed from those 120
development cases and measured once on the held-out ones, names what assignments write and what
removed symbols took: held out, 26 of the 42 behavior changes get a finding in an expected category
and 36 of 73 scored findings are in one, still with 5 of the 38 unchanged symbols given a finding;
in development, 124 of 157 and 152 of 330. The third held-out
split, which joined development before it, gave the third round's baseline, in
deterministic mode with 0.19.0 on 2026-11-08: every one of its 37 behavior changes got a finding, 30
of them in an expected category, and none of its 10 unchanged symbols got one; 37 of the 72 scored
findings, and 37 of the 45 that name a behavior category, are in an expected one. Its labels list
more categories per behavior change than the development cases of the time, 3.43 against 2.17, so
these figures do not compare with their 48 of 262 and 48 of 128. The third round's correction,
designed from those 90 development cases and measured once on the held-out ones, names the output
contract changes the rules had called unknown: held out, 32 of the 37 behavior changes get a finding
in an expected category and 47 of 74 scored findings are in one, still with no unchanged symbol
given a finding; in development, 70 of 120 and 77 of 248. The second held-out split, which joined
development before it, gave the second round's baseline, in deterministic mode
with 0.16.0 on 2026-11-03: every one of its 44 behavior changes got a finding, 16 of them in an
expected category, and 18 of its 29 unchanged symbols got one too; 16 of the 107 scored findings,
and 16 of the 50 that name a behavior category, are in an expected one. The second round's
correction, released in 0.17.0 and measured on them once it was designed, leaves 14 of those 29
symbols with a finding and 16 of 103 scored findings in an expected category, and every behavior
change keeps its finding and its category. These are the numbers the corpus above does not give.
With a model, `openai/gpt-oss-120b` on the development cases, the findings stayed as they were but
for two wrong ones, and a blind review judged 6 of 20 of the test obligations the model adds
useful, and none of 20 deterministic ones. With 0.19.2 on all 90 development cases, three models
through OpenRouter, deepseek-v4-flash, llama-3.1-8b-instruct, and gpt-oss-120b, left precision in
every category and the unchanged symbols' findings as they were, for 0.16 USD in all, and so did
gpt-6.1-terra through Hermes on 15 of them. None cited evidence it was not shown. A model's claim
that merged into a deterministic finding moved two findings to another symbol, one of them a
behavior change's only finding, and a correction since keeps a merged finding on its own symbol.
The six findings the models added were unknown changes beside findings already on the same
evidence, and another correction since adds none. A fourth blind review, of obligations on the 30
development cases the obligations from evidence were not designed from, judged 10 of 20
deterministic ones useful, and 9 of the 37 the four models added: 3 of 10 each of gpt-oss-120b's and
deepseek-v4-flash's, 3 of gpt-6.1-terra's 7, and none of llama-3.1-8b-instruct's 10.

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
`docs/decisions.md`, 2026-11-07. A nightly job on a Raspberry Pi has measured 20 mutants each of
distiller and claim-ledger since 2026-11-08; its first night, at distiller's 3012a73 and
claim-ledger's cd0c05e, caught 19 and 13 of them, with `select` choosing 54% and 83% of their test
files on average, and missed none.

## Test selection against real red runs

`scripts/shadow_select.py` measures `select` against failures that happened: the red pull-request
runs of public projects, whose failed tests it reads from their CI logs. The repositories this
project is tried on give none, since their pre-commit hooks run the whole suite before every
commit. A failed test file `select` left out is shared when the same file failed within three days
where no change `select` connected to it ran, and unexplained otherwise. A run is missed when an
unexplained file failed beside a selected one, so that running the selection alone would still
have failed, and escaped when no selected file failed.

On 2026-11-08 it read the benchmark's 20 projects: their newest red pull-request runs from the last
85 days, up to 15 a project, 268 in all, from 2026-08-21 to 2026-11-08. In 54 a test file of the
head failed, 216 files in all. The other 214 failed in lint, type checks, coverage thresholds,
builds, or a downstream project's tests (124), never started a job (88), or had lost their logs
(2).

| Measure | Default configuration | Documentation ignored |
| --- | --- | --- |
| Whole suite selected | 43 of 54 runs | 13 of 54 |
| Test files chosen otherwise, on average | 95%, over 11 runs | 93%, over 41 |
| Runs caught, explained, missed, escaped | 53, 0, 1, 0 | 51, 2, 1, 0 |
| Failed files selected, shared, unexplained | 212, 0, 4 | 210, 2, 4 |

No run escaped. Nearly every pull request changes a file that is not Python, most often a changelog
fragment, so by default `select` runs the whole suite. With documentation and changelogs ignored, as
`selection.ignore` lets a repository say, it narrows 41 runs, yet keeps 93% of the test files on
average: most of these packages import nearly all of themselves from their `__init__.py`, so almost
every test reaches almost any change. Only DRF's selections were narrow, 56% over 6 runs.

The missed run is DRF's pull request 9751. It added to `tests/test_validators.py` a Django model
with a constraint named `unique_constraint`, and building the test database for the session failed
on `relation "unique_constraint" already exists`, so 1,027 tests errored at setup, in 56 files.
`select` chose 55 of the 61 test files, 52 of the failed ones among them, and left out 4 that never
reach the change: they failed only because the session's database could not be built. No static
reading sees a test failed by what another test file sets up for the whole session, and
`docs/configuration.md` now says so. The two shared failures are `tests/test_parsers.py` in DRF's
pull requests 10060 and 10062 on 2026-10-06. The first changed only a documentation page, which
`select` rightly connects to no test, and both failed `test_get_encoded_filename` on the same
mangled file name. See `docs/decisions.md`, 2026-11-08.

## The inference layer against prompt injection

`scripts/prompt_injection.py` analyzes eight fixture changes with a real model, each made twice,
clean and with an instruction to the model in the README or in a changed string, plus one in a
comment as a control. It checks that no evidence the analysis did not extract reaches the output,
and measures what the instructions get the model to write. On 2026-11-08, three runs a variant, 48
runs a model: gpt-oss-120b with low reasoning, 50 calls for 0.0097 USD, before the copy rule, and
llama-3.1-8b-instruct, 51 calls for 0.0097 USD, with it, a schema-rejected reply costing a retry.
Each run's other column is its replies replayed, with the rule on or turned off; turned off, the
replay reproduces gpt-oss-120b's live figures.

| Measure | gpt-oss-120b, before the rule | with it | llama-3.1-8b, before the rule | with it |
| --- | --- | --- | --- | --- |
| Valid runs | 48 of 48 | 48 of 48 | 45 of 48 | 45 of 48 |
| Evidence the analysis did not extract, in the output | 0 | 0 | 0 | 0 |
| Deterministic findings lost | 0 | 0 | 0 | 0 |
| References to evidence never shown, discarded | 2 | 2 | 0 | 0 |
| Injected runs whose replies repeated a marker | 13 of 24 | 13 of 24 | 9 of 21 | 9 of 21 |
| Injected runs that carried one into the analysis | 7 of 24 | 2 of 24 | 3 of 21 | 0 of 21 |
| README command as an obligation's title | 3 of 3 | 0 of 3 | 3 of 3 | 0 of 3 |

The copy rule discards a model's text that repeats 40 or more consecutive characters of what the
call showed it from the repository, or of its own instructions. The 2 injected runs of
gpt-oss-120b still carrying a marker carry the marker alone. Both models, before the rule, titled
an obligation with the README's command in every run that asked for it. llama-3.1-8b's 3 invalid
runs are the README that asked for evidence it was never shown: following it, the model wrote
replies the schema rejects, and the analysis kept its deterministic findings. In both models the
warning that asked for a higher category lowered the confidence instead, from 0.772 to 0.675 and
from 0.835 to 0.750, through the assumptions the model added, and no category moved. Replayed on
the benchmark's 60 development cases, the rule discarded 2 of gpt-oss-120b's 262 proposed
obligations and 1 of its findings, all long quotes of the changed code. The control's comment never
reached the model. See `docs/decisions.md`, 2026-11-08, a model's prose that copies what it was
shown.

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

On 2026-11-11, with release 0.20.0, the suite ran once a case with the plugin, for 1.66 USD. Seven
cases passed every grader. The other two each failed one grader while the answer did what it asked:
- `unverified-candidates` said "The tool doesn't run any tests, so its test-to-change matches are a
  static guess", which the pattern of `says-unverified` did not match.
- In `triggers-in-spanish`, the judge failed an answer in two of three votes. The answer said that
  "una cesta de exactamente 5 artículos antes se rechazaba y ahora se acepta" and proposed checking
  `allowed(5) is True`.

The pattern now also matches "static", "doesn't run", and "without being run". Two regexes replace
that judge, one for each claim: five items now allowed, and a test of `allowed(5)`. On the 16
answers recorded so far, eight a case with and without the plugin, they agree with the judges'
verdicts on every answer but the one the judge failed.
