# Changelog

## Unreleased

- **Feature:** a Claude Code plugin. `/plugin marketplace add <path-to-checkout>` then
  `/plugin install semantic-diff-weaver@semantic-diff-weaver` adds an MCP server exposing the
  same `analyze_semantic_diff` tool and schema as the Hermes plugin, plus a `semantic-diff` skill.
  The server is a new dependency-free `semantic-diff-weaver-mcp` console script
  (`semantic_diff_weaver/mcp_server.py`) speaking MCP over stdio; the plugin launches it with
  `uvx`. Under it the authorized root is the Claude Code project directory, an operator-set
  `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` still wins, and the analysis is always deterministic. Its
  default result is the Markdown brief as plain text, with a declared 100,000-character result
  threshold, because Claude Code moves longer results out of the conversation into a file. See
  `docs/decisions.md` and `docs/security.md`.

- **Fix:** tests that reach a change alike are ordered by what they are about. Among candidates
  with equal scores and no test naming the symbol in its body, the order fell to paths and
  names, and the five kept could be any of dozens of integration tests. A test whose name or file
  shares more words with the changed symbol's class, function, or module now comes first, and
  category words only break what is left: `meal-manager`'s `_sweep_stale_temps` is led by
  `test_atomic_write_sweeps_stale_temps`, `_merge_expiry` by
  `test_merge_ingredient_alias_keeps_expiry`, and `DataDirLock`'s methods by
  `test_data_lock_configure_rejects_while_held`, each in place of an unrelated test. Only the
  order changes: no score, reason, or risk score moves.

- **Fix:** category terminology no longer ranks a test that reaches a change above one that
  calls it. A test's words matching the change's category added 0.10 to any test that imports
  the module and reaches the symbol, so `meal-manager`'s tests reaching `atomic_write_json`
  through a repository's `save`, whose words were about errors, pushed the tests that call it out
  of its error-handling list. The words now count for a test named after the symbol or its
  module, one that calls the symbol itself — naming it in a module it imports, through a helper
  too, or calling it on an instance of a class it makes or reads — or one only its file ties to
  the change, and no longer for a test that reaches the symbol through other calls. A mention in
  the test's text is no call: a message saying "resolve" does not call `CallGraph._resolve`.
  Candidate order changes on `meal-manager` (18 of 91 lists) and this repository (63 of 157), and
  no risk score moves; `distiller` is unchanged.

- **Fix:** a package's `__init__.py` is named by its package. Candidate mapping named it
  `shop.__init__`, so no test importing `shop` counted as importing it, a `test_init.py` mirrored
  every `__init__.py`, and its module never named a test. It is now named like `shop.py` for each
  of those, and the repository's own `__init__.py` is the root package. A test imports a package's
  `__init__.py` when the call graph locates one of its imports there — the package itself, or a
  name it binds — and not when it imports one of the package's modules, which runs the
  `__init__.py` as loading any module runs what it imports. On `meal-manager`, the test that loads
  the plugin package becomes a strong candidate for its `register`, `atomic_write_json`'s own test
  becomes a strong one, and the top-level code of `src`, `src/handlers`, and `src/repositories`
  gains the test files that load them: four risk scores fall, the overall from 57 to 56. One risk
  score falls on this repository, `ast_diff/__init__.py`'s, and `distiller` is unchanged. A change
  to the root `__init__.py` no longer fails the analysis, which an earlier draft of this change did.

- **Feature:** a test that loads the package it lives in is related to it. `meal-manager`'s tests
  load the plugin itself as `importlib.import_module(_PLUGIN_DIR.name)`, where
  `_PLUGIN_DIR = Path(__file__).resolve().parent`, and call `_pkg.register(...)`; the name is
  known only at run time, so the load was dropped. The index now reads the name of a test file's
  own directory — through `Path(__file__)`, `.resolve()`, `.parent`, `.name`,
  `os.path.dirname`, `os.path.basename`, module-level variables holding those, or
  `__package__` — as the test's own package, which for a test at the repository's root is the
  root package the call graph names `""`, and a name starting with a dot, `.register`, is
  relative to it. Taking `meal-manager`'s `register` as changed, `test_register_wires_every_tool`
  becomes its candidate where it had none; the measured ranges are unchanged, since that test
  reached the same code through the modules it also loads.

- **Fix:** a test holds an instance it makes or reads, not one a module makes when it loads. A test
  that reaches a class may call methods on an instance of it, but reaching a class counted loading
  a module whose top-level code makes one: every test importing `meal-manager`'s `dii` held its
  session store, and `.get` on any dictionary reached `IngredientSessionStore.get`. A test now
  holds an instance of a class it reaches through calls without loading a module, or of a
  module-level variable it reads, as `_dii_mod._store` does. The index also records the test's
  attribute chains and what the file's `import` statements bind, so
  `_repos_mod.JsonFridgeRepository(...)` is the class the module re-exports. On `meal-manager`,
  19 of 91 candidate lists swap 44 candidates for tests that use what changed, and no risk score
  moves; this repository and `distiller` are unchanged.

