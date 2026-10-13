# Decisions and compatibility record

## MVP fixed decisions

- Configuration is optional and conservative defaults provide first-run output.
- Evidence is batched by module under per-call bounds and an eight-call total ceiling.
- Low-confidence material risk is presented as a review question; below-threshold findings become
  visible omissions/limitations.
- Exact names, Git rename metadata, signatures, normalized fingerprints, and conservative similarity
  support symbol matching without forced ambiguous matches.
- Deterministic fallback is enabled by default.
- Stable behavior fingerprints plus low materiality classify refactors; otherwise the strongest
  supported category or `unknown_semantic_change` is used.
- The handler always returns JSON text, including Markdown-only mode.
- The user's active Hermes provider/model/profile is used without routing overrides.
- The plugin registers one standalone tool and no hooks, commands, skills, or overrides.

## Compatibility snapshot (2026-07-18)

- Local test runtime: CPython 3.12.3 on Linux.
- Local Git: 2.43.0.
- Hermes Agent 0.14.0 was installed in an isolated environment and passed pip-entry-point and
  temporary-home directory discovery, registration, and exact-tool smoke tests.
- Hermes Agent 0.13.0 was tested as the only earlier published release and failed the required
  contract because `PluginContext.register_tool` lacks the `override` parameter. PyPI's release index
  begins at 0.13.0, so 0.14.0 is the lowest verified compatible release.
- The current Hermes Agent 0.18.2 release was also installed and passed the same discovery and
  registration checks. It requires Python `>=3.11,<3.14`; this project remains Python `>=3.11` and
  does not force-install or pin the host runtime.
- `PluginContext.register_tool` accepts `name`, `toolset`, `schema`, `handler`, optional checks/env,
  async/description/emoji fields, and `override=False`.
- `PluginLlm.complete_structured` accepts `instructions`, typed `input`, `json_schema`, `schema_name`,
  request shaping (`temperature`, `max_tokens`, `timeout`, `purpose`), and gated routing overrides.
  This plugin supplies no provider, model, agent, or profile override.
- Directory and pip entry-point discovery remain opt-in; project plugins require
  `HERMES_ENABLE_PROJECT_PLUGINS`.

The CI compatibility matrix installs Hermes 0.14.0 and 0.18.2 with the built plugin wheel, then
repeats both real discovery paths for each version. The regular fake-context suite continues to
validate registration without requiring Hermes or a live model.

## Repository-clone plugin install (2026-07-30)

`hermes plugins install sergiparpal/semantic-diff-weaver --enable` is the documented install path,
matching the sibling plugin repositories. The installer clones the repository into the Hermes
plugins directory and reads `plugin.yaml`; it does not build the project or install its
dependencies. Four consequences are deliberate:

- The root `__init__.py` appends its own directory to `sys.path` before importing the package.
  Hermes loads that file through `spec_from_file_location` and does not put the plugin directory on
  `sys.path`, so without the bootstrap `import semantic_diff_weaver` resolves only when the package
  happens to be pip-installed as well — which a clone install never does. Appending rather than
  inserting keeps an installed copy authoritative when both exist.
- A missing Pydantic or PyYAML raises an `ImportError` naming the dependency and the command that
  fixes it. It is the one failure a clone install can plausibly hit that a wheel install cannot,
  and the alternative is a bare `ModuleNotFoundError` from inside the import chain that reads like
  a defect in the plugin. Any other missing module propagates untouched.
- `plugin.yaml` and `after-install.md` stay out of the wheel. They belong to the clone form; the
  wheel is discovered through the `hermes_agent.plugins` entry point and never reads the manifest.
  Shipping them would put both files at the root of `site-packages`, where every plugin's
  `plugin.yaml` would claim the same path.
- The manifest declares `manifest_version: 1` and an explicit empty `provides_hooks`. `--enable`
  makes the plugin live at install time, so "one tool, no hooks" is worth stating in the file the
  installer reads rather than only in prose.

## Claude Code plugin over MCP (2026-10-05)

The Claude Code plugin is a second transport for the Hermes tool, not a second tool. Claude Code
does not load Python into its own process, so the adapter is an MCP stdio server that serves a
copy of `ANALYZE_SEMANTIC_DIFF_SCHEMA` and delegates to `handle_analyze_semantic_diff`. Five choices
are deliberate:

