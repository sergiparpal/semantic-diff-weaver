# Architecture

The plugin follows a typed, bounded pipeline:

```text
request/config -> repository + resolved refs -> changed committed blobs
-> AST deltas and API schema deltas -> deterministic evidence/candidates
-> static candidate-test index -> optional structured LLM -> reconciliation
-> optional ingested coverage report -> risk/confidence -> obligations and change groups
-> canonical JSON -> optional Markdown
```

`plugin.py` is the only Hermes adapter, and registers two tools: `analyze_semantic_diff` and
`generate_test_skeletons`. Registration closes over `ctx.llm` but performs no Git or LLM
work. `mcp_server.py` serves the same tools and handlers to Claude Code over MCP stdio, always in
deterministic mode and with the Markdown brief as its default result, and `cli.py` is the standalone command line; all three share `service.analyze`
and the public error payload. `service.py` orchestrates modules without importing Hermes internals. The `git_diff/` package is the sole
subprocess boundary and invokes only Git with argument arrays, resolved commit IDs, timeouts, bounded
captured input/output, disabled external diff/text conversion, and `shell=False`. Tree metadata is
collected once per revision and eligible object IDs are read through bounded `cat-file` batches;
per-file patch hunks retain literal path isolation. Changed test code is not analyzed: collection
drops what a path settles — files under a test root and `conftest.py` — before its budgets, and
`service.py` drops a file named like a test module once the candidate-test parser finds a test in
it (`rules.analyze_test_files` keeps both).

The `ast_diff/` package parses text with the Python AST without importing target modules. It consumes
the `SourceRevisionPair` contract in `source.py` rather than Git types, so structural analysis does not
depend on Git and its safety budgets are passed in as an explicit `AstBudget`. It inventories module,
class, function, async-function, method, and async-method symbols; normalizes complete signatures
including return annotations and PEP 695 type parameters on both callables and classes, and records
a callable's parameters without their typing, so that a signature change callers pass the same
arguments across is reported as typing alone; retains only decorator names; preserves overload-like
duplicate definitions; and correlates conservative moves across files that both remain. The
`AstBudget` wall-clock deadline is checked between the parse, matching, and comparison phases and
counts as spent once reached, so a coarse platform clock cannot let an exhausted budget read as
live.

The `schema_diff/` package compares the changed YAML and JSON files that are API schemas, and
consumes `SourceRevisionPair` too. Collection reads those files apart from Python source, under a
file count, critical paths first, and a byte total of their own, and with no hunks: a schema's
evidence is the place in the document a change is, not a line. Whether a file is a schema, its
content decides: an OpenAPI 3 or Swagger 2 root, or a JSON Schema whose `$schema` names a
meta-schema. `load.py` parses JSON with the standard library and YAML with a subclass of the
configuration loader's budgeted `SafeLoader`, then copies the result into plain data under a node
and depth budget of its own, since aliases can make a small file a large tree. `compare.py`
compares operations by method and route — parameters, request bodies, responses, media types,
and the security each requires — then global security, servers, and reusable schemas, and within
a schema the keywords that decide what is accepted or returned. Each delta records whether its
place is part of a request, a response, a reusable component, or a standalone JSON Schema, and
`semantic_candidates.py` classifies it by that: a narrowed `enum` validates a request but changes
what a response returns. `$ref` is compared as text and never resolved. `settings.py` compares
the configuration files `paths.config_files` names, read with the documents and ahead of them, in
YAML, JSON, or TOML: each value by its dotted key path, a list as one value, and the delta
classified by the words of its key and whether its value is a number or a boolean. Both modules
make their deltas through `deltas.py`, which bounds how many one document makes, cuts each symbol,
and redacts what each shows. A configuration key reaches the tests whose body uses its names.