- **Feature:** a test that loads a module with `importlib` is related to it. `meal-manager`'s tests
  load everything that way, `importlib.import_module(".src.dish", _PLUGIN_DIR.name)` and a
  `_load_handler("add_dish")` helper that formats the name, so no test related to any of its
  modules. The test index now reads `importlib.import_module` calls whose name the file writes
  out — a literal, a module-level constant, or an f-string or concatenation of those, including a
  same-file helper's argument at each call — resolving a relative name against the package given,
  or the test file's own when that is known only at run time, and records what each name the file
  assigns from one stands for: `add_dish = _load_handler("add_dish")` is
  `src.handlers.add_dish.HANDLER`. An import inside a test function, of either kind, now belongs to
  that test alone. On `meal-manager`'s `8894749`, 60 of 91 findings gain their first candidate
  tests and 83 risk scores fall, the overall from 62 to 57; this repository changes 2 candidate
  lists, through the imports inside tests, and `distiller` none.

- **Fix:** an attribute a method reads on `self` is a member of its own class. A class's members
  know whatever any of them knows, so once `meal-manager`'s repositories kept the shared
  `DataDirLock` as `self.lock`, every `self.path` they read, their own data file, reached the
  lock's `path` property: 115 symbols reached it instead of 34. Module references now record the
  attributes a method reads on its own instance or class apart from the others, and the call graph
  looks them up only on that class and the classes it inherits from, still following what they
  return; `self.lock.path` reads the lock's. On `meal-manager` the graph loses those 19 edges and
  gains none, and `DataDirLock.path` is reached from 46 symbols; this repository's graph is
  unchanged, and so are candidate tests everywhere.

- **Fix:** a `src` directory that is itself a package keeps its name in the call graph. Module
  names drop a leading `src` or `lib`, as a `src` layout needs: `src/distiller/cli.py` is
  `distiller.cli`. But `meal-manager`'s `src` has an `__init__.py` and is imported as a package, so
  `from .. import data_lock` in `src/repositories/__init__.py` climbed to a package named `""`,
  the name of the plugin's own `__init__.py`, and never reached the lock `src/__init__.py`
  re-exports. A `src` or `lib` with an `__init__.py` now keeps its name, and its modules are still
  found without it, after every other name. On `meal-manager` the graph gains 40 edges and loses
  none, and `DataDirLock.configure` is reached from `register` and the repositories' `configure`;
  candidate tests are unchanged on every measured range.

- **Feature:** a call on an instance another module holds is followed into its class.
  `meal-manager`'s handlers call `dish_repo.load()` on the `JsonDishRepository` that
  `repositories/__init__.py` makes on import and they import, and the call graph stopped there:
  a module-level variable was no symbol, so neither the import nor the call resolved. Module
  references now record what each module-level variable is assigned — the class a call makes, the
  function it calls, another variable, a container's items — and its annotation, and a symbol
  knows the classes a variable it uses holds, its own module's or one it imports, through
  re-exporting packages and chains such as `stores.pantry.load()` too. This replaces the rule that
  every symbol of a module knows what the module's top-level code makes, and an attribute no longer
  resolves to a name an import binds. On `meal-manager`'s head, methods reached by some symbol go
  from 69 to 96 of 105; its candidate tests are unchanged, since they load modules with
  `importlib`. `distiller` and this repository have no such instances and are unchanged.