- **The protocol subset is implemented by hand.** A tool-only server needs `initialize`, `ping`,
  `tools/list`, and `tools/call`. The official SDK would bring an HTTP stack this transport never
  uses into an install that otherwise has two runtime dependencies, so the server has none beyond
  them. It echoes any of the protocol revisions 2024-11-05 through 2025-11-25 and offers the newest
  otherwise; the subset it serves is the same in all of them.
- **The project directory comes from `CLAUDE_PROJECT_DIR`.** Claude Code documents that variable
  but not the server's working directory, so the server enters it rather than relying on where it
  happens to be started. See [security.md](security.md#authorization-under-claude-code).
- **No inference provider.** Claude is already the interpreter of the result. Resolving a provider
  the way the CLI does would turn an `ANTHROPIC_API_KEY` set for Claude Code into a second billed
  model call per analysis without the user asking for one.
- **The brief is the default result, sized for the host.** Claude Code keeps a result inline only
  up to 50,000 characters unless the tool declares a higher threshold, and saves the rest to a
  file. On `389c29e..cd460ef`, the 19-file range that exposed this, Hermes's default `both` was
  158,368 characters, the JSON alone 101,171, and the brief 57,238; Claude spent four calls and
  eleven turns working around the overflow. So the MCP copy of the schema defaults `output_format`
  to `markdown`, as the CLI already does. A brief is returned as plain Markdown, which is smaller
  without JSON escaping. The tool declares `anthropic/maxResultSizeChars: 100000`, twice the
  default and a fifth of the 500,000 ceiling: enough for that brief, not for the JSON of the same
  range, which stays available on request and which the skill steers to narrowed calls.
  A brief that still would not fit is rendered again with shorter evidence excerpts. An earlier
  version of this entry claimed an overflowing brief would land in a file readable in parts; it
  does not. Claude Code saves the result as JSON, the whole brief on one line, and a model can then
  reach it only through shell tools that need approval: asked for the top obligation IDs of
  `v0.3.0..main`, a 124,551-byte brief, Claude spent fifteen turns working around it. The server
  now requests the analysis beside the brief and, over a 95,000-byte budget, re-renders it with
  each excerpt cut to 400, 200, 120, 80, 40, or 0 characters, the first that fits; that range fits
  at 80. Findings, obligations, and every ID are kept, the brief says excerpts were shortened, and
  the brief now carries finding and obligation IDs, so the question no longer needs the JSON. The
  skill narrows with `include` only, because `exclude` also removes files from candidate-test
  discovery — `tests/integration/test_candidate_mapping.py` pins that asymmetry.
- **`uvx --from ${CLAUDE_PLUGIN_ROOT}` launches it.** Installing a Claude Code plugin copies the
  repository and installs nothing, the same constraint as the Hermes clone install. `uvx` builds
  the checkout into an isolated, cached environment, so the plugin works without a prior
  `pip install` and never touches the user's Python. It does not write into the plugin directory,
  unlike `uv run --project`, which would create a lockfile and virtual environment there. uv
  reuses a local package's cached build until a cache key changes, and its default key is
  `pyproject.toml` alone, so a working copy installed through a local marketplace kept serving the
  code it was first built from. `[tool.uv] cache-keys` adds the package's Python sources.

The server is declared inline in `.claude-plugin/plugin.json`, not in a root `.mcp.json`. Claude Code
reads a root `.mcp.json` as the repository's own project MCP configuration too, where
`${CLAUDE_PLUGIN_ROOT}` is undefined, so anyone opening the checkout in Claude Code got a broken
second server waiting for approval.

`.claude-plugin/plugin.json` carries the package version and is kept in step with it by
`tests/contract/test_claude_code_plugin.py`.

## Output-affecting corrections (2026-10-13, test files)

Every changed Python file was analyzed, so test code was reviewed as if it were the behavior under
test. On `distiller` (`main..skeleton`, a branch adding a command-line skeleton and its tests), 17
of 28 findings were test functions, helpers, and their nested fakes, each an
`unknown_semantic_change` at 49/100 with no candidate test, so the tests alone set the overall
risk. With the project's coverage report they were also the only findings it could not ground —
its `source = src` measures no test file — which kept the obligation from a grounded verdict and
produced a warning blaming the report's paths. This repository had worked around it in its own
`.semantic-diff-weaver.yaml`.

A changed file is now left out as `test_file` when it is test code. A path settles that for
anything under a `test_roots` entry and for `conftest.py`, and those files are left out before the
collection budgets. A name such as `test_*.py` or `*_test.py` does not: a first version reused
candidate discovery's naming rule, and on this repository's `v0.3.0..main` it hid
`semantic_diff_weaver/test_mapper.py`, which is production code. Such a file is therefore test
code only when candidate discovery's parser finds a test in it, read at the head revision, or the
base one for a deletion; it counts toward the budgets, because it has to be read to be told apart.
`rules.analyze_test_files: true` restores the previous scope.

Measured: `distiller` `main..skeleton` went from 28 findings to 11, the 17 test findings gone. Its
overall risk stays 49 without a coverage report and falls from 49 to 45 with one, whose obligation
is now `covered_by_existing_tests`. `meal-manager` `8894749` went from 108 findings to 91 — its two
root-level test modules, with 17 findings, three of them `high` — at an unchanged overall 62. This
repository's `v0.3.0..main` is unchanged except that 24 files move from `path_filter` to
`test_file`, and so is the 17-case corpus, which changes no test file. `meal-manager`
`v0.2.0..HEAD` still exceeds the default line budget: its 4,068-line `test_unit.py` sits at the
repository root, outside any test root, so it counts until read.

## Output-affecting corrections (2026-10-05, prose in string literals)

The keyword rules read a delta's display text, which keeps string literals verbatim, so the words of
a message counted as code. `WeaverError(..., 'Retry with a narrower diff ...')` was reported as a
`retry_timeout_change`, and a test whose strings quoted `def run(limit=2)` became a retry boundary.

Filtering the display text cannot fix this: it is redacted and cut per value, so literals arrive
unterminated — a call argument stops at 100 characters — and redaction can consume a closing quote,
leaving no reliable boundary between code and string. The probe is computed from the AST at
extraction instead, for the three features the keyword rules read (comparisons, conditions, calls):
identifiers, attribute and keyword names, lambda parameters, and string literals without whitespace.
A literal containing whitespace is prose and contributes nothing; each f-string part is judged on its
own. Literals without whitespace stay because they are often the signal — `headers.get('Authorization')`
and `config.get('retry_limit')` keep their categories.

Measured on this repository's history — every commit against its first parent, 66 analyzable
commits and 1,909 evidence classifications — 10 classifications changed, and in each one every
keyword the old category rested on was inside a prose literal: error messages, obligation scenario
texts, and source code quoted in test strings. Six moved from `validation_change` to
`dependency_interaction_change`, two from `retry_timeout_change` to `dependency_interaction_change`,
and two from `retry_timeout_change` to `boundary_change`. The 17-case corpus is unchanged.

A first version let the probe reach further than the display text, which stops at 1,500 characters
per side. Being denser, it covered calls the text never showed, and changed six more
classifications by finding new keywords there — `service.analyze` became an `authorization_change`
because of a late `RepositoryIdentity(...)` call. That was a new false positive, not a fix, so a probe
now covers exactly the values its display text shows.

One limit remains: a one-word literal still counts, so `log("Retry")` reads as a retry. The
condition rule's comparison check had the same flaw as the keyword rules; it is fixed in the next
section.

## Output-affecting corrections (2026-10-05, tests named after their module)

Candidate-test mapping keyed on the changed symbol, static imports, and mirrored file names. Code
with one module per tool defeats all three: every `src/handlers/*.py` in `meal-manager` exports the
same `HANDLER`, the tests load each one with `importlib`, and they all live in one
`test_integration.py`. Its six `register_cooked_meal` tests were never proposed for a change to
that handler, while the generic token `handler` proposed tests of the `tool_handler` decorator.

What those tests share with the module is its name — `test_register_cooked_meal_rollback` calls
`register_cooked_meal(...)` — so a test whose name spells a changed file's stem as whole words now
scores +0.25, as much as a mirrored file name, and +0.10 more when its body names the module too.
Only multi-word, public stems count: a single word such as `store` turns up in tests about
anything, and `_common` or `__init__` are never what a test is named after. A body mention never
counts on its own. A first version let it, and on this repository's history it moved 167
candidate lists — tests here write `mcp_server.X` in tests of anything, and with the existing
package-import signal that mention alone crossed the threshold, displacing relevant tests.

Measured on `meal-manager` (`v0.2.0..HEAD` and `8894749`, production code): 25 of 47 obligations
went from no candidate to some, 127 candidates were added — every one named after a module its
obligation covers — and the 8 displaced were the generic `tool_handler` and 0.35 matches. Finding
a candidate lowers the test-gap axis from 90 to 65, so 32 risk scores fell by 6 or 7 points, and
2 findings moved from `high` to `medium`. This repository's history and the 17-case corpus are
unchanged.

## Output-affecting corrections (2026-10-05, coverage over changed lines)

Coverage grounding promised "unanimity across the changed lines a finding rests on" and judged
something else: each finding's evidence span, which is its whole symbol — for `<module>`, the whole
file. The summary then summed those spans, so a function's lines were counted again inside its
module's. Analyzing `069ce64..6e89d08` with a current report claimed 411 changed lines in a
366-line file whose diff added 46, of which 185 were covered and 5 not; and the `<module>` finding
got no verdict at all, because the file as a whole mixed covered and uncovered lines.

A finding is now judged on its evidence spans cut down to the head-side lines the diff changed,
read from the `--unified=0` hunks, and the summary counts each changed line once per file. The same
range now reports 46 changed lines, 13 covered and 0 uncovered — of the other 33, 25 continue a
multi-line statement, 6 are comments and 2 are blank, none of which coverage.py records as a line
of its own — and every finding, `<module>` included, is
`covered_by_existing_tests`. A finding with no changed head-side line, such as a pure deletion, is
`unknown`, as an unmatched file always was. Without a coverage report nothing changes, and the
17-case corpus has none.

## Output-affecting corrections (2026-10-05, condition comparisons)

The condition rule calls a changed condition a boundary — or a retry boundary — when it contains a
comparison, and decided that by searching the display text for `<`, `>`, `==`, or `!=`. Text cannot
answer the question, and it went wrong three ways: an operator inside a string literal counted
(`'<' in text`, or test code quoted in strings), a shift counted (`mask & (1 << bit)`), and an
equality that redaction had consumed did not — `token == expected` is displayed as
`token =[REDACTED] expected`, so the very comparisons that guard credentials were missed.

The probe now records whether each value contains an ordering or equality comparison, read from its
AST operators (`Lt`, `LtE`, `Gt`, `GtE`, `Eq`, `NotEq`). Membership and identity tests (`in`, `is`)
do not count, as they never did. The probe already covers exactly the values the display text
shows, so a comparison in a condition the text never reached does not count either. Deltas built
without a probe keep the old text scan.

On this repository's history — 70 commits, 1,989 evidence classifications — one classification
changed: a test whose conditions are membership checks over quoted source code, `'return x < 5'`
among it, moved from `boundary_change` to `unknown_semantic_change`. The history holds no shift or
redacted equality in a changed condition; unit tests pin both. The 17-case corpus is unchanged.

## Output-affecting corrections (2026-07-25)

These change what an existing repository is reported as, so they are recorded here rather than only
in the changelog.

- Conditions are classified by their changed expressions, never by the enclosing symbol name. The
  synthetic `<module>` and `<unparsed>` names were read as containing `<` and `>` comparison
  operators, forcing every module-scope or unparsed condition to `boundary_change` or
  `retry_timeout_change` ahead of the authorization, validation, and retry-guard rules. A
  module-scope authorization guard now reports impact 92 instead of 64, with the obligation
  scenarios and candidate-test terminology that follow from the corrected category.
- PEP 695 type parameters are part of the signature contract for both callables and classes.
  `def f[T]` to `def f[T, U]`, or an added bound such as `[T: int]`, previously fingerprinted
  identically, was labelled `structural_refactor`, and was then dropped under the default
  `emit_low_risk_refactors: false`. Such changes are now `signature_change`.
- A scalar `language:` section is a `configuration_error` with remediation text instead of an
  `AttributeError` surfacing as an opaque `internal_error`.
- The AST analysis deadline bounds matching and comparison as well as parsing, and is reached
  inclusively, so a spent or zero budget stops work on platforms with a coarse monotonic clock.
- The reviewed evaluation goldens required no regeneration: no corpus case contains a module-scope
  condition or PEP 695 syntax. Schema version, taxonomy values, error codes, and evidence IDs are
  unchanged.

## Output-affecting corrections (2026-07-25, statement ordering)

Ordering is now defined as a permutation. This removes findings from existing reports, so it is
recorded here alongside the corrections above.

- **What `statement_order` was comparing, and why it was wrong.** `ast_diff/extract.py` builds
  `statement_order` entries that embed unparsed expression *content*, not only sequence identity:
  `if:<test>`, `raise:<exc>`, `return:<value>`, and `assign:<targets>` all carry the expression
  text, and only `call:<name>` is content-free. `compare.py` then treated any inequality of that
  tuple as an ordering signal, so a pure content edit synthesized a phantom reordering. Adding a
  keyword argument to `return client.fetch(x)` moved nothing, yet reported `ordering_change`.
- **Ordering is now a permutation**, tested by `compare.is_reordering`, mirroring the call-order
  test `_append_call_delta` has always applied: the delta is emitted only when the two tuples are
  unequal *and* sort to the same multiset. The `ORDER_SENSITIVE_KINDS` guard is unchanged, so
  `call_order_change` still suppresses the fallback rather than double-reporting the same move.
  `extract.py` is unchanged — the embedded content is read as a prefix by `match.py` and is useful
  as evidence text.
- **Findings removed, and why none were genuine.** Two corpus cases lost their `ordering_change`.
  The `dependency arguments` case added only a keyword argument, which was a false positive
  outright. The `side effect` case inserted `notify(user)` ahead of a return; an insertion changes
  the multiset rather than permuting it, and the inserted call is already reported as
  `call_change`, which classifies as `side_effect_change` — so the ordering finding was redundant,
  not lost information. A genuine swap (`f(); g()` to `g(); f()`) is still reported, via
  `call_order_change`, and swapped sibling assignments are still reported via `assignment_change`.
- **Before and after, measured on the 17-case corpus.** Material precision rose from 88.24%
  (15 of 17 predicted categories) to 100.00% (15 of 15). Recall is unchanged at 100% (15 of 15),
  as are 100% evidence-anchor correctness, 100% obligation-concept match, and zero fabricated
  evidence references. There are now no surplus predictions in the corpus at all.
- `tests/fixtures/golden/canonical_outputs.json` was regenerated for exactly those two cases and
  reviewed field by field; the other fifteen are byte-identical, and both affected cases retain
  their genuine finding, its risk, its confidence, and their unchanged `risk_score`. The expected
  labels in `evaluation_expected.json` were not touched. Schema version, taxonomy values, error
  codes, and evidence IDs are unchanged.

## Output-affecting corrections (2026-07-26)

Review fixes. `SCHEMA_VERSION` stays `"1.1"`; the only new value any consumer can observe is one
additional omission reason.

- **A module-level type comment no longer makes a file unparseable.** `extract.py` parsed with
  `type_comments=True`, which accepts a strictly *narrower* grammar than the default: a stray
  `# type:` comment outside a function is a `SyntaxError` under the flag and valid Python without
  it. Such a file was reported as `parse_incomplete` and emitted a fabricated
  `unknown_semantic_change` finding at 0.68 baseline confidence — a claim about source that Python
  itself accepts. `parse_module` now falls back to a plain parse, so type comments enrich the
  rendered signature when they parse and never decide parseability. `test_mapper.py` never read
  one and no longer asks for them.
- **Oversized symbols are reported under their own reason.** Omitted LLM batches and symbols too
  large for any single call were summed into one counter published as `llm_batch_limit`, reporting
  a symbol count under a reason named for batches. A symbol whose payload alone exceeds
  `max_model_input_chars_per_call` now carries `model_input_symbol_limit`, with its own warning.
  This is the output difference: a consumer switching on `scope.omitted[].reason` sees a value it
  has not seen before, and a previously inflated `llm_batch_limit` count drops to the batches it
  actually names.
- **A module that still carries a precise delta keeps its changed-symbol count.**
  `_drop_redundant_module_deltas` filtered the changed-symbol key set by *path*, so a file with any
  detailed delta lost its `<module>` key even though only `symbol_added`, `symbol_removed`, and
  `structural_refactor` are dropped — a surviving module-scope condition change was therefore
  missing from `summary.changed_symbols`. The key set is now filtered by what actually survived.
- The reviewed goldens required no regeneration. No corpus case carries a module-level type
  comment, exceeds the model-input symbol bound, or mixes a surviving module-scope delta with a
  detailed one in the same file, so `tests/fixtures/golden/canonical_outputs.json` is
  byte-identical and `docs/evaluation.md` carries its corpus figures forward. Schema version,
  taxonomy values, error codes, and evidence IDs are unchanged.

## Anthropic request shaping is per model family (2026-07-26)

The optional provider adapter shapes each request to what the named model accepts, matching model
families by prefix so dated snapshots and aliases behave identically:

- families that removed `temperature`/`top_p`/`top_k` are sent none of them; the others get
  `temperature` through `extra_body`, because SDK 1.0 dropped it from the `messages.create`
  signature and the keyword now raises `TypeError` client-side — the same silent collapse into
  deterministic fallback described below;
- families whose thinking is always on are sent no `thinking` parameter at all, because an explicit
  `{"type": "disabled"}` returns HTTP 400 at any effort level. Everywhere the parameter is honored
  it is still sent as disabled, so the interpreter's small `max_tokens` budget bounds response text
  rather than being consumed by thinking before the JSON is emitted.

This is recorded because the failure mode it fixes is close to invisible. Sending a parameter the
family rejects fails *every* call; each failure maps to `ProviderUnavailable` and surfaces only as
the generic "One structured LLM batch failed" warning, and the analysis then collapses into
deterministic fallback — a supported, non-erroring mode. Nothing in the output says the request
shape was at fault. A model override (`--model`, `SEMANTIC_DIFF_WEAVER_MODEL`) therefore has to be
shaped as deliberately as the default, `claude-opus-5`.

## Coverage-artifact grounding (2026-07-25)

### Schema version bumped to 1.1

`CoverageStatus` gains two members, `covered_by_existing_tests` and `changed_lines_uncovered`,
and `AnalysisResult` gains an optional `coverage` object. Every existing member, value, error
code, taxonomy value, and evidence ID is unchanged, and `coverage` is `null` when no report is
supplied — so the addition is backward-compatible in the loose sense.

It is still a bump, because a consumer that exhaustively matches on `coverage_status` now sees
values it has never seen, and would do so with no version signal to key on. `SCHEMA_VERSION` is
therefore `"1.1"`, the `Literal["1.0"]` annotations on `AnalysisResult`, `MarkdownEnvelope`, and
`BothEnvelope` moved with it, and `tests/contract/` plus the canonical goldens were updated
intentionally. The golden diff is exactly 17 `schema_version` lines and 17 additive
`"coverage": null` lines; no finding, risk, confidence, or obligation changed.

`ErrorCode.COVERAGE_UNREADABLE` (`coverage_unreadable`) is new and additive. `coverage_report`
is a new optional tool argument and CLI flag.

### Cobertura and JaCoCo XML are excluded on purpose

Only coverage.py's native JSON and lcov `.info` are supported. Both parse with the standard
library, and neither carries an entity-expansion surface.

Supporting Cobertura or JaCoCo would mean parsing untrusted XML, which leaves two options and
no third: add a `defusedxml` dependency, or accept a billion-laughs surface. Adding a
dependency to the base install to read an optional artifact is a poor trade for a tool whose
selling point is safety when pointed at a hostile repository, and accepting the surface is
simply the thing this project exists not to do. `tests/security/test_coverage_inputs.py`
pins the outcome: an entity-expansion payload is refused as unreadable input, not expanded.

This is recorded so the exclusion is not silently revisited. If XML support is ever wanted, the
decision to revisit is the dependency question, not the parser.

### What a coverage claim is allowed to mean

- A changed file absent from the report is `unknown`, never `uncovered`. The opposite choice
  would make a misconfigured CI path prefix indistinguishable from a repository with no tests,
  which is the single most damaging thing this feature could get wrong. Unmatched files are
  counted in the output and named in a warning.
- A grounded verdict requires unanimity across the changed lines a finding rests on, and a
  merged obligation keeps one only when every behavior it stands for agrees.
- `CandidateTest.verified` stays `Literal[False]`. Coverage says a *line* was executed by the
  suite, not that a specific candidate test asserts the changed behavior. The README's
  "does not claim runtime coverage" wording was revised precisely rather than deleted.
- Scoring is nudged, not driven: the grounded state adjusts the existing test-gap axis by +10
  when uncovered and −15 when covered, leaving behavioral impact and critical-path weight
  untouched. See `docs/architecture.md`.

The no-execute invariant is unchanged. The tool ingests a report the user's own CI already
produced, as untrusted input data, and runs nothing to obtain one. Report entries are lookup
keys; none is ever opened or resolved against the filesystem.

## GitHub Action output format (2026-07-25)

### No SARIF, and no check annotations

The action posts one Markdown pull-request comment and nothing else. This is recorded so the
choice is not silently revisited.

SARIF's model is a flat list of results, each with a `ruleId`, a `level`, and a location. This
tool's whole output shape is the opposite: `scoring.py` produces a risk score and a confidence
score *separately and deliberately*, and `taxonomy.py` exists to keep behavior category,
observable impact, and obligation type distinct from both. Flattening that into a single
`level` per result would discard the distinction the analysis is built to express — a
high-risk/low-confidence finding is presented as a review *question*, not as an error, and
SARIF has nowhere to put that difference. Check annotations have the same problem plus a
narrower one: they anchor to a line, and many findings here are about a symbol's contract
rather than a line.

The Markdown brief from `renderer.py` already carries the obligations, review questions,
evidence anchors, and stated limitations, in the form they were designed to be read in.

### The comment is upserted, never appended

Idempotency is keyed on a hidden `<!-- semantic-diff-weaver:v1 -->` marker as the comment's
first line, not on comment authorship or position. A re-run edits. The marker is versioned so a
future format change can migrate rather than collide.

### The action is composite, not Docker

No image to publish or keep patched, faster cold start, and the dependency surface stays
readable in `action.yml`. The action contains no analysis logic; it wraps the Stage 3 CLI.

## Release note

The repository is licensed under MIT. Publishing or pushing remains a separately authorized action.

## Evaluation label review (2026-07-17)

The retry-predicate fixture originally listed `state_transition_change` even though its increment
statement was identical at both revisions. The expected label was removed after the required separate
fixture review so evaluation does not reward an unsupported state-change finding.

## Plan-conformance closure (2026-07-19)

- Callable signatures now include return annotations and type comments; decorator evidence retains
  safe names only.
- Symbol inventory distinguishes methods and async methods, always records a module snapshot, and
  preserves overload-style duplicate qualified names.
- Matching now performs a conservative second pass across changed files, while ambiguous near-ties
  remain separate findings with explicit warnings and reduced confidence.
- Equivalent obligations merge by normalized Given/When/Then semantics, union behavior links and
  candidate tests, preserve the strongest priority/confidence, and retain the global candidate cap.
- LLM batching connects same-module evidence and cross-module changes sharing a changed dependency
  call before applying input and call ceilings.
- Candidate-test discovery now has aggregate file and byte safety caps, and the full deterministic
  performance regression ceiling is five seconds as required by the project performance
  specification.
  Performance fixtures pause coverage tracing while timed application code runs, so the ceiling
  consistently measures production execution rather than instrumentation overhead across systems.
- Committed tree metadata and source blobs are collected in bounded Git plumbing batches instead of
  spawning size/read/mode processes per file; literal pathspecs and disabled lazy fetching preserve
  the repository boundary while meeting the cross-platform performance target.
- All 17 evaluation cases now have reviewed, normalized canonical JSON goldens in addition to their
  machine-readable category/evidence/scenario labels.