Deterministic rules create evidence before model use. Their term-based probes read the changed
expressions together with the symbol name, because names carry authorization, validation, retry, and
state meaning. For comparisons, conditions, calls, assignments, and loops they read a probe built
from the AST at extraction rather than the display text: identifiers, attribute and keyword
names, and string literals without whitespace (keys, headers, permission names), but not literals
containing whitespace, whose words are prose about the code. An assignment's probe holds its
targets only, so a changed assignment is a state transition when what it writes to, or its
symbol, is named for state; any other is an unknown change for review. A probe covers exactly the
values the delta's bounded display text shows, and only those of them the delta changed: an
unchanged `order.status = ...` does not make a changed `count = ...` beside it a state
transition. Reordered comparisons, assignments, or loops change no value and are read by their
symbol's name; reordered conditions and calls are ordering changes. A changed loop is
a retry only when its own words, or its symbol's name, say so — `attempt`, `retry`, `timeout`,
`sleep`, `backoff`, or `deadline`, but not `limit`, which every loop has — and is otherwise an
unknown change. A changed call that no keyword explains is a dependency interaction unless every
call that changed reaches the module's own code: a function or class the module defines, a method
one of its classes defines, called through `self`, `cls`, or the class, or a builtin that computes
in-process, such as `len` or `isinstance`. Such a change is an unknown change, since the
dependency category's obligations ask how the code handles a dependency's failures. What an
attribute holds is not known statically, so `self.client.fetch()` and `self.callback()` stay
dependency calls, as does a method inherited from another module's class. `warn` and `print` are
side-effect terms, so a changed `warnings.warn()` or `print()` is a side effect: a warning or a
printed line reaches whoever runs the code, as `pytest.warns` and `capsys` observe.
The comparison and condition rules' boundary check reads the same probe:
whether a value the delta changed compares a boundary — an ordering (`<`, `<=`, `>`, `>=`), or an
equality or inequality with a number or a `len()` on one side — taken from its operators and
operands and never from characters in its text, where a literal's `'<'`, a shift, or a synthetic
`<module>` name would count and an `==` consumed by redaction would not. An identity, membership,
or other equality test (`is None`, `in STARS`, `method == 'GET'`) has no below or above to test,
so it is read as a condition comparing no boundary is: by its authorization, validation, and retry
terms, or as an unknown change. `semantic_interpreter.py` sends bounded, delimited evidence through
`ctx.llm.complete_structured`, locally validates output, rejects fabricated evidence IDs, and cannot
initiate actions. Evidence is batched by module and shared changed dependency calls, prioritized by
configured critical paths, bounded per symbol and per call, and retried once only for explicitly
retryable schema/transport failures within the eight-call ceiling. What each bound drops is reported
under its own omission reason, because the three count different units: `llm_batch_limit` for
batches the call ceiling could not accommodate or that retries left unvisited within it,
`model_input_symbol_limit` for a single symbol whose payload cannot fit one call, and
`model_evidence_limit` for a symbol whose evidence was truncated to fit.
A bounded, redacted committed README excerpt may provide repository purpose context. `test_mapper.py`
parses committed tests statically and always labels matches as unverified candidates. A test
imports what its file imports outside test functions and what its own body imports, including
modules loaded with `importlib.import_module` under a name the file writes out or its own
package's name, and the names its file binds to what such a load returns. Tests that
match only through evidence about their file — a mapping, a mirrored name, an import, a run, or a
package import — cannot be told apart by it, so several of them stand as one candidate for the
file, with the symbol `<module>`. A package's `__init__.py` is named by its package for each of
them, and a test imports it by importing the package or a name the package binds, never by
importing one of its modules. Top-level code is no name a test can use, but a changed assignment
names the variables it writes to, so a test whose body or name uses one, as `CODE_ROOTS`, is listed
on its own. `call_graph.py` reads the head revision of the modules tests
import and of the changed ones, then follows their imports, under file, byte, and time bounds and
the AST stage's parse budgets. For each symbol it keeps the names and attribute chains its code
uses, and for each module what its imports bind, so `messages.unexpected_failure` resolves to a
symbol of another module, through re-exporting packages too. It also keeps which symbols are
classes, their bases, and each function's return annotation, so a call on an instance,
`cart.total()`, reaches the method of each class the calling code knows: one it uses, one a
function it calls returns, one held in such a class's fields, one its own class knows, one a
module-level variable it uses holds, its own module's or imported, and the bases they inherit
from, but no subclass's override. An attribute a method reads on `self` names a member of its own
class or its bases only. Code reading a module-level variable calls what its value names without
calling it, as a table of functions does — `classify_delta` runs each classifier `CLASSIFIERS`
holds — through the variables and imports the value copies; what a call in the value calls ran
when the module loaded, and is left out. From what each test names, with the
same-file helpers it calls, the modules it imports or runs, and the names it imports through
re-exporting packages, the mapper follows those uses across modules: a test reaches whatever it
names, whatever that uses in turn, the methods it calls on an instance it holds — of a class it
reaches through calls, or one a module-level variable it reads holds — and the top-level code of
every module loading brings in. Reaching a symbol from a module the test imports counts
more than reaching it only through another module, which never makes a strong candidate on its
own. A reach replaces a mention of the symbol in the test's body, the weaker evidence of the same
thing, and among equal scores a test that names the symbol ranks first, then one whose name shares
more words with the symbol's class, function, or module, each weighed by how few symbols of the
modules beside it name it too, then one reaching the symbol through calls rather than only by
loading a module whose top-level code names it. Category terminology adds only to a test named
after the symbol or its module, one that calls the symbol itself, or one only its file ties to the
change. A private, undecorated helper that no test reaches or names gets no candidate from a test
file, since only its module calls it, unless the graph was cut short by its bounds. A change to an
API operation has no Python symbol to import or call, so it maps to the tests that request its
route: string literals in a test or its same-file helpers that are a path, or a URL whose path
is, with an f-string's placeholders standing for one segment each, matched against the route
template after any base path. One that also calls the operation's method by name ranks higher,
and one that calls only other methods is about another operation of the route. A change to a
reusable schema names no route, and has no candidate. `git_diff/history.py` reads the files each
of the commits up to the base changed, and a test file that changed with a changed module in two
of them or more is evidence about it as a file, as a mirrored name is: alone, it makes the file a
candidate.