- **Feature:** a call on an instance is followed into its class's module. `cart = Cart();
  cart.total()` stopped at `cart.total`, since `cart` is a local variable, so a method of a class
  from another module was reached only by tests that imported its module and named the method.
  The call graph now records which symbols are classes, their bases, and each function's return
  annotation. A symbol knows the classes it makes, annotates with, or inherits from, those the
  functions it calls are annotated to return, those their fields hold, what any member of its
  class knows (`self.cart = Cart()` in `__init__`), and what its module's top-level code makes,
  and an attribute it uses reaches the method of that name on those classes and the bases they
  inherit from. A subclass's override is reached only where the subclass is known. A test that
  reaches a class and calls a method on it reaches the method too, and a test now starts from what
  it imports through a re-exporting package, such as `from shop import Cart`, for which import
  names keep their case. On this repository's `v0.3.0..main`, 14 candidate lists change and
  `StructuralDelta` falls five points; taking each of its 94 methods as changed, 70 have candidates
  instead of 68. `distiller` and `meal-manager` are unchanged.

- **Fix:** a test that calls a changed symbol no longer ranks below the tests that reach it
  through other calls. A mention of the symbol in a test's body and a reach through its calls
  were meant as one piece of evidence, never added together, but a reach from a module the test
  imports had come to count 0.15 against the mention's 0.10, and a test that calls the symbol
  kept its mention; a reach through another module, meanwhile, added to it. The reach now
  replaces the mention wherever both hold, and among equal scores a test whose body names the
  symbol ranks first, so `test_line_sets_are_judged_like_ranges` leads
  `CoverageMap.status_for_lines` instead of missing its five candidates. A test named after a
  symbol, importing its module, and calling it now scores 0.60 instead of 0.55, a strong
  candidate: `distiller`'s `RunArgs` falls four points, and 15 risk scores on this repository's
  `v0.3.0..main` fall four or five. The test that runs `python -m distiller` no longer leads
  `evaluate.run`, as the mention it scored was `subprocess.run`.

- **Feature:** a test is followed into modules it never imports. Candidate mapping stopped at
  the module a test imported, so a test of `cli.main` did not count for `messages`, which `main`
  calls, and a helper changed beneath an unchanged module counted no test that reaches it through
  that module. The new `call_graph.py` reads the modules tests import and the changed ones at
  head, follows their imports under file, byte, and time bounds, and resolves each attribute chain
  through the module's imports and re-exporting packages, so `messages.unexpected_failure` is that
  function and `store.get` on some object is not `cache.get`. A test that reaches a changed symbol
  only through another module is a candidate, "changed symbol reached through another module's
  calls", but never a strong one on its own, since static reach through an orchestrator such as
  `service.analyze` takes every branch; a package import and category terminology no longer add
  to it. Reaching a symbol from a module the test imports now counts 0.15 instead of 0.10, so such
  a test ranks above one that reaches it only from afar. A graph cut short by its bounds rules no
  helper untested. On `distiller`, `unexpected_failure` gains the tests that reach it through
  `cli.main` and `evaluate.run` the one that runs `python -m distiller`; no risk score changes
  there, and two change on this repository's `v0.3.0..main`.

- **Feature:** candidate mapping tells a helper the tests reach from one nothing calls. A test
  that loads a changed module is now followed through the module's own calls — `cli.main` calls
  `_run_command`, which builds `_parser`, which hands `pr_number` to argparse — and through the
  helpers of its own file, so it becomes a candidate for every symbol it reaches ("changed
  symbol reached through calls"). Before, a helper no test named had only its module's test
  file, the same as a helper nothing calls. A private, undecorated helper that no such test
  reaches or names now has no candidate, since only its module calls it; a public function or a
  decorated helper keeps the file, as something else may call it. On `distiller`, `_parser`,
  `_run_command`, and `pr_number` gain the tests that reach them and fall four or five points;
  on this repository, 24 private helpers reached through their module's public functions gain
  their first candidate.

- **Fix:** added code reads as added, and an added or removed class or module shows what it is.
  Every added symbol, a brand-new file's included, was "added without a reliable semantic match"
  and warned that "callers or observable behavior may change" — the wording of a rename the
  matcher could not correlate. An added symbol now reads "A callable or structural symbol was
  added." with "New behavior becomes reachable; its intended contract needs characterizing
  tests.", a removed one names the callers it may break, and the old wording stays for the
  ambiguous near-matches it describes. The evidence of an added or removed class showed only its
  `bases(...)`, and a module only `module`. A class now shows its decorators, bases, and fields
  (`@dataclass class RunArgs: pr: int, head: str, …`) and a module its first top-level
  statements. Scores, confidence, and obligations are unchanged.

- **Fix:** running without a model is no longer reported as a fallback. The CLI's `--no-llm`, a
  CLI without a provider, and the Claude Code server run without one by design, yet every result
  warned "No inference model was provided; returned deterministic fallback findings." and listed
  "LLM interpretation was unavailable; deterministic fallback was used." among its limitations,
  so a reader took the analysis for a degraded one. Such a result now has no warning and states
  "No model interpreted the findings; each comes from a deterministic structural rule." A model
  that was called and failed still reports the fallback. The `deterministic_fallback` origin and
  `deterministic_mode` are unchanged. Sixteen evaluation goldens change accordingly.

- **Fix:** a candidate test pooled into an obligation from several findings keeps the reasons
  of its best match. It kept the best score but the union of every match's reasons, so the
  reasons no longer explained the score: on `distiller` one test showed 0.60 with five reasons
  worth 0.95. Every listed candidate's reasons now add up to its score; which tests are listed,
  and every score, are unchanged.

- **Fix:** tests matched only through their file stand as that file. A mirrored file name, an
  import, a configured mapping, a `python -m` run, and a package import are evidence about a test
  file and hold for every test in it, yet each test was listed as a candidate of its own and the
  list cut to five alphabetically: a `cli.py` helper that no test names got the first five of
  `test_cli.py`'s eight tests. Such tests now make one candidate, the file, with the symbol
  `<module>`, shown in the brief as `tests/test_cli.py` (whole file). A file with one such test
  still names it, and no score or risk changes. On this repository's `v0.3.0..main` 50 findings
  gain a whole-file entry and two obligations change their candidate list.

- **Fix:** importing a package no longer counts as a direct import of every module in it.
  `from distiller import cli` records `distiller`, and every module under an imported package was
  treated as imported, as "direct module or symbol import". With category terminology alone that
  reached the 0.35 threshold, so tests of one module were proposed for modules they never touch:
  on this repository, the skill's risk-formula test for most of `mcp_server.py`, and other
  scripts' tests for `scripts/verify_wheel.py`. The relation is now its own signal, "changed
  module's package imported", at 0.15: enough to confirm a test named after the change —
  `distiller`'s `unexpected_failure` keeps its test — and never enough with terminology alone. On
  this repository's `v0.3.0..main`, 28 findings lose candidates that were all unrelated and score
  six or seven points higher; `distiller` and `meal-manager` keep every candidate.

- **Fix:** a changed `__main__.py` finds the tests that run it. No test imports a package's
  `__main__.py`; they run it with `python -m pkg`, so a changed one never had a candidate and its
  finding carried the full test gap. `tests/test_main.py` now mirrors `__main__.py` — a test file
  names a module without the underscores that make it private or special, so `test_common.py`
  also mirrors `_common.py` — and a test file that runs `pkg` as a program, as
  `[sys.executable, "-m", "pkg"]`, `"python -m pkg"`, or `runpy.run_module("pkg")`, counts as
  much as an import of `pkg/__main__.py` ("changed module run with python -m"). Running any other
  module adds nothing, being as coarse as an import of it. On `distiller`, `__main__` gains
  `test_python_m_distiller_exits_with_what_main_returns` and the overall risk falls from 49 to 42.

- **Fix:** a test named after a changed symbol is found when the name has more than one word,
  and a single word no longer finds tests about something else. Test names and bodies were split
  into single words, so `pr_number` could never match `test_pr_number_rejects_zero`, while `run`
  matched any test whose name or body held `run_args` or `run_args_from`. A symbol's name now
  matches a test name as whole words in sequence — `RunArgs` and `_run_args` read as `run_args` —
  and a test body by whole identifiers, a private wrapper such as `_serve` reading as `serve`. A
  single word names the symbol only in a test whose body also uses it, a dunder such as
  `__init__` names nothing, and a test's own name no longer counts as part of its body. On
  `meal-manager`, `atomic_write_json`, `validate_state`, and `from_dict` gain their tests and six
  risk scores fall by six or seven points; on `distiller`, `messages.unexpected_failure` gains the
  test named after it.

- **Fix:** an obligation no longer shows one finding's candidate tests for another that has none.
  Equivalent scenarios merged on their template's wording, which names no symbol, so every finding
  of a category shared one obligation, its candidate tests pooled and its status
  `candidate_exists_unverified` as soon as any one had a candidate. A finding with no test was
  listed under another's tests, and nothing in the brief told the two apart. Obligations now
  merge only between findings with the same coverage status, so findings without a candidate get
  their own obligation reading "none (no_candidate_found)", at the higher priority their test gap
  already earned. Covered and uncovered findings no longer share an obligation either: each keeps
  its own grounded verdict, where a shared one used to fall back to static matching. The
  obligation grouping high-risk overflow past `max_test_obligations` reports its least-tested
  finding's status. Risk scores are unchanged, and the obligation count rises: 25 to 42 on
  `meal-manager` `8894749`, where 67 of 91 findings had been listed under other findings' tests.

- **Fix:** changed tests are no longer analyzed as product code. The default include is `**/*.py`,
  so a branch that added a module and its tests reported every test function, helper, and nested
  fake as a medium-risk behavior change with an obligation to test it — 17 of 28 findings on one
  such branch — and those findings set the overall risk. A changed file under a `test_roots`
  entry, a `conftest.py`, or a file named `test_*.py` or `*_test.py` that defines a test is now
  left out as `test_file` ("test file(s) left out by `analyze_test_files: false`") and stays a
  candidate test. A name alone is not enough: a production module named like a test, such as
  this repository's `test_mapper.py`, is still analyzed. Files under a test root no longer count
  toward the changed-file and changed-line budgets. The new `rules.analyze_test_files: true`
  restores the previous scope.

- **Fix:** a changed file that is not Python is excluded as not Python. The default include is
  `**/*.py` and the path filter ran first, so every Markdown or YAML change counted as
  `path_filter` and the brief called it "outside the configured include and exclude paths", even
  in a repository that configured none — which read as a configuration problem. The extension is
  now checked first, so such files count as `unsupported_extension` ("not Python"), and
  `path_filter` keeps the Python files the include and exclude patterns reject. The same files are
  excluded and every finding is unchanged; only the reason in `scope.excluded_counts` moves.

- **Feature:** comparing a commit with itself says so. When the base and head refs resolve to the
  same commit — `main` and `HEAD` before a change is committed — the result's 0/100 risk at 100%
  confidence read like a clean review. It now carries a warning that nothing was compared and that
  only committed changes are analyzed. It stays a successful result rather than an error, because
  it is accurate, and nothing else in it changes.

- **Fix:** a test named after a changed module is now its candidate. Code with one module per
  tool — each exporting a generic `HANDLER`, loaded with `importlib` — gave candidate mapping
  nothing to match, so a handler's own tests were never proposed while the token `handler`
  proposed unrelated ones. A test whose name spells a multi-word module stem, such as
  `test_register_cooked_meal_rollback`, now scores like a mirrored file name; a module mention in
  a test body only confirms such a test. On `meal-manager`, 25 obligations gained candidates and
  32 risk scores fell by 6 or 7 points as their test gap closed; this repository is unaffected.

- **Feature:** the Markdown brief carries finding and obligation IDs. Each finding shows its
  `bc-` ID, and each obligation its `to-` ID and the findings it covers, so a question about
  specific obligations no longer needs the JSON. Under Claude Code, a brief too large for the
  host is rendered again with shorter evidence excerpts — never fewer findings or obligations —
  instead of being saved to a file the model cannot read in parts.

- **Fix:** coverage grounding judges the lines the diff changed, as documented, not each finding's
  whole symbol. A finding's span is its symbol — for `<module>`, the whole file — so one
  uncovered, unchanged line hid a covered change, and the summary summed overlapping spans, so it
  could report more "changed lines" than the file had — 411 in a 366-line file whose diff added 46.
  Changed lines now come from the `--unified=0` hunks, and each is counted once per file. Only
  analyses given a coverage report are affected.

- **Fix:** authorization failures say how to fix them. Every `path_outside_repository` error
  carried the remediation "Use a repository-relative path that remains inside the resolved Git
  repository", including a repository refused for lying outside the authorized workspace, where no
  relative path can help. Refusals now name the remedies — a path inside an authorized root, or the
  operator listing its directory in `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` — with the entry
  separator and a warning that setting the variable replaces the default root, so the previous
  default must be listed too. A misconfigured
  variable, a missing path, and a filesystem-root entry each get their own. The error code and
  messages are unchanged; repository-relative path errors keep their remediation.

- **Feature:** the Markdown brief says why scope was left out, not only how much. Below the
  **Scope:** line, an **Omitted:** line counts each omission reason in words — for example
  "2 low-risk refactor(s) hidden by `emit_low_risk_refactors: false`" — and an
  **Excluded files:** line does the same for exclusions. A reason without a phrase is shown as its
  code. The canonical JSON already carried these reasons and is unchanged; the brief is what the
  CLI prints, the GitHub Action comments, and Claude Code reads.

- **Fix:** the deterministic keyword rules no longer read the words of prose string literals, so a
  changed message such as `'Retry with a narrower diff'` is no longer a `retry_timeout_change`.
  Comparisons, conditions, and calls are classified from a probe built from the AST: identifiers,
  attribute and keyword names, and literals without whitespace, which still carry keys and headers
  such as `'Authorization'` or `'retry_limit'`. On this repository's history 10 of 1,909 evidence
  classifications changed, each one resting only on prose; the evaluation corpus is unchanged. See
  `docs/decisions.md`.

- **Fix:** the condition rule decides whether a changed condition compares from its AST operators,
  not by searching its text for `<`, `>`, `==`, or `!=`. An operator inside a string literal or a
  shift (`1 << bit`) no longer makes a boundary, and an equality that redaction consumed —
  `token == expected` is displayed as `token =[REDACTED] expected` — is no longer missed. On this
  repository's history one of 1,989 classifications changed; the evaluation corpus is unchanged.

- **Fix:** stop blaming a Hermes host when no model is in use. The `warnings` entry for a run
  without a model read `Hermes-hosted LLM unavailable; returned deterministic fallback findings.`
  on every transport, although the CLI's `--no-llm`, a CLI without a provider, and the Claude Code
  server run without one by design; it now reads `No inference model was provided; returned
  deterministic fallback findings.` The `llm_unavailable` and `llm_schema_failure` remediations
  name both ways to supply a model, and the `internal_error` remediation no longer points at
  Hermes debug logs, which never held stage-level details. Error codes and findings are unchanged.

- **Tests:** `scripts/verify_wheel.py` gains offline unit tests for every archive defect it
  rejects, which the gate — only ever run on a good build — could not show. Writing them found
  three gaps, now closed: a wheel without `METADATA` crashed with `StopIteration` instead of being
  reported; entry points were matched as substrings, so a line under the wrong group or a
  near-miss target such as `cli:main_legacy` passed; and the install phase never ran the
  installed `semantic-diff-weaver-mcp`, which it now asks for its tool list.

- **Fix:** send `temperature` to the Anthropic SDK through `extra_body` instead of as a keyword
  argument. SDK 1.0 removed the keyword from `messages.create`, so with `anthropic>=1` every
  call on a model that still honors sampling (for example `claude-sonnet-4-6`) raised
  `TypeError`, surfaced as `ProviderUnavailable`, and fell back to deterministic mode without an
  error. The default model was unaffected. Behavior on 0.x SDKs is unchanged.

## 0.3.0 - 2026-07-30

- **Feature:** support the one-command plugin install,
  `hermes plugins install sergiparpal/semantic-diff-weaver --enable`. The installer clones the
  repository into the Hermes plugins directory without building it, so the root `__init__.py` now
  appends its own directory to `sys.path` before importing the package — previously the directory
  form loaded only when the wheel was also pip-installed. A missing Pydantic or PyYAML is reported
  by name with the command that fixes it instead of as a bare `ModuleNotFoundError`. `plugin.yaml`
  gains `manifest_version: 1` and an explicit empty `provides_hooks`, and a new `after-install.md`
  walks through enabling, dependencies, authorized roots, and the first run. See
  `docs/decisions.md`.

## 0.2.0 - 2026-07-26

The project becomes runnable without Hermes: a standalone CLI, a GitHub Action, an optional
inference provider, and coverage grounding. The Hermes plugin path is unchanged.

- **Fix:** define statement ordering as a permutation instead of content inequality.
  `SymbolSnapshot.statement_order` entries embed unparsed expression content, not just sequence
  identity, so comparing the tuples for inequality reported a phantom reordering on any pure
  content edit — adding a keyword argument to `return client.fetch(x)` moved nothing yet emitted
  `ordering_change`. Ordering now requires a permutation, mirroring the test `_append_call_delta`
  already applied to call names. Material precision on the 17-case corpus rises from 88.24%
  (15/17) to 100.00% (15/15) with recall unchanged at 100%; both removed findings were verified
  non-genuine. See `docs/decisions.md`.
- **Breaking (schema):** `SCHEMA_VERSION` is now `"1.1"`. `CoverageStatus` gains
  `covered_by_existing_tests` and `changed_lines_uncovered`, and `AnalysisResult` gains an
  optional `coverage` object. Every existing value is unchanged and `coverage` is `null` without a
  report, but a consumer that exhaustively matches `coverage_status` now sees new values and needs
  a version signal.
- **Feature:** ground candidate coverage in an ingested coverage report. `--coverage PATH`,
  `coverage_report`, or `coverage.report_path` accepts a coverage.py JSON or lcov `.info` report,
  sniffed by content and bounded by the new `rules.max_coverage_bytes` (default 20 MB). The report
  is untrusted input data and nothing in it is ever opened or executed. A changed file absent from
  the report is reported as **unknown, never uncovered**, and unmatched files are counted and
  warned about. `CandidateTest.verified` stays `false`: coverage says a line ran, not that a test
  asserts the change. Cobertura/JaCoCo XML is excluded deliberately — see `docs/decisions.md`.
- **Fix (Windows):** the CLI writes UTF-8 regardless of the platform locale encoding. The
  brief contains U+00B7, U+2014, and U+2205, and Python selects the *locale* encoding for
  stdout — cp1252 on a default Windows install, where U+2205 has no mapping. A diff that added
  or removed a symbol therefore died mid-report with an unhandled `UnicodeEncodeError`, and
  redirected output was undecodable even when the run survived. The output encoding is the
  CLI's contract, so it now sets it rather than inheriting it.
- **Feature:** add a standalone command line, `semantic-diff-weaver` and
  `python -m semantic_diff_weaver`, with `--repo`, `--base`, `--head`, `--include`, `--exclude`,
  `--risk-profile`, `--coverage`, `--format`, `--allow-root`, `--fail-on`, `--model`, and
  `--no-llm`. Exit codes are `0` success, `1` analysis error, `2` argument error, and `3` success
  above the `--fail-on` risk threshold.
- **Security:** the CLI populates `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` from `--repo` and
  `--allow-root` when it is unset, because on a command line a person types the path and that is
  the authorization. An operator-set value is never widened: `--allow-root` is refused and a
  `--repo` outside the bound fails as before. See `docs/security.md`.
- **Feature:** add a GitHub Action that posts the review brief as one pull-request comment,
  upserted by hidden marker so re-runs edit rather than append, and truncated at a section
  boundary with an explicit notice above GitHub's 65,536-character cap. No SARIF and no check
  annotations, by decision. See `docs/github-action.md`.
- **Feature:** add an optional Anthropic provider (`pip install 'semantic-diff-weaver[anthropic]'`)
  so the CLI keeps the inference layer. A missing package, missing key, provider error, timeout,
  or schema failure all degrade to deterministic findings; credentials are never a hard failure
  and are never logged, echoed in an error, or written to output.
- **Refactor:** name the structured-inference contract as an explicit `LlmClient` protocol.
  `llm: Any` was threaded from the Hermes handler through the service into the interpreter, where
  exactly one method is ever called. Hermes' `PluginLlm` satisfies it unchanged.
- Add a new stable error code, `coverage_unreadable`, and a new optional tool argument,
  `coverage_report`. All existing codes, taxonomy values, and evidence IDs are unchanged.
- Widen the CI interpreter matrix with Python 3.12 and 3.13 on Linux. 3.12 is the local
  development and `hermes-compatibility` interpreter and was previously never exercised by the
  main test job.
- Hold `cli.py` and `coverage_map.py` to the 90% critical-module branch bar; both are at 100%.

### Review fixes folded in before the tag

These correct defects in the work above. They were found in review after it landed on `main` and
before any of it was tagged, so no consumer ever saw the behavior they change. `SCHEMA_VERSION`
stays `"1.1"`; the only output difference is a new `model_input_symbol_limit` omission reason that
was previously miscounted under `llm_batch_limit`.

- **Fix:** stop reporting valid Python as unparseable. `extract_symbols` parsed with
  `type_comments=True`, which accepts a strictly *narrower* grammar than the default — a stray
  module-level `# type:` comment is a `SyntaxError` under the flag and valid Python without it.
  Such a file was reported as `parse_incomplete`, emitting a fabricated `unknown_semantic_change`
  finding at 0.68 baseline confidence about source Python itself accepts. Type comments only
  enrich the rendered signature, so the flag now degrades to a plain parse instead of deciding
  parseability. `test_mapper` never read a type comment at all and no longer asks for them.
