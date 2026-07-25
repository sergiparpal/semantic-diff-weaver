# Architecture

The plugin follows a typed, bounded pipeline:

```text
request/config -> repository + resolved refs -> changed committed blobs -> AST deltas
-> deterministic evidence/candidates -> static candidate-test index -> optional structured LLM
-> reconciliation -> risk/confidence -> obligations -> canonical JSON -> optional Markdown
```

`plugin.py` is the only Hermes adapter. Registration closes over `ctx.llm` but performs no Git or LLM
work. `service.py` orchestrates modules without importing Hermes internals. The `git_diff/` package is the sole
subprocess boundary and invokes only Git with argument arrays, resolved commit IDs, timeouts, bounded
captured input/output, disabled external diff/text conversion, and `shell=False`. Tree metadata is
collected once per revision and eligible object IDs are read through bounded `cat-file` batches;
per-file patch hunks retain literal path isolation.

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
meaning. The comparison-operator probe is the exception: it reads the changed expressions alone,
since the synthetic `<module>` and `<unparsed>` names contain angle brackets and would otherwise
force every module-scope or unparsed condition into a boundary or retry finding ahead of the
guard rules. `semantic_interpreter.py` sends bounded, delimited evidence through
`ctx.llm.complete_structured`, locally validates output, rejects fabricated evidence IDs, and cannot
initiate actions. Evidence is batched by module and shared changed dependency calls, prioritized by
configured critical paths, bounded per symbol and per call, and retried once only for explicitly
retryable schema/transport failures within the eight-call ceiling.
A bounded, redacted committed README excerpt may provide repository purpose context. `test_mapper.py`
parses committed tests statically and always labels matches as unverified candidates.

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