When global file or line limits are exceeded, explicitly configured critical paths are analyzed first
and every omitted count/reason is carried into canonical scope and Markdown. Parse-incomplete files
produce bounded unknown findings rather than losing all evidence. Conservative symbol matching can
connect renamed or moved functions, including moves between two existing files, but refuses ambiguous
near-ties and lowers confidence for the unmatched lifecycle findings.

The canonical schema version is `1.4`. Markdown is derived entirely from the canonical analysis.
Risk estimates impact and test gap; confidence estimates support strength. They are intentionally
independent. `change_groups.py` groups the findings in Python code that calls connect, through
`call_graph.py`: a finding reaches another when its symbol's code calls the other's, directly or
through any code between them, changed or not. Each group is led by an entry point, a finding no
other finding of the group reaches unless it reaches that one back, with every finding it reaches;
entry points that reach each other lead one group together, and a finding two entry points reach is
in both groups. Two findings that only share a caller are not connected, since every symbol of a
program shares `main`. A finding in a module's top-level code that changed a variable's assignment
is reached by the code that reads the variable — in its own module, or through an import of the
variable or of its module, unless that code binds the name itself — and by whatever reaches that
code through calls: it joins the groups of the entry points that reach it, leads none, and its group
names the variables it is reached through. The graph is built for this whenever one Python symbol
has findings and a second symbol, or a module's changed assignments, has some too, with tests to
follow or without. Grouping changes no finding, score, or obligation. The summary's advisory verdict
reads the overall risk against the `verdict` thresholds, and an analysis that left scope unread or
compared a commit with itself never passes.
`baseline.py` compares the findings with those of an earlier analysis, read as untrusted JSON, by
category, path, and symbol, and reports each as new, updated, unchanged, or resolved.

`skeletons.py` serves the CLI's `skeletons` subcommand, beside the pipeline rather than in it. It
reads an earlier analysis as untrusted JSON, validated like a baseline, and the test files of its
head commit through `git_diff/`, and returns a patch adding a failing test for each obligation the
user approved by ID: at the end of the test file named for the changed module, one of the
obligation's candidate tests first, or in a new file beside the obligation's first candidate test,
or in the first test root without one. The analysis's text reaches the generated code only as
escaped comments, a skeleton never takes a name its file already binds, and the module writes
nothing; the user reviews the patch and applies it. As the `generate_test_skeletons` tool it takes
the analysis by its arguments too, from `base_ref` on, and has `service.analyze` run it again
without inference: a deterministic analysis numbers its obligations alike on every run, so the IDs
of a brief name the same obligations in it.