- **Fix:** send a request shape the model accepts on always-on-thinking families. The Anthropic
  adapter sent `thinking: {"type": "disabled"}` unconditionally, which those models reject with
  HTTP 400 — so *every* call failed, was mapped to `ProviderUnavailable`, and silently collapsed
  the analysis into deterministic fallback with no sign the request shape was at fault. The
  parameter is now omitted for them and kept everywhere it is honored.
- **Fix:** `__version__` said `0.1.1` while `pyproject.toml` and `plugin.yaml` said `0.2.0`. The
  three are now pinned to each other by a test.
- **Fix:** a module that still carries a precise delta keeps its changed-symbol count.
  `_drop_redundant_module_deltas` filtered the key set by *path*, so a file with any detailed
  delta lost its `<module>` key even when only the three generic lifecycle kinds were dropped and
  a module-scope condition change survived — under-counting `summary.changed_symbols`.
- **Fix:** report omitted LLM batches and oversized symbols as the different units they are.
  The two were summed into one counter published under `llm_batch_limit`, so a symbol count was
  reported under a reason named for batches. Oversized symbols now get their own
  `model_input_symbol_limit` reason and their own warning.
- **Performance:** coverage-report path resolution is memoized and its candidate paths are split
  once per report rather than on every lookup. `status_for` and `counts_for` resolve
  independently, so every finding paid two full `O(report files)` scans through `PurePosixPath`
  construction; a 2,000-file report went from ~4ms per lookup to effectively free after the first.
