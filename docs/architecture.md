# Architecture

The plugin follows a typed, bounded pipeline:

```text
request/config -> repository + resolved refs -> changed committed blobs -> AST deltas
-> deterministic evidence/candidates -> static candidate-test index -> optional structured LLM
-> reconciliation -> optional ingested coverage report -> risk/confidence -> obligations
-> canonical JSON -> optional Markdown
```

`plugin.py` is the only Hermes adapter. Registration closes over `ctx.llm` but performs no Git or LLM
work. `mcp_server.py` serves the same tool and handler to Claude Code over MCP stdio, always in
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
including return annotations and PEP 695 type parameters on both callables and classes; retains only
decorator names; preserves overload-like duplicate definitions; and correlates conservative moves
across files that both remain. The `AstBudget` wall-clock deadline is checked between the parse,
matching, and comparison phases and counts as spent once reached, so a coarse platform clock cannot
let an exhausted budget read as live.

Deterministic rules create evidence before model use. Their term-based probes read the changed
expressions together with the symbol name, because names carry authorization, validation, and retry
meaning. For comparisons, conditions, and calls they read a probe built from the AST at extraction
rather than the display text: identifiers, attribute and keyword names, and string literals without
whitespace (keys, headers, permission names), but not literals containing whitespace, whose words are
prose about the code. A probe covers exactly the values the delta's bounded display text shows. The
condition rule's boundary check reads the same probe: whether a value contains an ordering or
equality comparison (`<`, `<=`, `>`, `>=`, `==`, `!=`), taken from its operators and never from
characters in its text, where a literal's `'<'`, a shift, or a synthetic `<module>` name would count
and an `==` consumed by redaction would not. `semantic_interpreter.py` sends bounded, delimited evidence through
`ctx.llm.complete_structured`, locally validates output, rejects fabricated evidence IDs, and cannot
initiate actions. Evidence is batched by module and shared changed dependency calls, prioritized by
configured critical paths, bounded per symbol and per call, and retried once only for explicitly
retryable schema/transport failures within the eight-call ceiling. What each bound drops is reported
under its own omission reason, because the three count different units: `llm_batch_limit` for
batches the call ceiling could not accommodate or that retries left unvisited within it,
`model_input_symbol_limit` for a single symbol whose payload cannot fit one call, and
`model_evidence_limit` for a symbol whose evidence was truncated to fit.
A bounded, redacted committed README excerpt may provide repository purpose context. `test_mapper.py`
parses committed tests statically and always labels matches as unverified candidates. Tests that
match only through evidence about their file — a mapping, a mirrored name, an import, a run, or a
package import — cannot be told apart by it, so several of them stand as one candidate for the
file, with the symbol `<module>`. `call_graph.py` reads the head revision of the modules tests
import and of the changed ones, then follows their imports, under file, byte, and time bounds and
the AST stage's parse budgets. For each symbol it keeps the names and attribute chains its code
uses, and for each module what its imports bind, so `messages.unexpected_failure` resolves to a
symbol of another module, through re-exporting packages too. From what each test names, with the
same-file helpers it calls, and the modules it imports or runs, the mapper follows those uses
across modules: a test reaches whatever it names, whatever that uses in turn, and the top-level
code of every module loading brings in. Reaching a symbol from a module the test imports counts
more than reaching it only through another module, which never makes a strong candidate on its
own. A private, undecorated helper that no test reaches or names gets no candidate from a test
file, since only its module calls it, unless the graph was cut short by its bounds.

When global file or line limits are exceeded, explicitly configured critical paths are analyzed first
and every omitted count/reason is carried into canonical scope and Markdown. Parse-incomplete files
produce bounded unknown findings rather than losing all evidence. Conservative symbol matching can
connect renamed or moved functions, including moves between two existing files, but refuses ambiguous
near-ties and lowers confidence for the unmatched lifecycle findings.

The canonical schema version is `1.0`. Markdown is derived entirely from the canonical analysis.
Risk estimates impact and test gap; confidence estimates support strength. They are intentionally
independent.

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

Two hooks consume it:

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
behaviors while its status and candidate tests hold for each of them. The obligation that groups
high-risk overflow past the global cap reports its least-tested behavior's status.

`AnalysisResult.coverage` reports what the report actually matched — source format, matched and
unmatched file counts, and changed/covered/uncovered line counts — so a reader can audit the
claim rather than take it on faith.