Semantic candidates are immutable and carry their own `(path, symbol)` identity. The three stages that
merge them — rule grouping, model reconciliation, and service deduplication — each return new values,
so no stage can invalidate a key another stage already computed. `service.py` threads a single
`_PipelineState` for omissions, warnings, and truncation, and carries confidence and candidate tests
on a `_ScoredCandidate` rather than in dictionaries keyed separately by identity and position.

Per-category behavior knowledge lives once, in `taxonomy.py`: impact weight, obligation scenario
templates, and candidate-test terminology. Completeness over `BehaviorCategory` is enforced at import
and pinned by a contract test, so extending the taxonomy cannot half-land.

## Coverage grounding

`coverage_map.py` sits between reconciliation and scoring. It is loaded once per analysis, in
`service._load_coverage`, only when a report path is configured, and it never reaches the
filesystem for anything except that one bounded read. Report entries are lookup keys; none of
them is ever opened, so the no-execute invariant is untouched.

The map is immutable once built, so suffix resolution is memoized and the report's candidate paths
are split once rather than per lookup. `status_for` and `counts_for` resolve independently, and
without the cache every finding paid two full `O(report files)` scans.

What it judges is the lines the diff changed, not the symbols around them. Hunks are collected
with `--unified=0`, so their head-side spans are exactly the added or replaced lines;
`service._finding_lines` cuts each finding's evidence spans down to those lines. An evidence span
is a whole symbol — for `<module>`, the whole file — so judging it directly let one uncovered,
unchanged line hide a covered change, and counted lines no diff touched. The summary counts each
changed line once per file however many findings rest on it.

Three hooks consume it:

**Scoring.** `score_risk` takes an optional grounded state and adjusts the existing test-gap
axis only:

```text
test_gap = clamp(static_gap + {uncovered: +10, covered: -15, unknown: 0}, 0, 100)
risk_score = impact*0.35 + critical_path*0.25 + test_gap*0.25 + change_surface*0.15
```

The adjustment is deliberately small. An executed line is real evidence but weaker than a test
that asserts the changed behavior, so coverage nudges the ranking without being able to
override behavioral impact or the critical-path weight. Because the critical-path axis is
scored independently, an uncovered change on a critical path ranks above a covered one on the
same path, and above an uncovered one off it.

**Obligations.** `generate_obligations` receives a per-behavior grounded state and prefers it
over static candidate matching. Obligations merge by normalized Given/When/Then semantics only
between behaviors with the same coverage status, so one obligation can stand for several
behaviors while its status and candidate tests hold for each of them. Their candidates are pooled
and cut to the per-obligation cap, and among equal scores the test files take turns, so one file's
tests cannot fill every place. The obligation that groups high-risk overflow past the global cap
reports its least-tested behavior's status.

**Candidate tests.** A run measured with per-test contexts also says which test executed each line:
the coverage.py data file, `.coverage`, that `coverage_map.load_coverage_data` reads beside the
report, or a small report written with its contexts. `service._executed_tests` asks it which context
labels executed each Python finding's changed lines and the report how many of those lines it
measures, and `coverage_map.ContextTests` names the indexed tests each label stands for, a pytest
node ID by its file's longest shared suffix and its names, coverage.py's dotted name by its longest
dotted suffix, never a tie between files. `map_candidate_tests` makes each such test a candidate for
the finding, at 0.65 when it executed every changed line the report measures and 0.55 when it
executed some, added to its other evidence, and among equal scores puts first the test that ran more
of the change.

The data file is a SQLite database, and untrusted: its bytes are read under their own bound and
deserialized into an in-memory connection hardened before its schema is parsed, so SQLite opens no
path, and only the tables coverage.py writes are queried, each an ordinary table. `docs/security.md`
lists the settings.

`AnalysisResult.coverage` reports what the report actually matched — source format, matched and
unmatched file counts, and changed/covered/uncovered line counts — so a reader can audit the
claim rather than take it on faith.