- **Robustness:** an evidence-free candidate no longer raises `IndexError` while being packed for
  a model call, a high-risk behavior with no linked obligation degrades instead of raising
  `StopIteration` through the tool boundary, and a present-but-empty scenario tuple now fails the
  taxonomy import that already checks for missing ones.
- **Security (hardening):** `scripts/pr_comment.py` constrains `--repository` and
  `--pull-request` before interpolating them into a `gh api` resource path. The `gh` boundary was
  already argument-list-only, so this was never shell injection, but an unvalidated component
  containing `/` or `..` could retarget the request at a different endpoint.
- **Fix (CLI):** a set-but-empty `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` now names the variable
  instead of failing with "no authorized workspace root is configured", which pointed at the
  invocation rather than the cause. The operator's bound is still never widened.

## 0.1.1 - 2026-07-25

- Rename the project from `hermes-semantic-diff-weaver` to `semantic-diff-weaver`. The distribution,
  the `semantic_diff_weaver` package, and the plugin entry-point name all drop the `hermes-` prefix.
  Existing installations must uninstall the old distribution and re-enable the plugin under its new
  name.
- **Breaking:** rename the `HERMES_SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS` environment variable to
  `SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS`. The old name is no longer read; hosts that set it lose the
  extra authorized roots and fall back to the process working directory.
- **Fix:** classify module-scope and unparsed condition changes by their changed expressions
  instead of their symbol name. The synthetic names `<module>` and `<unparsed>` contain angle
  brackets, which the comparison-operator probe read as `<` and `>`, so every such condition was
  forced to `boundary_change` or `retry_timeout_change` and could never reach the authorization,
  validation, or retry-guard rules. A module-scope authorization guard now scores at impact 92
  rather than 64, with the matching obligation scenarios and candidate-test terminology.
- **Fix:** report PEP 695 type parameters as part of a callable or class signature. Changing
  `def f[T]` to `def f[T, U]`, or adding a bound such as `[T: int]`, previously fingerprinted
  identically, was labelled `structural_refactor`, and was then dropped entirely under the default
  `emit_low_risk_refactors: false`. Such changes are now `signature_change`.
- **Fix:** raise a `configuration_error` for a scalar `language:` section. `language: python`
  previously raised `AttributeError` and surfaced as an opaque `internal_error` with no
  remediation.
- **Fix:** own Git child processes with a context manager so their pipes close deterministically.
  Every Git invocation previously leaked a `ResourceWarning` per stream until refcounting
  collected the `Popen`.
- **Fix:** skip malformed `--numstat` counts and truncated rename records instead of raising bare
  `ValueError`/`IndexError` past the error contract, and tolerate an empty path in
  `exclusion_reason`.
- Bound the AST analysis deadline across matching and comparison, not only parsing, and compare
  against it inclusively. `time.monotonic()` is only as fine as the platform clock — about 15.6ms
  on Windows before Python 3.13 — so a strict comparison let an already-spent budget read as live
  there, and a zero timeout did no bounding at all. Files reached after the deadline are now
  reported as resource-limited rather than analyzed unbounded.
- Resolve candidate-test signals into memoized position sets and score only a candidate's
  structural hits instead of the whole index; dotted imports are matched through a leaf index.
  Output is unchanged (verified against the previous algorithm over randomized inputs) and the
  mapping stage is ~2.5x faster at the documented caps.
- Load configuration YAML through a `SafeLoader` subclass that enforces the node, depth, and alias
  budgets while composing, replacing a separate counting pass over every file.
- Move the taxonomy completeness guard ahead of the registry it protects, so an incomplete
  taxonomy fails with its named error rather than a bare `KeyError`.
- Enable the `S` (flake8-bandit) lint rules, and hold `ast_diff/` to the 90% critical-module
  branch-coverage bar.
- Read per-file hunks with one bounded Git command per path chunk instead of one process per
  changed file, keeping the previous single-path semantics via `--no-renames` and falling back to
  the per-file reader for any path Git does not report. Analysis output is unchanged.
- Remove redundant work from candidate-test mapping, candidate deduplication, and model batch
  packing, and memoize the pure path-glob and redaction helpers.
- Fix the omitted-obligation count when the global cap collapses overflowing high-risk behaviors
  into one grouped review obligation. The synthetic obligation replaces a generated one, so the
  previous count under-reported the omission by one. Canonical scope and the Markdown scope line
  now report the corrected number; all other output is unchanged.
- Make `SemanticCandidate` immutable and give it explicit `path`/`symbol` fields instead of deriving
  them from `evidence[0]`. Merging re-sorts evidence, so a candidate could previously change the
  identity that deduplication and batching had already keyed on.
- Decouple `ast_diff/` from `git_diff/` behind the new `source.py` `SourceRevisionPair` contract, and
  pass AST safety budgets as an explicit `AstBudget` instead of resolving them through a cached
  self-import of the package. `git_diff/` likewise calls its process runner directly.
- Merge the three per-`BehaviorCategory` tables in `scoring`, `obligations`, and `test_mapper` into
  one `taxonomy.py` registry whose completeness is checked at import and pinned by a contract test.
- Narrow `score_risk`, `generate_obligations`, and the candidate classifiers to the configuration
  they actually read, and trim both package `__init__` exports to the pipeline-facing surface,
  removing nine backward-compatible aliases that existed only for tests.
- Bring the documentation current with the above and correct the evaluation record. The reviewed
  17-case corpus measures 88.24% material precision (15 of 17 predicted categories), not the 100%
  the 2026-07-19 note claimed; that figure never held for this corpus, and the two extra
  `ordering_change` findings are present in the reviewed goldens from the corpus's first commit.

## 0.1.0 - 2026-07-18

- Add the read-only `analyze_semantic_diff` Hermes tool.
- Add bounded committed Git collection, Python AST structural comparison, deterministic behavior
  rules, structured Hermes-hosted interpretation, static candidate-test mapping, test obligations,
  risk/confidence scoring, and JSON/Markdown transports.
- Add safe configuration, path containment, secret redaction, deterministic fallback, packaging,
  CI, and automated evaluation support.
- Complete conservative rename matching, parse-incomplete unknown findings, critical-path resource
  prioritization, bounded README/model context, schema retry/reconciliation limits, and explicit scope
  truncation reporting.
- Expand the corpus, enforce overall and critical-module coverage policy, add a full-pipeline
  performance fixture, and add clean-wheel plus real Hermes 0.14.0/0.18.2 discovery gates.
- Complete return-annotation signature detection, method/module inventory, overload preservation,
  cross-file symbol moves, semantic obligation merging, shared-call LLM batching, and aggregate
  candidate-test index bounds; expand the reviewed corpus to 17 cases.
- Harden secret egress, model-data framing, terminal/control rendering, repository authorization,
  Git replacement-object handling, inherited Git environment isolation, configuration complexity,
  and adversarial AST resource limits.
- License the project under MIT.
