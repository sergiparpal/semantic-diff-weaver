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
- The plugin registers standalone tools — one in the MVP, and `generate_test_skeletons` beside it
  since 2026-11-01 — and no hooks, commands, skills, or overrides.

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

## Compatibility snapshot (2026-10-31)

- Hermes Agent 0.19.0, the current release since 2026-07-20, passes the same pip-entry-point and
  temporary-home directory discovery and registration checks in an isolated environment.
- Nothing the plugin uses changed from 0.18.2: `agent/plugin_llm.py` is byte-identical,
  `hermes_cli/plugins.py` differs only in comments on the approval hooks, and
  `PluginContext.register_tool` and `PluginLlm.complete_structured` have the same signatures in
  0.14.0, 0.18.2, and 0.19.0.
- The CI compatibility matrix now installs 0.14.0 and 0.19.0, the lowest verified release and
  the current one; 0.18.2 leaves it.
- The check of `complete_structured` used a list kept by hand that required `json_mode`, which
  the plugin never passes, and left out `temperature` and `purpose`, which every call passes. A
  Hermes release dropping either would have sent every model call to deterministic fallback
  behind a generic warning, with CI green. `scripts/verify_hermes.py` and the real-runtime test
  now require exactly the parameters `LlmClient` declares, which
  `tests/contract/test_llm_client_protocol.py` pins to the single call site. All three releases
  accept them.

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
copy of `ANALYZE_SEMANTIC_DIFF_SCHEMA` and delegates to `handle_analyze_semantic_diff`. Six choices
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
- **The skill reviews a branch from its merge-base (2026-10-19).** The tool diffs the base and
  head trees as they are, so a base that moved on after the branch point reads as the branch
  reverting the base's new commits, and a local `main` behind `origin/main` counts commits that
  are already merged. The skill used to suggest `main` or `origin/main`, confirmed by
  `origin/HEAD`, which a repository pushed after `git init` never records. On
  `adaptive-policy-compilation`, whose local `main` was six commits behind, only Claude's own
  judgment kept branch `escalations-hash` from a review of already-merged work. The skill now
  takes the branch the work merges into, prefers whichever of its local and remote copies
  contains the other, and passes `git merge-base <base> <head>` as `base_ref`.
  `tests/integration/test_service.py` pins the tree-to-tree comparison it relies on.

The server is declared inline in `.claude-plugin/plugin.json`, not in a root `.mcp.json`. Claude Code
reads a root `.mcp.json` as the repository's own project MCP configuration too, where
`${CLAUDE_PLUGIN_ROOT}` is undefined, so anyone opening the checkout in Claude Code got a broken
second server waiting for approval.

`.claude-plugin/plugin.json` carries the package version and is kept in step with it by
`tests/contract/test_claude_code_plugin.py`.

## API schemas (2026-11-01)

The roadmap's v0.2 asks for config and API schema awareness, and API schemas come first, at the
user's choice: a changed OpenAPI 3, Swagger 2, or JSON Schema document, in YAML or JSON, is now
compared beside the Python change. Configuration files are left for later, and will be read only
where a repository's configuration names them. Six choices are deliberate:

- **Content decides what a schema is.** Every changed `.yaml`, `.yml`, and `.json` file is read,
  and compared only if a revision's root says it is a schema: `openapi: 3.x` with `paths`,
  `components`, or `webhooks`; `swagger: "2.0"` with `paths` or `definitions`; or a `$schema`
  naming a `json-schema.org` meta-schema or the OpenAPI 3.1 dialect. A name does not settle it —
  an OpenAPI document is named after its service as often as `openapi.yaml` — and a configuration
  file that names the schema it is validated by, as `tsconfig.json` and `renovate.json` do in
  their `$schema`, is no schema. Every other file is counted as `not_api_schema`. The default
  `include` gains `**/*.yaml`, `**/*.yml`, and `**/*.json`, so an `include` a repository wrote for
  its Python narrows its schemas too, and `rules.analyze_api_schemas: false` turns the comparison
  off, leaving YAML and JSON `unsupported_extension` as before.
- **A file that cannot be read costs no completeness; a schema that does not parse does.** Most
  changed YAML and JSON files are lockfiles, manifests, and data. One too large to read, not UTF-8,
  or a link is counted as `unreadable_document`, and the analysis can still pass, since whether it
  was a schema cannot be told unread; a Python file that cannot be read still truncates. A document
  that does not parse within its budgets is a schema left unread when its text opens with a
  schema's key and version or meta-schema. It is then a finding of its own,
  `document_parse_incomplete`, and the analysis is truncated; otherwise it is `not_api_schema`.
- **Documents have budgets of their own.** At most 200 changed YAML and JSON files are read,
  critical paths first, with 64 MiB of base and head text in all, apart from the 64 MiB of Python
  source, so a change to many manifests or one large lockfile cannot crowd out the Python files the
  line budget is for. A file past either is `document_limit` omitted scope, which truncates. One
  document's comparison stops at 500 changes, as `schema_delta_limit`.
- **What is compared is what a client or a server can observe.** Operations by method and route,
  their parameters by where and what they are, request bodies, responses by status, media types,
  and the security they require; then global security and security schemes, servers, and reusable
  schemas; and within a schema its properties, `required`, `enum`, `type`, the numeric and length
  bounds, `pattern`, `format`, `default`, `items`, `additionalProperties`, and `$ref`. Descriptions,
  titles, summaries, examples, operation IDs, tags, and `x-` extensions are words for people and
  change no behavior, so nothing reads them; another keyword is a `schema_keyword_change` for
  review. `$ref` is compared as text and never resolved, so a change to a reusable schema is
  reported once, at the schema, not at every operation using it.
- **Where a change is decides what it means.** Each change records whether its place is part of a
  request, a response, a reusable component, or a standalone JSON Schema. A narrowed `enum` or a new
  property in a request, or in a JSON Schema, which validates what it is given, is a validation
  change; in a response it changes the output contract. A component serves requests and responses
  alike and is read as a response, the side a client depends on. A change to `required`, a
  pattern, or a bound means the same anywhere. A removed operation is an output-contract change, a
  new required parameter a validation change, a security change an authorization change, and a new
  operation an unknown change, whose intended contract static analysis cannot know.
- **An operation's tests request its route.** A change to an API schema has no Python symbol to
  import or call, so its candidate tests are those that write out its route: a string literal in a
  test, or in a same-file helper it calls, that is a path or a URL's path, an f-string's
  placeholders standing for one segment each, matched against the route template after any base
  path the API is served under. A test that also calls the operation's method by name,
  `client.delete(...)`, ranks higher, and one calling only other methods is about another
  operation of the route. A change to a reusable schema names no route and gets no candidate,
  since which operations use it is not followed through `$ref`.

The schema of the result is unchanged. Two outputs read differently: the limitation for a diff
with nothing to analyze now says "no included changed Python source or API schema", one golden
line, and the brief's line for an analysis with no finding no longer says Python. The analysis
tool's description, the MCP server's instructions, and the skill name API schemas. `schema_diff/`
is a critical module, held to the branch-coverage bar like `ast_diff/`.

## Test skeletons beside their candidates (2026-11-01)

A module without a test file of its name now gets one beside the obligation's first candidate
test, rather than in the first test root, at the user's choice once 0.7.0 had shipped. On this
repository's `fed1578..63c8dec`, the obligations of `ast_diff/compare.py`, whose candidate tests
are all in `tests/unit/test_category_probes.py`, were scaffolded into a new `tests/test_compare.py`,
a directory above every test of the module; they now go to `tests/unit/test_compare.py`. Three
rules keep it safe:

- **Only a test file places one.** The candidate must be a test file at the analyzed commit —
  under a test root or defining a test — readable, not excluded, and not a link. A production
  module named like a test does not count, and a candidate path the mandatory exclusions refuse,
  such as one under `.git`, is never looked up. Without such a candidate the first test root
  stands, as before.
- **One new file per module.** Two obligations of one module can have their first candidates in
  two directories, and two new files of one basename would make pytest refuse the second outside
  packages, so the first of the module's obligations, in the analysis's order, places the one file
  all of them get.
- **Nothing is overwritten.** Beside a candidate outside the test roots, a production module can
  already bear the new file's name; that obligation is skipped, with the reason.

## The `generate_test_skeletons` tool (2026-11-01)

The specification's optional `generate_test_skeletons` tool is served by both transports, the
Hermes plugin and the MCP server, beside `analyze_semantic_diff`: the MVP's one-tool rule held while
there was one tool to serve. It is the `skeletons` subcommand behind the tool boundary — the same
request model, the same patch, and the result `--format json` prints — and is read-only like the
analysis tool: it returns the patch and never applies it. Three choices are deliberate:

- **The analysis is named either way, exactly one of them.** A model that read a brief has no file
  to pass, since the analysis it read lives in the conversation. So the tool takes the analysis
  tool's own arguments, from `base_ref` on, and runs that analysis again without inference. Or it
  takes `analysis_path`, the JSON of a saved analysis, as the specification's signature and the CLI
  have it. A deterministic analysis numbers its obligations alike on every run: on this
  repository's `fed1578..63c8dec`, two runs gave the same six obligations under the same IDs, each
  run with its own analysis ID. Over MCP every analysis is deterministic, so a brief's IDs name
  the same obligations in the tool. An analysis made with inference, as under Hermes, can number
  them otherwise, and `analysis_path` is how to scaffold from one: running it again with inference
  would not reproduce its numbering either.
- **Only the arguments that shape obligations are taken.** `include`, `exclude`, `risk_profile`,
  and `coverage_report` decide which obligations exist and how they are numbered — coverage decides
  whether two behaviors' obligations merge. `baseline_report` only marks findings new or resolved,
  and `output_format` only shapes the output, so neither is accepted. `head_ref` has no default in
  the schema, so a host that fills defaults in cannot send one beside an `analysis_path`.
- **No inference, and no choosing for the user.** The Hermes handler of this tool does not close
  over `ctx.llm`, so nothing it runs involves the host's model. The skill tells Claude to scaffold
  only the obligations the user named, and to apply the patch only when asked.

`scripts/verify_hermes.py` and `scripts/verify_wheel.py` now expect both tools, from a real Hermes
and from the installed MCP server.

## Test skeletons (2026-11-01)

The roadmap's v0.3, test skeleton generation, is the CLI's `skeletons` subcommand: the
specification's `generate_test_skeletons(analysis_path, framework)` on the command line. It reads
the analysis an earlier run saved with `--format json` or `--format both`, and the IDs of the
obligations the user approved from it, and prints a patch adding one test per obligation. Six
choices are deliberate:

- **Approval is given twice, and nothing is written.** The user names each obligation by ID — no
  flag approves them all — and applies the printed patch with `git apply` after reading it. The
  tool writes no file and makes no commit, as the roadmap requires, so the read-only invariant
  holds for this command too.
- **A skeleton fails until it is written.** Under pytest it raises `NotImplementedError`, under
  unittest it calls `self.fail()`, as PyCharm's generated tests and Eclipse's
  `fail("Not yet implemented")` do. A skipped skeleton would have been safe to commit unwritten,
  and an obligation someone approved would then pass CI with nothing tested; a failing one cannot
  reach a protected `main` unwritten.
- **It goes in the module's test file.** That is the file named `test_<module>.py` or
  `<module>_test.py`, as candidate-test mirroring reads a name, preferring one among the
  obligation's candidates and then one mirroring the module's directory. When there is none, it is
  created as `test_<module>.py` in the first test root, and since "Test skeletons beside their
  candidates" beside the obligation's first candidate test. A file with that name that cannot take
  the skeleton is reported, not worked around with a second file beside it: pytest refuses two test
  modules of one basename outside packages, as this repository's suite did while this change was
  being written. A production module merely named like a test, as `test_mapper.py` is here, is not a
  test file, by the rule candidate-test discovery already applies.
- **The analysis's text is comments.** It is untrusted input, read under the baseline's bound and
  validated field by field. Each Given, When, and Then, the behaviors and their symbols, and the
  candidate tests reach the file only as comments, each line nothing but a comment, with every
  non-printable character escaped. Test names are ASCII identifiers made from the symbol and the
  obligation's title, and none rebinds a name the file already has: a second `def` of an existing
  test's name would replace that test, which would then stop running without a sign.
- **The patch is against the analysis's head commit.** Test files are read from it through the Git
  boundary, never from the working tree, and the commit must be the one the analysis names, not a
  ref of that name. When it is not `HEAD`, a warning says so. The diff keeps a file's CRLF endings
  and gives a last line its missing line feed, so it applies to any file Git can show.
- **The command line first.** In Claude Code the host model already writes tests from the
  obligations, so the deterministic scaffold matters most to a person reading the brief. The
  `generate_test_skeletons` tool for Hermes and MCP is left for later, and the result's JSON form,
  `--format json`, is the shape it would return.

An unusable analysis is the new `analysis_unreadable` error, additive like `baseline_unreadable`.
An obligation the analysis does not list is a `configuration_error`, and an analysis of a commit
the repository lacks is `invalid_ref`. The analysis's schema is unchanged. The bounded read the
coverage report and the baseline each had a copy of is now `textutil.read_bounded_text`, shared by
all three. `skeletons.py` is a critical module, held to the branch-coverage bar like `baseline.py`.

## Output-affecting corrections (2026-11-01, assignments)

Assignments were the last probed feature whose rule read the targets of every assignment its
delta showed. The 2026-10-19 entry on assignments named for state recorded the limit: an unchanged
`state = ...` beside a changed `count = ...` made the change a state transition. They now read
the targets of the assignments that changed, as comparisons, conditions, loops, and calls already
do. Every probe now reads only what its delta changed, so `CHANGED_VALUE_FEATURES`, which listed
the features that did, is gone. A write to `state` whose value changed still counts, since its
target is its probe and the assignment is among those that changed.

Measured on every commit against its first parent, with the analyzer of `main` and of this change:

- This repository: 23 of 389 assignment classifications were state transitions, and 10 become
  unknown changes. Each changed assignments to targets such as `baseline`, `call_graph`,
  `YIELDING_RULES`, and `oversized_symbols`, and took its state word from one that did not
  change: the pipeline's `state` in `service.analyze`, the `STATE_TERMS` constant beside new
  module constants, the `status` field of `InterpreterResult`, git's `_parse_name_status`, and a
  `coverage_status`. Findings fall from 1,444 to 1,439, obligations from 638 to 611, and the
  highest risk falls in 5 pairs.
- `adaptive-policy-compilation`: 3 of 201 were state transitions, and 1 becomes an unknown change,
  a new `baseline` beside an unchanged `state` in `authorship/score.py`'s `main`. Findings stay at
  3,002, obligations fall from 381 to 378, and the highest risk falls in 1 pair.

No range's highest risk rises and no verdict moves. No classification moves on the 20 pull
requests of `docs/real-pr-validation.md` or on the 14 of the earlier sample, and the 18 corpus
goldens are unchanged.

One of the 10 changed no value. In `f963e63`'s `_collect_scope`, `changed_symbols = ...` moved
below two writes to `state` that kept their text. Reordered comparisons, assignments, and loops
change no value, so their words are their symbol's name alone; reordered conditions and calls are
ordering changes. Comparisons and loops have read that way since they read changed values, and
pure reorderings are rare: of the comparison, assignment, and loop changes in the 662 ranges,
three assignment changes and two comparison changes are one, and all five are unknown changes
now. A swapped pair of writes to `state` would read as an unknown change too, a limit kept rather
than a rule added for it.

The probe test that a long old side cannot crowd out the new one used assignments because they
read every value shown; it now changes the assignment it probes. A new case pins that an
unchanged `order.status = 'open'` beside a changed `count` is an unknown change.

## Output-affecting corrections (2026-11-01, printed lines)

The calls entry below left a changed `print` a dependency call, whose obligations ask how the code
handles a dependency's success, failure, and unexpected response, and left open whether it is a
side effect. It is one: `print` is now a side-effect term beside `warn`. A printed line reaches
whoever runs the code, as `capsys` observes, and a changed `sys.stdout.write()` was a side effect
already, by its `write`. The side-effect obligations — the write occurs as intended, and does not
when its conditions are not met — are the tests a changed line of output needs. The term is read
like the others, as a word of the calls that changed, so `console.print()` and
`traceback.print_exc()` count and `fingerprint()` does not.

What the calls entry weighed against it is risk. The side-effect category's impact is 84 and the
dependency category's 62, so each finding that moves gains 8 points, and a script that no test
reaches can cross from medium into high. Measured on every commit against its first parent, with
the analyzer of `main` and of this change:

- This repository: 4 of 348 call classifications move from dependency to side effect, all in
  scripts that report to whoever runs them — three error messages `scripts/verify_wheel.py` prints
  to `stderr`, and the baseline status `scripts/pr_comment.py` prints. Findings stay at 1,444,
  obligations rise from 632 to 638, the highest risk rises in 4 pairs, and no verdict changes.
- `adaptive-policy-compilation`: 28 of 179 move, each a changed `print()` in a script. Twelve are in
  `542defd`, which renames Spanish identifiers to English, and five in `fa28d22`, which renames
  three packages. Findings stay at 3,002, obligations rise from 366 to 381, and the highest risk
  rises in 12 pairs. Two findings cross into high, both in `542defd`, whose printed values keep
  their meaning under new names: `contra` became `against`. That range's verdict moves from `warn`
  to `review_required`, the only verdict to move in the 662 ranges the two histories measure. A
  rename reads as a change, as `docs/real-pr-validation.md` notes of finding 4's loop variables, and
  this one was a dependency interaction before.

None of the 20 pull requests of `docs/real-pr-validation.md`, nor of the 14 in the earlier sample,
changes a `print`, and the 18 corpus goldens are unchanged. A logging call is still a dependency
call: none of the measured histories changes one, so it is left for when one does.

## Output-affecting corrections (2026-10-31, calls)

`SDW-RULE-DEPENDENCY` made every changed call that no keyword explained a
`dependency_interaction_change`, whose obligations ask how the code handles a dependency's
success, failure, and unexpected response. Of the 15 dependency findings in
`docs/real-pr-validation.md`, 8 changed calls to the symbol's own class or module —
`self.get_help_spec(ctx)` for `self.make_metavar(ctx)`, a new `self._check_name_is_usable(...)`, a
helper such as `_starts_statement` — and the review there found that none of the 15 has a
dependency's failures to test.

A changed call is now an `unknown_semantic_change`, under the new
`SDW-RULE-UNKNOWN-INTERNAL-CALL`, when every call that changed reaches the module's own code: a
function or class the module defines in either revision, called by its name; a method one of its
classes defines, called through `self`, `cls`, or the class; or a builtin that computes
in-process, from `len` and `isinstance` to `sorted` and `getattr`. `open`, `print`, `input`,
`exec`, `eval`, `compile`, `__import__`, and `breakpoint` reach out and are not among them. What
an attribute holds is not known statically, so `self.client.fetch()` and `self.callback()` stay
dependency calls. So do a method inherited from another module's class, such as the
`generic_visit` of `ast.NodeVisitor`, and a method called on a local value, such as
`help.split()`. A symbol matched across files may call what either of its modules defines. The
rule takes the unknown condition's 0.55 baseline and its assumption, that the surrounding runtime
contract is unavailable to static analysis. A finding loses confidence for each distinct
assumption, and an internal call usually joins its symbol's unknown condition or comparison: with
an assumption of its own, six findings of a truncated range on `adaptive-policy-compilation` fell
below the reporting confidence.

The call rule also reads its keywords from the calls that changed and the symbol's name, no longer
from every call shown, the limit the boundary entry below left in place. An unchanged
`write_text(...)` made whatever call changed beside it a side effect, 12 times on
`adaptive-policy-compilation`, and an unchanged `state.record_omitted('llm_batch_limit', ...)` made
any call change in this repository's `service.analyze` a retry, by its `limit`. Assignments still
read every value shown. Two probe tests used calls to show what the display window covers and that a
long old side cannot crowd out the new one; the first now changes a call early in the text and one
late, and the second uses assignments.

`warn` joins the side-effect terms: a warning reaches whoever runs the code, as `pytest.warns`
observes, and depends on nothing. Click's `Parameter.human_readable_name`, which gained a
`warnings.warn`, was a dependency interaction. A changed `print` still is one. Whether it is a
side effect is a question of its own, since a script's printed lines are its output and the
side-effect category weighs the most.

Measured on every commit against its first parent, with the analyzer of `main` and of this change.
This repository's 122 pairs, one over the changed-line bound either way, hold 348 call
classifications, and 126 move. 85 move to `unknown_semantic_change`, 68 of them from
`dependency_interaction_change` and 17 from the keyword categories; of those 68, 31 change only
builtins, 27 only functions and classes of the module, and 10 methods of `self` or a mix. 34 move
from retry, validation, authorization, and side effect to `dependency_interaction_change`, their
keywords having sat in calls that did not change, and 7 from one keyword category to another.
`adaptive-policy-compilation`'s 541 pairs hold 179, and 62 move: 35 to unknown, 31 of them from
dependency; 24 to dependency, 17 of them side effects through an unchanged call; and 3 from
validation to retry. Findings fall from 1,502 to 1,444 and from 3,033 to 3,002, obligations from 751
to 632 and from 415 to 366, and a range's highest risk falls in 18 and 13 pairs. It rises in two
here, both in `service.analyze`, both retries at 47 by that `limit`. A changed
`build_call_graph(repo, head_commit, ...)` is a side effect at 56: `commit`, a side-effect term for
a transaction, names a Git revision in this repository. And an added `state.warnings.append(...)` is
a dependency call at 48.

On the 20 pull requests of `docs/real-pr-validation.md`, 7 of 18 call classifications move. Five
dependency findings become unknown changes: click's `Argument.get_error_hint`,
`Argument.get_help_record`, and both `_parse_decls`, and black's `_continues_fmt_skip_line`.
`Parameter.human_readable_name` becomes a side effect. Click's `Option.__init__`, a validation
change through its unchanged `self._validate(...)`, becomes a dependency interaction: it no longer
calls `inspect.cleandoc` and a module helper, and passes `help` on to `super().__init__()`.
Dependency findings fall from 15 to 10 and findings from 76 to 75. The 10 left change calls into
other modules, such as `textwrap.shorten` and `t.TypeVar`, or on values whose type static analysis
does not know, such as `help.split()` and `info_dict.update()`. On the 14 merges of the earlier
sample the same click findings move, and httpx's `RedirectMiddleware.build_redirect_request`, which
passes its own `self.redirect_headers` a new argument, becomes an unknown change; findings fall from
69 to 67. The 18 corpus goldens are unchanged: the corpus's one dependency case changes a call on a
client passed in as a parameter.

## The GitHub Action keeps its baseline in its comment (2026-10-31)

The baseline comparison needs the previous push's analysis, and a workflow keeps nothing between
runs. An artifact belongs to its run, so the next one would have to find it through the API with
`actions: read`; a cache key cannot be overwritten, and a fork's pull request writes caches of its
own. The pull-request comment the action already posts lasts as long as the pull request, is
replaced on every push, and needs no permission the action lacks, so the baseline lives there.

The comment ends with a hidden digest of its analysis: each finding's category, path, symbol,
risk, and the first 32 hex digits of a SHA-256 of its evidence, compressed with zlib and
base64-encoded, with no evidence text. A finding costs about 39 characters, so some 500 fit in
the 20,000 the digest may take beside the brief; the largest analysis measured on 2026-10-31 had
34. With the new `baseline` input at its default, `previous`, the next run reads the digest back
and passes it as `--baseline`. The analyzer's loader takes a digest as it takes a full analysis,
and both reduce to the same fingerprints, so the comparison is the one an analysis file gets.

The comment is matched by its author as well as its marker. `find_existing` matched the marker
alone, by design, but anyone can post a comment starting with it: posted before the action's first,
it was edited in the action's place, which GitHub refuses, and the step failed; read as the
baseline, a crafted digest could make every new finding read as unchanged. The author is
`github-actions[bot]`, the identity `github.token` comments as, which the action always uses;
`pr_comment.py --author` names another for a script run with a different token.

A digest read back is bounded before it is trusted: at most 20,000 encoded characters, inflated no
further than 2 MB, and loaded by the analyzer, which validates every field. One that fails — an
edited comment, a digest from an incompatible release — is reported in the log and left out, and
the analysis runs as on a first push, since comparing with the previous push is a convenience
rather than a reason to fail a review. The decoded digest is written to the checkout's `.git`
directory, already an authorized root for the analyzer, so no authorization is widened for it,
and no later step finds it in the working tree. The analyzer, its schema, and its CLI are
unchanged; `pr_comment.py` gains `--read-baseline` and `--author`, and the action its `baseline`
input.

## Baseline comparison (2026-10-31)

The roadmap's "baseline comparison between analysis revisions" is `--baseline PATH`, or
`baseline_report` in the tool arguments: the JSON an earlier run wrote, from `--format json` or
`--format both`, of the same repository. The result's new `baseline` object lists this analysis's
findings as `new`, `updated`, or `unchanged`, and the baseline's findings no longer reported as
`resolved`; the brief counts each group, marks new and updated findings beside their IDs, and
names the resolved ones.

A finding is the same one when its category, path, and symbol are: the key `build_candidates`
groups findings by, so it names one finding in each analysis. Its evidence decides `updated`
against `unchanged`, compared by kind, path, symbol, and old and new text, without line numbers,
which move with any edit above a symbol. A finding whose category changed is a resolved one and a
new one, which is what a reviewer sees it as. Finding and evidence IDs number positions within one
result, so they are never matched, and the baseline's are not quoted.

The baseline is untrusted input, read like a coverage report: bounded by the new
`rules.max_baseline_bytes`, strict UTF-8, authorized like `risk_profile`, and validated for only
the fields the comparison reads, so any 1.x schema can be compared and nothing else in the file is
trusted. A resolved finding reaches the result and the brief by its validated category, path,
symbol, and risk alone, escaped like the candidate tests. Its summary does not: it is free text a
crafted file could fill with a heading, a footer, or an instruction to the model reading the
brief, which `tests/security/test_baseline_inputs.py` pins. An unusable baseline is the new
`baseline_unreadable` error, a hard failure as an unreadable coverage report is, since an analysis
that silently compared nothing would look like one that found nothing changed.

The `baseline` object is optional and `null` without a baseline, part of schema 1.2, which no
release has carried yet: the golden diff is 18 additive `"baseline": null` lines. `baseline.py` is
a critical module, held to the branch-coverage bar like `coverage_map.py`. The GitHub Action does
not keep an analysis between pushes yet, so it has no `baseline` input.

## Advisory verdict (2026-10-31)

The specification's roadmap promised "policy thresholds for advisory `pass`, `warn`, or
`review_required`". Until now the one policy was `--fail-on`, which fails a run at an overall risk
level and tells a reader of the report nothing. `summary.verdict` now carries one of the three,
read from the overall risk: `warn` from `verdict.warn_at`, `medium` by default, and
`review_required` from `verdict.review_required_at`, `high` by default. On the evaluation corpus
that is 3 `pass`, 12 `warn`, and 3 `review_required`: the three cases without a finding pass, and
the authorization guard, the side effect, and the critical-path case hold for review.

An analysis that left scope unread, or compared a commit with itself, is at least `warn`, and the
model rejects one that says `pass`. That is not configurable. A file left unread for a limit, a
parse failure, or an omitted model batch is not a file without findings, and a range with nothing
in it is most often a change that was never committed, the case the warning about equal refs
already names. `none` turns a risk level off, so with both thresholds at `none` the verdict is that
rule alone.

The verdict is advisory. `--fail-on` still decides the CLI's exit code and nothing blocks on the
verdict; the GitHub Action exposes it as a `verdict` output for a workflow to act on, and the
Markdown brief, and so the pull-request comment and the Claude Code brief, opens with it.

### Schema version bumped to 1.2

`Summary` gains the required `verdict` field and its three `Verdict` values. As with 1.1, a
consumer that validates the summary strictly now sees a field it has never seen, so
`SCHEMA_VERSION` is `"1.2"`, the `Literal` annotations on `AnalysisResult`, `MarkdownEnvelope`, and
`BothEnvelope` moved with it, and the contract tests and canonical goldens were updated
intentionally. The golden diff is 18 `schema_version` lines and 18 additive `verdict` lines, plus
the comma each `test_obligations` line gains before it; no finding, risk, confidence, or
obligation changed. The configuration gains the optional `verdict` section, and no error code
changes. The baseline comparison above adds its optional `baseline` object to the same unreleased
1.2.

## Output-affecting corrections (2026-10-31, output contracts)

Two corrections to `output_contract_change`, both from the hand review in
`docs/real-pr-validation.md`.

A signature change whose parameters are the same once annotations, the return annotation, type
parameters, and type comments are set aside now says so: "Only the type annotations of a callable
signature appear to have changed", with the impact that callers pass the same arguments while type
checkers, and code that reads annotations at runtime, may see a different declared contract, and
the assumption that annotations change behavior only where something reads them, such as a
framework the callable is registered with. It stays an `output_contract_change`: the corpus's
reviewed `return annotation signature` case expects one, and a declared return type is a contract
consumers see. But its text asked callers to supply different arguments, and four of the twelve
output-contract findings in that run were typing changes to `click.types.Path`. The assumption takes
its confidence from 0.879 to 0.80; its impact, and so its risk, is unchanged. A class's bases are
evaluated when the class is defined, so a change to them is never typing alone. On this repository's
history 29 of 89 signature classifications are typing alone, on `adaptive-policy-compilation` 1 of
54, and on the 14 real merges 5 of 9, under the new `SDW-RULE-SIGNATURE-ANNOTATIONS`. When its
finding also holds other evidence, as 4, 1, and 3 of them do, the typing-only classification gives
way: the finding takes the other one's summary, impact, and assumptions. Otherwise `Argument.get_help_record`, which narrowed
its return annotation when it stopped returning `None`, read "Only the type annotations ... appear
to have changed" over the return change below, at the confidence of an assumption about
annotations.

A changed comparison dropped every return change in its symbol, a rule meant for `return x < 5`
becoming `return x <= 5`, where the comparison says what changed. It now drops a return change only
when every changed return value shows a changed comparison. Click's `Argument.get_help_record` lost
an `if self.help is None` and the `return None` under it, and the change its callers see — a help
row for every positional argument — had been reported only through the signature's narrowed return
annotation. On this repository's history 34 return changes were dropped that way and on
`adaptive-policy-compilation` 26. They are now reported, each joining or forming an
`output_contract_change`, as return changes are wherever no comparison changed beside them, renames
such as `return resumen` becoming `return summary` included. No classification moves. Findings rise
from 1,306 to 1,333 and from 3,014 to 3,033, obligations from 637 to 647 and from 403 to 415, and no
range's highest risk rises. On the 14 real merges four return changes appear — `get_help_record`'s
in both pull requests that carry it, the `textwrap.shorten` rewrite of `_make_default_short_help`,
and `Path.coerce_path_result` — and the findings go from 68 to 69.

The `return annotation signature` golden is regenerated for the new text, assumption, and
confidence; its category, risk, and obligations are unchanged, the other 17 goldens are unchanged,
and precision and recall stay at 100%.

## Output-affecting corrections (2026-10-31, loops)

`SDW-RULE-LOOP` made every changed loop a `retry_timeout_change`. The `for` loop over a command's
parameters in click's `Command.format_arguments`, and the `while i > 0` that dropped words until a
help text fit in `_make_default_short_help`, became retries with obligations to recover before a
retry limit and to stop on a terminal failure; none of the three loop findings in
`docs/real-pr-validation.md` involves a retry.

A changed loop is now a retry only when what it iterates over or stops on, or its symbol's name,
says so: `attempt`, `retry`, `timeout`, `sleep`, `backoff`, or `deadline`. The words come from a
probe of the loop's target and iterable, or of a `while` loop's test, built from the AST like the
other probes and read from the loops that changed. `limit`, a retry term in the other rules, does
not count here: every loop has one, and `while len(payload) > limit` shrinks a payload until it
fits. Any other changed loop is an `unknown_semantic_change` under the new `SDW-RULE-UNKNOWN-LOOP`,
with the unknown condition's 0.55 baseline and assumption. A `while` loop's test was already a
condition too, so its stop condition is still classified as one: a changed `while attempts < 3` is
a retry boundary either way.

`test_complete_taxonomy_rules` classified `range(2)` to `range(3)` as a retry. It now classifies
`for attempt in range(2)` as one and `for item in items` to `for item in rows` as an unknown change.

Measured on every commit against its first parent, against the analyzer with the boundary
correction below. This repository's history holds 54 loop classifications and
`adaptive-policy-compilation`'s 45. Every one was a retry, none of them is one, and every one is
now an unknown change; the last to go, on this repository, was the `while len(...) > limit`
truncation in an old `_evidence_payload`. No other classification moves. Findings fall from 1,353
to 1,306 and from 3,056 to 3,014, obligations from 672 to 637 and from 442 to 403, and no range's
highest risk rises; one falls. On the 14 merges of `docs/real-pr-validation.md` the three loop
findings become unknown changes, and the findings fall from 71 to 68. The corpus's one loop, in
its `retry predicate` case, reads `attempts` and is still a retry, so the 18 goldens are unchanged.

## Output-affecting corrections (2026-10-31, boundary comparisons)

`SDW-RULE-BOUNDARY` made every changed comparison a `boundary_change` without reading its operator,
so identity and membership tests counted: of the 13 boundary findings in the merge-base run of
`docs/real-pr-validation.md`, two were boundaries. The condition rule had read operators from the
AST since 2026-10-05, but it counted every `==` and `!=`, and it asked whether any value its delta
showed compared, changed or not, so an unchanged `len(pending) >= STOP_AFTER_FAILURES_IN_A_ROW` made
a `recorder is not None` added beside it a boundary.

A boundary is what the category's obligations test: a value just below it, at it, and just above
it. An ordering has those, and so does an equality or inequality with a number written in the code,
signed or not, or with a `len()`: `count == 0`, `offset != -1`, `index == len(items)`. An equality
between other values picks one case out of many and orders nothing — `method == 'GET'`,
`ch.type == token.STAR`, `token == expected` — and identity and membership tests never were
boundaries. Both rules now read this from the AST operators and operands of the values the delta
changed. A comparison that is no boundary is classified the way a condition comparing none is: by
its authorization, validation, and retry terms, or as an `unknown_semantic_change` under the new
`SDW-RULE-UNKNOWN-COMPARISON`. A changed `if parent is None` is therefore one finding, where it was an
unknown condition beside a boundary comparison.

Both rules also read their keywords from the changed values and the symbol's name, no longer from
every value shown. That was harmless while the boundary check ran first and caught most changed
conditions; without it, an unchanged `row['valid']` made a `recorder is not None` added beside it a
validation guard, and an unchanged `owner in module.symbols`, where `owner` is the class a method
belongs to, an authorization one. Calls and assignments still read every value their delta shows,
the limit the 2026-10-19 entry records.

Two tests pinned the old reading. `test_a_comparison_redaction_erased_from_the_text_still_counts`
held the 2026-10-05 redaction fix on `token == expected`; it now holds it on `token == 0`, which
redaction displays as `token =[REDACTED] 0`, and `token == expected` is an unknown change. The
quoted-code case of `test_classification_reads_code_not_prose` expected a boundary from
`'boundary' in kinds(...)`, a membership test; it is an unknown change, and the case still pins
what it is for, that the quoted `limit` is no retry. One consequence is deliberate:
`user.role == 'admin'` was a boundary at impact 64, ahead of the rules that read names, and is an
authorization guard at 92. A delta built without a probe keeps the text scan, which counts every
equality.

Measured on every commit against its first parent, with the analyzer of `main` and of this change.
This repository's 111 pairs, one over the changed-line bound either way, hold 1,594 evidence
classifications, and 169 move: 152 from `boundary_change` and 9 from `retry_timeout_change` to
`unknown_semantic_change`; 5 from retry to boundary, where what changed were size limits, a batch
index, and a materiality threshold while the retry words sat in unchanged checks of a `deadline` or
of `attempts`; and 3 to `validation_change`, in functions named for validating or accepting.
`adaptive-policy-compilation`'s 541 pairs hold 3,186, and 86 move: 80 from boundary and 5 from
retry to unknown, and 1 to validation, again by its function's name. Findings fall from 1,434 to
1,353 and from 3,112 to 3,056, obligations from 764 to 672 and from 511 to 442, and no range's
highest risk rises; two fall.

On the 14 merges of `docs/real-pr-validation.md`, 16 of 90 classifications move from boundary to
unknown. The boundary findings fall from 13 to 2, a version gate and the truncation in
`_make_default_short_help`, both boundaries, and the findings from 78 to 71. Their unknown share
rises from 37% to 47%: what was called a boundary is now called what static analysis can tell of
it. The 18 corpus goldens are unchanged, since every comparison in them is an ordering.

## Anthropic requests carry no sampling parameters (2026-10-25)

SDK 2.0 of `anthropic` sends API version 2026-10-21 by default, and on it a Messages request that
carries `temperature`, `top_p`, or `top_k` returns HTTP 400 on every model. The adapter sent
`temperature` 0.1 through `extra_body` to the families that still honored it on 2023-06-01, so
under SDK 2.x every call to them would have failed and collapsed into deterministic fallback, the
failure mode recorded under *Anthropic request shaping is per model family*. CI could not show it:
the extra is absent from the development environment, so no test runs the SDK itself.

The value is no longer sent on any SDK release. Sending it only through releases before 2.0 would
make the same call sample differently depending on which SDK is installed, and pinning
`anthropic-version: 2023-06-01` on those calls would keep a second request shape alive for the
oldest models only, on a version the SDK no longer sends. Those families, Claude 4.6 and earlier,
now sample at their default temperature, 1.0, instead of 0.1. Their output is still
schema-constrained and checked against the evidence, and the default model, `claude-opus-5`, never
received the value.

Two newer families were shaped wrongly for thinking, with the same silent fallback. Claude Opus 5.5
rejects `{"type": "disabled"}` at every effort level, so it is sent no `thinking` parameter, like
Fable and Mythos. Claude Sonnet 5.5 rejects it too and is sent `{"type": "between_tools"}`, which
turns its thinking off and which no other family accepts. Claude Haiku 5.5 rejected only the 0.1
temperature.

The extra's floor rises from 0.40 to 0.77, the first release whose `messages.create` accepts
`output_config`; below it, every call raised `TypeError` before it was sent. All of this was
checked offline against a local stub server with releases 0.76.0, 0.77.0, 1.17.0, and 2.3.0,
recording the version header and request body each one sends. The deterministic analysis never
reaches the adapter, so no golden output, schema, taxonomy value, error code, or evidence ID
changes.

## Output-affecting corrections (2026-10-19, modules that are only a docstring)

An added module was a `symbol_added` finding whenever its diff had a hunk, even with nothing but a
docstring. Every package of `adaptive-policy-compilation` opens with an `__init__.py` that only
says what the package is for, and each was reported as "New behavior becomes reachable", a
medium-risk unknown change, beside the package's real functions. An empty `__init__.py` never was,
since its diff has no hunk.

An added module whose top-level code, without its definitions, is only a docstring, comments, or
nothing is now no change either. Its functions and classes are symbols of their own and are
reported as before. A removed one still is, since what imported it now fails, and so is an added
module that runs anything, an import included.

Measured on every commit against its first parent. On `adaptive-policy-compilation`, 16 findings
go, one per package's `__init__.py`, and no risk score or obligation count moves. Two of its
ranges at the 100-symbol bound each fit one more real symbol, `students/score.py`'s `on_test` and
`blind/score.py`'s `verdicts`, which the bound had cut. This repository's 102 pairs are unchanged,
and so is the 18-case corpus.

## Output-affecting corrections (2026-10-19, the GitHub Action's base revision)

The action's `base-ref` defaulted to `github.event.pull_request.base.sha`, and the analyzer
compares two trees as they are. That commit is the base branch as it stood when the pull request
opened, not where its head branched off, as Ruff found for its own change detection
(astral-sh/ruff#16857). Commits the base branch gained before the pull request opened read as
reverted by it, and once the head merged the base branch, the commits it brought in read as the
pull request's own. The skill passes a merge-base for the same reason; see the Claude Code plugin
entry.

Unless `base-ref` is set, a step now resolves the base as `git merge-base` of the base branch as
checked out, `refs/remotes/origin/<base>`, and the head. `fetch-depth: 0`, which the action
already requires, fetches every branch; if that ref is still missing, the event's base commit
stands in, which covers a base that moved on but not one merged into the head. An explicit
`base-ref` is compared as given, and an event without a pull request still fails asking for one.
`tests/contract/test_github_action.py` runs the step's own script on both histories, the fallback,
an explicit base, and the two failures. The analyzer is unchanged, and so is the 18-case corpus:
what changes is the range an action run reviews.

## Output-affecting corrections (2026-10-19, pooled candidates from several test files)

Behaviors that share an obligation pool their candidate tests, which are cut to five, and among
equal scores the cut went by path. On `adaptive-policy-compilation` `06cb75e..65f79f7`, the
`CODE_ROOTS` change shares its obligation with 37 findings of a new package. Every candidate
scored 0.60, so all five places went to `tests/test_escalations.py`, and the two tests reading
`CODE_ROOTS`, found by name since the entry below, never showed.

Among equal scores the test files now take turns: each file's first test in the old order, then
each file's second, and so on. A lower score still never comes before a higher one, and only
pooled lists are reordered. The range's obligation now shows three escalation tests and both
tests of `CODE_ROOTS`.

Measured on the same pairs as the entry below, against it. On `adaptive-policy-compilation`, 65
of 493 obligations show other tests and 18 only reorder them, and the distinct test files in
those 65 lists go from 100 to 187. On this repository, 184 of 726 show other tests and 37 reorder
them, with test files going from 490 to 723. No finding, score, or coverage status moves, and the
18-case corpus is unchanged.

## Output-affecting corrections (2026-10-19, tests that read a changed module variable)

A change to a module's top-level code has the symbol `<module>`, which no test can name, so its
candidates were only evidence about test files, a mirrored name or an import, shared by every test
in a file and shown as the file. On `adaptive-policy-compilation`, adding `"escalations"` to the
`CODE_ROOTS` tuple in `harness/provenance.py` got `tests/test_provenance.py` as a whole file,
although two of its tests read the tuple, `TestCodeDigest.test_does_not_include_the_tests` and
`TestEveryWriterRecordsEnvironment.test_no_JSON_writer_is_left_without_env`, and
`tests/test_writer_lists.py` holds a third.

The AST stage now records what each assignment writes to: the names it binds or unpacks into, or
the variable whose item or attribute it sets. A changed assignment delta carries the variables of
the assignments that differ, `CODE_ROOTS` alone where the delta shows `REPO`, `CODE_ROOTS`, and
`DIGEST_CHARS`. The mapper looks those variables up as it looks up a symbol's name. A test whose
body uses one as a whole identifier, or whose name spells it, has evidence of its own —
`changed module variable in bounded test body` (0.10) or `changed module variable in test name`
(0.20) — and is listed by name, while the rest of its file still stands as the file. As for a
symbol, a mention only adds to a test the file already ties to the change, so a test reading a
variable of the same name from another module is no candidate.

Measured on every commit against its first parent, comparing each finding's candidates. On
`adaptive-policy-compilation`, 41 module findings named the variables they changed; 31 gained 86
named tests, the three for `CODE_ROOTS` each time a package joined the tuple among them, and
obligation lists changed in 12 of 417 pairs. On this repository, 77 module findings named
variables and 31 gained 55 tests: `__version__` finds
`test_the_declared_version_matches_the_packaging_metadata`, `EXCLUSION_PHRASES` finds
`test_every_exclusion_reason_the_pipeline_emits_has_a_phrase`, and the scoring weights find
`test_every_risk_score_is_the_stated_combination_of_its_axes`. Five risk scores fell, each where a
module change without a candidate gained one, and obligation lists changed in 13 of 100 pairs. No
other finding's candidates moved, and the 18-case corpus is unchanged.

## Output-affecting corrections (2026-10-19, assignments named for state)

`SDW-RULE-STATE` made every changed assignment a `state_transition_change`, whatever it assigned.
On `adaptive-policy-compilation` `06cb75e..65f79f7`, adding `"escalations"` to the `CODE_ROOTS`
tuple in `harness/provenance.py` became a state transition at 86% confidence. The category's
impact of 78 made it the range's overall risk, 51 where every other finding scored 42, and its
three obligations led the brief: verify an allowed transition, reject an invalid one, and repeat
it, none of which a module constant has. The corpus never showed this, since its one assignment
case assigns a local named `state`.

An assignment is now a state transition only when what it writes to, or the symbol it is in, is
named for state: `state`, `status`, or `transition`, singular or plural, as whole words the way
the keyword rules split names, so `order_status` and `currentState` count. Any other is an
`unknown_semantic_change`, "An assigned value changed without enough local contract context",
on the footing of a condition no keyword explains: a 0.55 baseline and the assumption that
whether the value is state, configuration, or data is unavailable to static analysis. The words
come from a probe of the assignment's targets, built from the AST like the other probes, so
neither prose in the value nor a value that merely names state counts: `get_state()` reads state
and `RandomState(1)` makes a random generator, and neither moves one.

Measured on every commit against its first parent. `adaptive-policy-compilation`'s 417 pairs on
`origin/main` held 197 assignment classifications, all state transitions before; 194 are now
unknown changes and 3 stay, each on a word rather than a state machine: a `status` holding
`git status` output, a search's `_prefix_states`, and a verdict label named `state`. This
repository's 98 pairs, one of them over the changed-line bound either way, held 329; 310 move and
19 stay: 7 on the pipeline's `state`, 7 on coverage status, from `coverage_status` to the
`CoverageStatus` enum, 2 on the `status` field of `InterpreterResult`, and 3 on git's
`name_status`. No other classification moved, none of the moved ones was a state machine, and
every risk score that changed fell: in 36 and 43 pairs respectively, with obligation counts
changing in 47 and 50.

`06cb75e..65f79f7` now scores 42, and its four obligations become one. That obligation is the one
every unknown change of the range shares, so `tests/test_provenance.py`, the `CODE_ROOTS`
change's candidate at 0.50, is pooled with the escalation tests at 0.60 and cut by the
five-candidate cap — the pooling the merged-obligation entry below describes, not this rule.

A new corpus case, `module constant`, pins a module-level constant as an unknown change. The
other 17 goldens are unchanged, and precision and recall stay at 100%, now 16 of 16. One limit
remains: the words are the targets of every assignment the delta shows, so an unchanged
`state = ...` beside a changed `count = ...` still makes the change a state transition, as the
condition and call rules read every value they show.

## Output-affecting corrections (2026-10-14, tables of functions and tests that only load)

`_classify_condition` was led by `test_every_condition_site_records_its_probe`, which extracts
symbols and never classifies a delta, ahead of
`test_the_condition_rule_reads_comparisons_from_operators`. Both import `semantic_candidates`,
both share `condition` with the change, and both reached it the same way. `classify_delta` runs
each classifier of `CLASSIFIERS`, a module-level tuple, so no call names `_classify_condition`,
and the call graph reached it only through the module's top-level code, which names it in the
table. Loading the module reached it for every test importing it, and the order fell to names.

Two changes tell them apart. The graph follows a table of functions: code using a module-level
variable uses what the variable's value names without calling it — the items of a tuple, list,
set, or dict, the variables and imports it copies, and the arguments of a call in it, as
`partial(check, strict=True)` hands over `check` — so `classify_delta` reaches each classifier
through a call. What a call in the value calls is left out, because the call ran when the module
loaded: counted, `tuning_repo = JsonTuningRepository(path)` had every handler reading
`tuning_repo` call the class and its `__init__`, and handlers' tests took over the constructor's
list. A variable's annotation is left out too, as it names a type rather than a value. And among
equal scores and equally weighed shared words, a test reaching the symbol through calls — the
graph's callers, which never pass through a module's top-level code — comes before one that only
loads a module naming it, which may never run it.

That key comes after the shared words, not before them. Placed first, it put `meal-manager`'s
`dii_finalize` tests, which reach the `DishRepository` and `FridgeRepository` protocols through
functions annotated with them, ahead of `test_add_dish_dict` and the tests sharing the
protocols' words.

Measured: no score, reason, or risk score moves. `distiller` `main..skeleton` and
`meal-manager`'s `8894749` are unchanged. `v0.2.0..HEAD` over `src/**` changes the five tests
kept in 6 of 122 lists, all repository constructors: `JsonDishRepository.__init__` is led by
`test_dish_repo_restore_adds_back`, `JsonHistoryRepository.__init__` by
`test_history_corruption`, and `JsonFridgeRepository.__init__` keeps
`test_fridge_entries_grammar` and `test_fridge_mutations_preserve_expiry`, which make a
repository, in place of tests using the one the package makes on import. This repository's
`v0.3.0..2f3840a` reorders 13 of 157 lists and changes the five kept in 9 of them:
`_classify_condition` is led by `test_the_condition_rule_reads_comparisons_from_operators` in
each of its four categories, `verify_wheel._installed_script` by
`test_main_installs_only_after_both_archives_pass`, the other checks of `verify_wheel` keep the
tests reaching them through `main` in place of tests calling only another check, and
`_classify_symbol_lifecycle` trades the performance test that only runs the AST stage for tests
that build candidates. The 17-case corpus is unchanged.

## Output-affecting corrections (2026-10-14, weighing the shared words)

The words a test's name shares with a change ordered tests that reach it alike, each counting
once, so one generic word weighed as much as one that singles the change out:
`JsonDishRepository.__init__` was led by `test_atomic_write_json_cleans_up_on_failure`, sharing
`json`, ahead of `test_add_dish_dict`, sharing `dish`.

What makes `json` generic there is the change's neighbours: it is in the name of all five
repositories of `meal-manager`'s `repositories` package, and `dish` in one of them and its
protocol. Counted over the whole repository, the frequencies point the other way: `json` is in 4
test names and 59 symbols, `dish` in 34 and 75, so a frequency over tests or over every symbol
would have weighed `json` more. A shared word now weighs by an inverse document frequency over
the symbols of the modules in the changed file's directory that the call graph read,
`log((1 + symbols) / (1 + symbols naming the word))`: a word every neighbour names weighs
nothing, and without the graph every word still counts once.

Weighed so, a common verb that names a function weighed most, since symbols rarely share it
while test names, written as sentences, often do: `CallGraph._uses` was led by the mapper's tests
whose names say "uses". A function named by one word now adds no word to the subject, as the
name signal holds that such a word turns up in test names about anything; a class named by one,
a noun such as `Dish`, still does.

Measured: no score, reason, or risk score moves. On `meal-manager`'s `8894749` no list changes
which five tests it keeps, and `IngredientSessionStore._sweep_candidates` is led by
`test_dii_store_ttl_and_recovery`. `v0.2.0..HEAD` over `src/**` changes 6 of 122 lists:
`_sweep_candidates` is led by `test_dii_orphan_sweep_takes_the_oldest_backup`,
`JsonDishRepository.__init__` by `test_add_dish_dict`, and the alias, history, and tuning
repositories keep their own tests instead of the atomic-write ones. This repository's
`v0.3.0..2f3840a` changes 17 of 157 lists: `snapshot_symbol` and `SymbolSnapshot` are led by
the test of a snapshot without probes, `class_display` by the test of the class shell, and
`_classify_condition` by `test_every_condition_site_records_its_probe`, while `CallGraph._uses`
keeps the graph's tests. On `distiller` `main..skeleton`, `report.run` stops crediting
`test_evaluate_gets_its_options_as_run_args` for `run`. The 17-case corpus is unchanged.

## Output-affecting corrections (2026-10-14, order among tests reaching a change alike)

Candidates with equal scores are ordered by whether the test's body names the symbol, then by
path and name. Most tests that reach a change only through other calls score alike, an import
and a reach at 0.40, or a reach from another module at 0.35, and none names the symbol, so the
five kept were the first five paths and names among dozens: `meal-manager`'s
`_sweep_stale_temps` was led by `test_alias_repository`, and `DishRepository` by whichever
integration test sorted first. Category words had ordered them until the previous section, and
were measured as an order of their own and left out, because they describe a test's topic: they
put a session test first for `DishRepository`.

A test whose name or file shares words with what changed is likelier about it. The changed
symbol's subject is the words of its class, its function, and its module, a package's own for
an `__init__.py`, split at underscores and capitals, without words of two letters, a short list
of words that say nothing, and dunders or `<module>`: `JsonFridgeRepository.load_entries` in
`json_fridge.py` reads json, fridge, repository, load, and entries. Among equal scores, after a
test naming the symbol in its body, the test sharing more of those words with its name and its
file's comes first; category words break what is left, then path and name. The order changes
alone: no score, reason, or risk score.

Measured: no risk score moves on any range. On `meal-manager`'s `8894749`, 41 of 91 lists change
which five tests they keep and 20 first candidates change: `_apply_target_mode` is led by
`test_atomic_write_preserves_file_mode`, `_sweep_stale_temps` by
`test_atomic_write_sweeps_stale_temps`, `_merge_expiry` by
`test_merge_ingredient_alias_keeps_expiry`, `DataDirLock`'s methods by
`test_data_lock_configure_rejects_while_held`, `JsonTuningRepository.load` by
`test_online_weight_tuning`, and `finalize_session`'s error handling by
`test_dii_finalize_rollback` again. `v0.2.0..HEAD` over `src/**` changes 52 of 122 lists and 31
first candidates, `HistoryRepository` led by `test_list_cooking_history` and
`TuningRepository` by `test_online_weight_tuning`. One shared word still decides alone where
another would have been better: `JsonDishRepository.__init__` gets
`test_atomic_write_json_cleans_up_on_failure`, which shares json, before `test_add_dish_dict`,
which shares dish, and the category's words break that tie. This repository's `v0.3.0..2f3840a`
changes 92 of 157 lists and 62 first candidates, `_group_overflow` led by the test of a grouped
overflow obligation, `_mirrored` by the test of a file naming a module without its underscores,
and `build_test_index` by `test_test_index_batches_reads_and_honors_exclusions`. On `distiller`
`main..skeleton`, `report.run` is led by `test_report_gets_the_record_files`, and
`test_evaluate_gets_its_options_as_run_args` enters the five kept for `evaluate.run`. The
17-case corpus is unchanged.

## Output-affecting corrections (2026-10-14, category terminology and direct tests)

A test whose words match the change's category gains 0.10, "behavior-category terminology". The
words confirm a test tied to the change itself, and a test that reaches the symbol only from
another module had already stopped gaining them, because they describe whatever that test is
about. A test that imports the module and reaches the symbol through other calls still gained
them, and a test calling the symbol without such words could not catch up: a reach replaces a
mention, so a direct call and an indirect reach both score the import and the reach. On
`meal-manager`, the unit tests reaching `atomic_write_json` through a repository's `save`, whose
words were about errors, scored 0.50 in its error-handling list, and its own tests
`test_atomic_write_preserves_file_mode` and `test_atomic_write_sweeps_stale_temps`, at 0.40, fell
out of the five kept.

The words now count for a test tied to the change: named after the symbol or its module, calling
the symbol itself, or tied to it only by its file, as a mirrored or importing test file standing
for all of its tests is. A test calls the symbol itself when the call graph starts it there,
naming it in a module it imports, through a same-file helper too, or when it calls it as a method
on an instance of a class it makes or names itself, or reads from a module-level variable:
`repo = JsonFridgeRepository(path); repo.load_entries()`. A test that reaches the symbol
through other calls is about what it calls and gains nothing from its words.

The mention in a test's body was not used as the call, though it decides ties: it reads the
test's text, strings included, and a message saying "resolve to the same commit" or a path
ending in `__init__.py` kept the words for end-to-end tests over `CallGraph._resolve` and
`CallGraph.__init__`, above the graph's own tests. Calling a method on an instance whose class
the test reaches only deep inside what it calls is no direct call either: `.resolve()` on a path
had made the same tests call `_resolve`. Ordering the indirect tests by their words, without
scoring them, was measured and left out: it kept some first candidates the words had chosen, such
as `test_dii_finalize_rollback` for `finalize_session`'s error handling, and put a session test
before the dish test leading `DishRepository`, for the reason the words are not counted at all.

Measured: no risk score moves on any range, since no candidate crossed 0.60 by its words alone.
On `meal-manager`'s `8894749`, 18 of 91 lists change and 7 first candidates. `atomic_write_json`'s
error-handling list keeps both of its own tests; `load_entries` is still led by
`test_fridge_unreadable_file_degrades_quietly`, which calls it on a repository it makes;
`DataDirLock.__init__` stops being led by `test_calculate_score_basic` and
`update_fridge_inventory`'s `_fmt` by a cooking-history test, and `finalizer.commit` is led by its
unit test instead of an integration test. `v0.2.0..HEAD` over `src/**` changes 17 of 122 lists.
This repository's `v0.3.0..2f3840a` changes 63 of 157 lists and 39 first candidates, mostly to a
test that calls the symbol, as for `compare_symbol`, `score_risk`, `classify_delta`, and
`map_candidate_tests`, and otherwise among tests that reach it indirectly, now in the order of
their paths and names. `distiller` `main..skeleton` and the 17-case corpus are unchanged.

## Output-affecting corrections (2026-10-14, a package's `__init__.py`)

Candidate mapping named a changed file by its path, so a package's `__init__.py` was
`shop.__init__`. No test importing `shop` counted as importing it, a `test_init.py` file mirrored
every `__init__.py` in a matching directory, and the module never named a test, since `__init__`
reads as private. A package's `__init__.py` is now named like the package's own file, `shop.py`,
for the import, the mirrored name, and the module name in a test's name, and the repository's own
`__init__.py` is the root package, `""`.

Named so, the import rule that matches a module and the symbols imported from it,
`imported.startswith(f"{module}.")`, made importing any module of a package an import of its
`__init__.py`: the previous section measured that, and left it out. Python does run a package's
`__init__.py` before any of its modules, as loading a module runs what it imports, which no test
counts. So a test imports a package's `__init__.py` when the call graph locates one of its imports
there: the package itself, as `from shop import total` and the `meal-manager` tests' `.src` load
do, or a name the package binds, re-exports included; `src.repositories.json_fridge` locates in
its own module, and does not. Without a graph nothing imports a package's `__init__.py`, as before.

Measured: on `meal-manager`'s `8894749`, 14 of 91 lists change and 4 risk scores fall.
`test_register_wires_every_tool` is now a strong candidate for the plugin's `register`, at 0.60.
`atomic_write_json`'s own test becomes a strong one, its risk falling from 42 to 38, and the unit
tests that load `src` and write through it replace the dish tests reaching `_apply_target_mode`
from afar. The top-level code of `src`, `src/handlers`, and `src/repositories` gains the test
files that load each package, falling from 57 to 51. The overall falls from 57 to 56.
`v0.2.0..HEAD` over `src/**` changes 15 of 122 lists and also strengthens
`test_data_lock_configure_rejects_while_held` for `repositories.configure`, from 48 to 43. Among
tests that import and reach `atomic_write_json`, those whose words match a category still outrank
its direct tests in that category's list, as they would for any module. This repository's
`v0.3.0..2f3840a` changes one list, `ast_diff/__init__.py`'s top-level code gaining the test files
that import the package, from 57 to 51; `distiller` `main..skeleton` and the 17-case corpus are
unchanged. A first draft named the root `__init__.py` `""` without guarding the path it builds,
and a change to that file failed the analysis; the integration suite now covers it.

## Candidate tests that load their own package (2026-10-13)

`meal-manager`'s tests load the plugin package they live in by their directory's name:
`_PLUGIN_DIR = Path(__file__).resolve().parent`, then
`_pkg = importlib.import_module(_PLUGIN_DIR.name)`, and `test_register_wires_every_tool` calls
`_pkg.register(ctx, data_dir=...)`. The name is known only at run time, so the index dropped the
load, although it already resolved relative loads such as `.src.dish` against the same package.

The index now reads the name of the test file's own directory as the test's own package: the
`.name` or `os.path.basename` of `Path(__file__).parent`, `os.path.dirname(__file__)`, or the same
made absolute or resolved, through module-level variables that hold them, and `__package__`. That
package is the one the call graph would name the directory, `""` for a test at the repository's
root, which is the plugin's own `__init__.py` when it is a package. A module-level name is now a
constant whenever its value reads as one, assigned in order, and forgotten when assigned again
with anything else. The graph reads a dotted name that starts with a dot as relative to that root
package: `.register`, what `_pkg.register` resolves to, is a top-level module of that name if one
exists, and otherwise what the root `__init__.py` binds, here its `register`.

A test that imports a package does not count as importing its `__init__.py`, whose module name
keeps the `__init__`, so `test_register_wires_every_tool` reaches `register` from afar, at 0.55
with its name, rather than through an import, which would make it strong. Naming an
`__init__.py` by its package was measured and left out: importing any module below a package
then counts as importing the package, every test file importing one became a candidate for
changes to the package's top-level code, and integration tests with category terminology
outranked the unit tests of `atomic_write_json`, for four risk scores on `meal-manager`'s
`8894749` and one on this repository.

Measured: taking `meal-manager`'s `register` as changed, its candidate goes from none to
`test_register_wires_every_tool`. `meal-manager`'s `8894749` and `v0.2.0..HEAD` over `src/**`
are unchanged, because that test is the only one to load the package, and it reaches the same
code through `_repos_mod.configure` and `_handlers_pkg.iter_tools`. `distiller` `main..skeleton`,
this repository's `v0.3.0..2f3840a` — the range the previous sections measured, now that `main`
exceeds the 3,000 changed-line bound — and the 17-case corpus are unchanged.

## Output-affecting corrections (2026-10-13, instances a test holds)

A test that reaches a class may hold an instance of it and reaches the methods it calls on one by
name. Reaching a class counted any path, loading a module included, so a module that makes an
instance on import handed one to every test that loads it: once `meal-manager`'s tests related to
its modules, each of the 81 integration tests importing `dii` held the session store that
`dii/__init__.py` makes, and `.get` on a dictionary in `test_add_dish_with_instructions` reached
`IngredientSessionStore.get` and the session parsing behind it.

A test now holds an instance of a class it reaches through calls that load no module, which
`CallGraph.callers` follows, or of a module-level variable it reads, by a name its file binds or a
chain rooted at one: `_dii_mod._store` holds what `dii/__init__.py` assigns it. Production code is
followed as before; a function that calls `_store.get()` still knows its variable. For those chains
the index records each test's attribute chains, with its same-file helpers', and what the file's
`import` statements bind beside what it loads by name, so `_repos_mod.JsonFridgeRepository(...)`
starts the test from the class `src/repositories/__init__.py` re-exports.

Measured: on `meal-manager`'s `8894749`, 19 of 91 lists swap 44 candidates and 2 first candidates
change, with no risk score moving: the session helpers are reached by the `dii` tests instead of
the dish tests, `JsonTuningRepository.load` by the tests that register cooked meals instead of the
`dii` ones, and `tuning.validate_state` by the shopping-list test, whose handler loads the tuning,
instead of `test_edit_dish_empty_rejected`. `v0.2.0..HEAD` over `src/**` swaps 29 candidates in 14
lists. This repository's `v0.3.0..main`, `distiller` `main..skeleton`, and the 17-case corpus are
unchanged.

## Candidate tests that load modules with `importlib` (2026-10-13)

`meal-manager`'s tests run as scripts from the plugin's directory, so they cannot import its
package by name, and load every module with `importlib`:
`_dish_mod = importlib.import_module(".src.dish", _PLUGIN_DIR.name)`, then `Dish = _dish_mod.Dish`,
and `_load_handler("add_dish")`, a helper that loads `f".src.handlers.{module_suffix}"` and returns
its `HANDLER`. The index read only `import` statements, so none of its tests related to any
module, and the graph had no test to follow.

The index now reads `importlib.import_module` calls, written either way, whose name the file
writes out: a literal, a module-level string constant, or an f-string or concatenation of those,
followed 16 levels deep. A same-file function that loads a name built from its parameters is
evaluated at each call that passes them written out, and a name known only when the test runs
loads nothing the index can tell. A relative name resolves against the package the call names,
or, when that too is known only at run time, against the test file's own package, the one
`Path(__file__).parent` names. The file's assignments bind names to what they load:
`dish_mod` to `src.dish`, `Dish = dish_mod.Dish` to `src.dish.Dish`, and `add_dish` to what the
helper returns, `src.handlers.add_dish.HANDLER`. A test that uses such a name starts from its
symbol, as it starts from what it names in a module it imports.

Each test used to import everything its file imports anywhere, so two of `meal-manager`'s 83
integration tests loading `dii.store` and `dii.session` inside their bodies made all 83 import
them, and `.get` on a dictionary in any of them reached `IngredientSessionStore.get`. An import
inside a test function, a statement or a load, now belongs to that test; what the file imports
outside test functions, helpers included, still belongs to every test in it.

Measured: on `meal-manager`'s `8894749`, 83 of 91 candidate lists change and 60 findings gain
their first candidates; 323 candidates are added and 1 removed, displaced from the five kept for
`register_cooked_meal`'s `HANDLER` by the tests named after the handler, which now import and reach
it, at 0.85 instead of 0.45. 83 risk scores fall, most by five or six points and eleven by eleven
or twelve, where a finding with no candidate gains a strong one; the overall falls from 62 to 57,
and three findings go from high to medium. `v0.2.0..HEAD` over `src/**` changes 108 of 122
lists, 83 gaining their first candidates, and 106 risk scores fall, the overall from 62 to 57.
Keeping imports to their test alone accounts for 14 and 21 of those lists, which swap 40 and 67
candidates: `session.from_dict` stops being led by `Dish.from_dict`'s tests. This repository's
`v0.3.0..main` changes 2 lists, which swap 3 candidates among tests that imported `path_policy`
inside other tests' bodies, and no risk score; `distiller` `main..skeleton` and the 17-case corpus
are unchanged.

## Output-affecting corrections (2026-10-13, attributes of `self`)

A class's members know whatever any of them knows, so that `self.cart.total()` in one method
reaches what `self.cart = Cart()` in `__init__` holds, and an attribute any member reads was
looked up on every class so known. Once `from .. import data_lock` resolved, each of
`meal-manager`'s repositories knew the `DataDirLock` it keeps as `self.lock`, and every
`self.path` they read, their own data file, reached the lock's `path` property: 115 symbols
reached it, where 34 had before the lock resolved.

An attribute read directly on a method's own instance or class — its first parameter, `self` or
`cls`, unless it is a static method — names a member of that class or of a class it inherits from,
never of another class it knows: Python finds it there, or in the instance's own attributes, which
are not methods. Module references now record those attributes apart, and the call graph looks
them up only along the class's bases, still following what the methods they reach return, so
`self.key().turn()` reaches what an inherited `key` returns. Every other attribute is looked up as
before: `self.lock.path` reads `path` on whatever `self.lock` holds, the lock included.

Measured over `meal-manager`'s head with every module read, the graph loses those 19 edges, from
each repository method to `DataDirLock.path`, and gains none: the property is reached from 46
symbols, among them the modules that now load the lock, the repositories' `configure`, and the
plugin's `register`. This repository's graph keeps all of its 1,214 edges. Candidate tests and risk scores are unchanged on
`distiller` `main..skeleton`, on `meal-manager` (`8894749`, and `v0.2.0..HEAD` over `src/**`), on
this repository's `v0.3.0..main`, and on the 17-case corpus.

## Output-affecting corrections (2026-10-13, a `src` package)

The call graph names a module as code imports it, and drops a leading `src` or `lib`, the
directory a `src` layout keeps its packages in: `src/distiller/cli.py` is `distiller.cli`.
`meal-manager`'s `src` is a package itself, with an `__init__.py`, which the plugin's own
`__init__.py` imports as `.src`. Its modules were named without `src`, so `src/__init__.py` was
`""`, a name the plugin's `__init__.py` held first, and `from .. import data_lock` in
`src/repositories/__init__.py`, which climbs to `src`, bound `data_lock` to nothing the graph
could find. The lock that `src/filelock.py` makes and `src/__init__.py` re-exports stayed out of
reach, and so did every call on it.

A `src` or `lib` with an `__init__.py` now keeps its name, in the modules' names and in the
packages their relative imports resolve against: `src/dish.py` is `src.dish`. Its modules are also
found by their names without it, after every other name, as code that puts `src` on its path
imports them, so no lookup that resolved before stops resolving. A `src` layout, without an
`__init__.py` in `src`, is named as before.

Measured on `meal-manager`'s head with every module read: the graph gains 40 edges and loses none.
`DataDirLock.configure` is reached from the plugin's `register` and the repositories' `configure`
instead of from nothing, and the lock's locking methods from ten more symbols each. Candidate
tests and risk scores are unchanged on `meal-manager` (`8894749`, and `v0.2.0..HEAD` over
`src/**`), whose tests load modules with `importlib`, on `distiller` `main..skeleton` and this
repository's `v0.3.0..main`, which have no `src` package, and on the 17-case corpus.

## Candidate tests through imported instances (2026-10-13)

A call on an instance was followed through the classes the calling code knows, and code knew an
instance another module holds only by its class. `meal-manager`'s `repositories/__init__.py` makes
one repository of each kind on import, `dish_repo: DishRepository = JsonDishRepository(path)`, and
every handler imports one and calls `dish_repo.load()`. A module-level variable is no symbol, so
neither the import nor the call resolved, and no handler reached a repository method.

Module references now record each module-level variable, to what its assignments put in it and
what its annotation names. What a value is, not every name it mentions: the class or function a
call calls, the name or chain it reads, and the items a container, a conditional, or a
comprehension holds, so `JsonDishRepository(path)` is a repository whatever `path` is, and
`dish_repo.load()` is what `load` returns. Assignments inside a module-level `if`, `try`, or
`with` count. A variable holds the classes such a value makes, what a function it calls is
annotated to return, what another variable it copies holds, and what a method called on one
returns. A symbol knows what the variables it uses hold, its own module's or ones it imports,
through re-exporting packages like any other name, and through chains such as
`stores.pantry.load()`.

This replaces a rule of the previous section: every symbol of a module knew what the module's
top-level code made, whether it used it or not, and now knows only what the variables it uses
hold. An attribute also stopped resolving to a name an import binds, since `order.Cart` reads
`order`, not the `Cart` imported beside it. Neither removed a single edge from the call graphs of
this repository or `meal-manager`.

Measured over `meal-manager`'s head with every module read, methods reached by some symbol go from
69 to 96 of 105, and the graph gains 127 edges and loses none: `JsonDishRepository.load` is reached
from 14 symbols instead of 3, and `JsonHistoryRepository.append_event` from `register_cooked_meal`
instead of from none. Its candidate tests do not change (`8894749`, and `v0.2.0..HEAD` over
`src/**`), because its tests load modules with `importlib`. `distiller` `main..skeleton` and this
repository's `v0.3.0..main` have no module-level instance of their own classes: their candidates,
risk scores, and graph edges are unchanged, and so is the pipeline's time. The 17-case corpus has
no test file and is unchanged.

## Candidate tests through calls on instances (2026-10-13)

The call graph followed a use only through a name it could resolve: one of the module's own
symbols, or one an import binds. `cart = Cart(); cart.total()` resolved `Cart` through the import
and stopped at `cart.total`, since `cart` is a local variable, so a method of a class from another
module was reached only by tests that imported its module and named the method.

The graph now records which symbols are classes, the bases each class names, the names in each
function's return annotation, and which of the names a symbol uses are attributes. A symbol knows
the classes it uses, wherever they live — making one, annotating with it, inheriting from it — and
those the functions it calls are annotated to return. An instance holds what its class inherits
and the instances its class body names, as a dataclass's fields do. A class's members know the
class and whatever any of them knows, since `self.cart = Cart()` in `__init__` holds what
`self.cart.total()` calls in another method, and every symbol of a module knows what the module's
top-level code knows, which covers a module-level instance. An attribute the symbol uses reaches
the method of that name on each class it so knows, and what that method returns is known in turn.
Only attributes count, as a method is called on an instance: a first version read every name, and
local variables named `ordered` reached `LineRange.ordered`.

Overrides are not followed down: a call on an object known as a `Cart` reaches `Cart.total`, not
a subclass's `total`, unless the subclass itself is known. Following every override, as class
hierarchy analysis does, would credit a test of one subclass with all of its siblings. A
`Protocol` is matched by structure rather than inheritance, so a call through one, as
`meal-manager`'s handlers make on their repositories, reaches only the protocol's stub, and the
graph does not see the methods a base class outside the repository calls by name, such as
`ast.NodeVisitor` calling `visit_Call`.

A test is followed the same way, one level deep: a test that reaches a class through its calls —
`api.make_invoice()` makes one — and uses a method's name as an attribute reaches the method, or
the one the class inherits, but what that method returns is not followed further. A test also starts from what it imports through a re-exporting
package, if it uses the name, since every test of the file shares the import: `from shop import
Cart` names the class in `shop/cart.py`. That needed import names as written. The index casefolded
them, and the graph located `shop.Cart` as the module `shop/cart.py`, which Python's
case-sensitive imports never do; both keep the case now, and matching a changed module by name
still folds it.

The classes a symbol knows are computed per symbol, as a closure over what instances hold and what
the methods it calls return. A first version repeated a pass over the whole graph until nothing
changed, one pass per link of a chain of methods each returning the next class: such a chain
across 500 modules, the graph's bound, took 57 seconds, and takes 0.6 now. The performance suite
covers it.

Measured: `distiller` `main..skeleton` and `meal-manager` (`8894749`, and `v0.2.0..HEAD` over
`src/**`) are unchanged: `distiller`'s one class has no methods, and `meal-manager`'s tests load
its modules with `importlib`, which a static graph cannot follow. This repository's `v0.3.0..main` changes 14
of 156 lists and one risk score: `StructuralDelta` falls five points, its first candidate now
`test_extracts_required_structural_delta_classes`, which reaches it through `analyze_ast`
imported from `ast_diff/__init__.py`. Thirteen of the fourteen come from imports followed through
re-exporting packages. The other comes from calls on instances, and is not better: three tests in
`tests/security` that reach `CoverageMap.status_for_lines` through `service` tie at 0.40 with the
map's own tests and precede them by path. Taking each of this repository's 94 methods as changed,
with up to 25 candidates, 70 have candidates instead of 68 — `ChangedFile.as_revision_pair`, which
`service` calls on each file of what `collect_diff` returns, gains its first — and the candidates
grow from 1,187 to 1,424, those reached only from afar from 75 to 227. The pipeline over this
repository takes the same 1.4 seconds. The 17-case corpus has no test file and is unchanged.

## Output-affecting corrections (2026-10-13, direct calls)

A mention of a changed symbol in a test's body and a reach through the test's calls were meant
as one piece of evidence, that the test exercises the symbol: a reach was "worth 0.10 like a body
mention and not added on top of one". Raising a reach from an imported module to 0.15, so that it
ranks above a reach only from afar, left two inversions. A test calling the symbol directly both
names and reaches it, and kept the mention, so it scored below the tests reaching the symbol
through other calls: `test_line_sets_are_judged_like_ranges`, which calls
`CoverageMap.status_for_lines`, was not among its five candidates. And a reach from afar, 0.35,
did add to a mention, so a test that never imports the module could outrank one that does and
calls the symbol. Following calls on instances, which a test makes by naming the method, would
have made that common.

A reach now replaces the mention wherever both hold. Adding the two instead would reward a direct
call more, on weights tuned for evidence that does not stack; measured, it moved 32 risk scores on
this repository's `v0.3.0..main` rather than 15. A direct caller then ties with the tests reaching the
symbol through other calls, and alphabetical order still left it out, so among equal scores a
test whose body names the symbol ranks first. A mention still scores 0.10 for a test the graph
does not show reaching the symbol.

Measured: on `distiller` `main..skeleton`, 5 of 11 lists change. `RunArgs` falls four points, its
named test now 0.60, `cli.main` is led by the test that calls it, and the test that runs
`python -m distiller` no longer leads `evaluate.run` and `report.run`, since the mention it scored
was `subprocess.run`. `meal-manager` (`8894749`, and `v0.2.0..HEAD` over `src/**`) is unchanged.
This repository's `v0.3.0..main` changes 80 of 156 lists and the first candidate of 43, mostly to
a test that calls the symbol: `build_call_graph` is led by a test of `test_call_graph.py` instead
of an end-to-end test of the Claude Code skill, and `CallGraph.resolve` by a test that builds a
graph instead of a `service` test whose message says "resolve to the same commit". 15 risk scores
fall four or five points, each because a test named after the symbol, importing its module, and
calling it became a strong candidate. The 17-case corpus has no test file and is unchanged.

## Candidate tests through other modules' calls (2026-10-13)

Reach stopped at the module a test imported. A test of `distiller`'s `cli.main` reached
`_parser` but not `messages.unexpected_failure`, which `main` calls, and a helper changed beneath
an unchanged module could not count a test reaching it through that module, because only
changed files were read.

`call_graph.py` now reads modules at the head revision: the ones tests import and the changed
ones first, then whatever their imports name, breadth first, until 500 modules, 8 MiB, or five
seconds, each under the AST stage's parse budgets. It keeps, for every symbol, the names and the
attribute chains rooted at a name that its code uses, and for every module what each name its
imports bind stands for, relative imports resolved against the package. A use reaches a symbol
in its own module by name, as before, and one in another module only through an import:
`messages.unexpected_failure` resolves through `from distiller import messages`, and a package
that re-exports a name, as `ast_diff/__init__.py` does, is followed to its definition. A name
that no import binds is not resolved to another module, so `store.get(...)` on some object
never reaches a `get` function the module happens to import. Loading a module loads what it
imports, so their top-level code is reached too, and a test starts from the modules it imports
and the `__main__.py` it runs. The references the previous section had the AST stage record
moved here, which reads changed and unchanged modules alike.

A first version scored a test that reaches a symbol only through other modules 0.35, on top of
the package import and category terminology. On this repository's `v0.3.0..main` that made
end-to-end tests strong candidates for nearly everything — `service.analyze` calls into every
stage, and static reach takes every branch — and moved 70 risk scores, so a test of the MCP
transport became the first candidate for `obligations._group_overflow`. Call distance did not
separate them: integration tests calling `analyze` are two calls from it. What did was weight.
Reaching a module through another is what importing its package could only suggest, so the
package import no longer adds to such a test, and category terminology, which describes what the
distant test is about rather than the change, neither. Such a test scores 0.35, a candidate but
never a strong one alone. Reaching the symbol from a module the test imports now scores 0.15
instead of 0.10, so an import plus that reach, 0.40, ranks above a test that only reaches it from
afar.

The graph's bounds matter twice. A module that does not parse, one past a bound, and a slow
repository leave the graph incomplete, with a warning, and an incomplete graph rules no private
helper untested, since the reach it lacks could be the one that tests it. It never stops a reach
it did find from counting.

Measured: on `distiller` `main..skeleton`, `unexpected_failure` gains every test of `cli.py` at
0.35, its own test reaching 0.55 with its name, and `evaluate.run` and `report.run` gain the test
that runs `python -m distiller`; no risk score changes. `meal-manager` (`8894749`, and
`v0.2.0..HEAD` over `src/**`) is unchanged. This repository's `v0.3.0..main` changes 121 of 156
candidate lists, mostly adding tests that reach a symbol from afar below the ones that reach it
from its module, and two risk scores: `SymbolReferences` gains its first candidates, and
`authorized_roots`, whose tests are named after it and reach it, becomes a strong candidate. The
pipeline over this repository takes 0.2 to 0.4 seconds longer. The 17-case corpus has no test file
and is unchanged.

## Candidate tests through a module's own calls (2026-10-13)

Once file-level evidence stood as the test file, a helper no test names, such as `distiller`'s
`_parser` or `pr_number`, got `tests/test_cli.py` as its candidate, and so did a helper nothing
calls. The test gap could not tell them apart, nor could a reader.

The AST stage now records, for each symbol of a changed file's head revision, the names its code
uses — references rather than calls, because `type=pr_number` hands a function to argparse and
`{"x": _handle_x}` to a dispatcher, which call it later. For every test that file-level evidence
relates to the module — a configured mapping, a mirrored name, a direct import, or a `python -m` run
— the mapper takes the names the test uses, with those of the same file's helpers and fixtures it
names (`run_args_from(...)` calls `cli.main`), and follows them through the module: a test reaches
whatever it names, whatever that names in turn, the protocol methods of a class it reaches, and the
module's top-level code, which loading runs. Reaching a symbol is per-test evidence, "changed symbol
reached through calls", worth 0.10 like a body mention and not added on top of one. Loading a module
is not counted again for its `<module>` symbol, since the import or the run already is.

A private, undecorated helper is called only through its own module. When tests relate to the module
and none reaches or names it, neither their file nor a test named after the module is evidence that
it is tested, and only a configured mapping still counts: such a helper has no candidate. A public
function may be called from elsewhere and a decorator may register a helper with something that
calls it, so both keep the file. Without any test related to the module that way, nothing is ruled
unreached: `meal-manager` loads its handlers with `importlib`, and its tests named after a handler
stay candidates.

Static reach over-approximates: a name shared by unrelated symbols counts for all of them, and
a branch a test never takes counts as reached. It cannot see dynamic dispatch, which is why only
private, undecorated helpers are ever ruled out.

Measured: on `distiller` `main..skeleton`, `_parser`, `_run_command`, and `pr_number` gain the
tests that reach them, at 0.60, and fall four or five points; 7 of 11 lists change and the overall
risk stays 42. On a scratch copy of that branch with an unused `_describe` helper added to
`cli.py`, `_describe` and `_parser` had both scored 40 with `tests/test_cli.py`; now `_describe` has
its own obligation reading "none (no_candidate_found)", at 46, and `_parser` scores 35.
`meal-manager` (`8894749`, and `v0.2.0..HEAD` over `src/**`) is unchanged. This repository's
`v0.3.0..main` changes 92 of 152 lists, and 24 private helpers reached through their module's
public functions, such as `mcp_server._dispatch` through `McpServer.handle`, gain their first
candidate and fall six or seven points. No changed private helper in these ranges is unreached,
so none loses its candidates. The 17-case corpus has no test file and is unchanged.

## Output-affecting corrections (2026-10-13, added and removed symbols)

Every added or removed symbol read "A callable or structural symbol was added without a reliable
semantic match." with "Callers or observable behavior may change; the intended compatibility
contract needs review." That describes a rename or move the matcher could not correlate, which is
what the ambiguous near-match case is. For the rest it was wrong twice: added code, a whole new
file's included, has nothing to match and no earlier callers. On `distiller` `main..skeleton`,
where all eleven findings are new code, it read as eleven failed renames. An added symbol now
reads "A callable or structural symbol was added." with "New behavior becomes reachable; its
intended contract needs characterizing tests.", and a removed one "A callable or structural
symbol was removed." with "Callers that still use it may fail or change behavior; the intended
compatibility contract needs review." The ambiguous case keeps its wording, its two assumptions,
and its rule ID. Each other case keeps one assumption and its 0.58 baseline, so no confidence
moves.

Their evidence showed the snapshot's signature, which is what comparisons read: a callable's
parameters, but `bases(...)` for a class and `module` for a module. A snapshot now also carries a
display text for these two deltas only. A class shows its decorators by name, its bases and
keywords, and up to 20 fields, as `@dataclass class RunArgs: pr: int, head: str, …`, and a module
its first five top-level statements after any docstring, one per line. A callable's display is
its signature, and the signature itself is unchanged.

Measured: no score, confidence, or obligation changes. `distiller` rewords its eleven findings,
and `__main__` and `RunArgs` now show their code. `meal-manager` rewords 14 of 91 findings on
`8894749` and 24 of 122 on `v0.2.0..HEAD` over `src/**`, and this repository 43 of 149 on
`v0.3.0..main`. One, two, and six class excerpts change, as `bases(TimeoutError)` becoming
`class DataLockTimeout(TimeoutError)`. The 17-case corpus has no added or removed symbol and is
unchanged.

## Output-affecting corrections (2026-10-13, running without a model)

The CLI's `--no-llm`, a CLI without a provider, and the Claude Code server run without a model by
design; the server never has one. Every such result still warned "No inference model was
provided; returned deterministic fallback findings." and listed "LLM interpretation was
unavailable; deterministic fallback was used." among its limitations. The interpreter's own
comment already called this "not necessarily a failure", but read in a brief it was one: a
Claude Code session reviewing `distiller` reported that the tool had fallen back to a degraded
mode.

No model being supplied is no longer a warning, and its limitation now reads "No model
interpreted the findings; each comes from a deterministic structural rule." A model that was
called and produced nothing usable is a fallback, and keeps the warning of its failed batches
and the old limitation. The `deterministic_fallback` origin and `deterministic_mode` keep their
values, which the README documents for runs without a provider; only the prose changes. When the
CLI cannot load a provider, it still says why on stderr.

The sixteen evaluation goldens with findings lose the warning and carry the new limitation;
nothing else in them changes, nor does any finding, score, or obligation.

## Output-affecting corrections (2026-10-13, pooled candidate reasons)

An obligation that stands for several findings pools their candidate tests. For a test more than
one of them matched, it kept the best score but the union of every match's reasons, so the
reasons stopped explaining the score. On `distiller` `main..skeleton`,
`test_an_unexpected_failure_is_one_line_that_names_only_its_class` showed 0.60 with five reasons
worth 0.95, drawn from its matches for `cli.main` and for `messages.unexpected_failure`. A pooled
test now keeps its best match whole, reasons included, and on a tie the first one pooled.

Measured: `distiller`'s two such candidates now add up, and `meal-manager` and this repository's
`v0.3.0..main` had none. No candidate list, score, or risk changes.

## Output-affecting corrections (2026-10-13, file-level candidates)

A mirrored file name, an import, a configured mapping, a `python -m` run, and a package import
are evidence about a test file: every test in it has the same. On `distiller` a mirrored name
plus an import scored 0.50 for each of `test_cli.py`'s eight tests and every symbol of `cli.py`,
so a helper that no test names, such as `_parser` or `pr_number`, got the first five of those
tests in alphabetical order, as five pieces of evidence. A function no test reaches would have
got the same five.

Counting those signals once instead of summing them was measured and rejected: `_parser`,
`_run_command`, and `pr_number` lost every candidate, and so did `__main__`, whose mirrored name
and run are both evidence about its file, although `test_cli.py` exercises the helpers through
`main`. Their test gap would have read as no test at all. So the scores stay and the
presentation changes. A test with evidence of its own — the changed symbol or module named in
its name or body — is listed as before. The other tests that qualify are grouped by file. A file
with more than one stands as a single candidate with the symbol `<module>`, the best of their
scores, and that test's reasons, and the brief shows it as the file. A file with one such test
still names it, which keeps `__main__`'s `test_python_m_distiller_exits_with_what_main_returns`.

This does not tell a tested helper from an untested one in the same module: both get the file.
That needs to know which functions the tests reach through the module's own calls, which the
mapper does not compute.

Measured: no score or risk changes anywhere. `distiller` changes 6 of 11 candidate lists, and
`_parser`, `_run_command`, and `pr_number` now have only `tests/test_cli.py`; its one obligation
shows the same five tests, which outrank the file. `meal-manager` is unchanged. In this
repository's `v0.3.0..main` 50 of 147 findings gain a whole-file entry, and two obligations
change: four of the five candidates of the `authorization_change` in
`semantic_candidates._classify_condition` were tests of `test_candidate_mapping.py`, chosen
alphabetically, and it now lists three files and two single tests. The 17-case corpus is
unchanged.

## Output-affecting corrections (2026-10-13, package imports)

Candidate mapping counted a test as importing the changed module when it imported any package
above it: `from distiller import cli` records `distiller`, so the test also imported every other
module of `distiller`, at 0.25 as "direct module or symbol import". A package import reaches one
of its modules only if something along the way imports it, which the rule could not tell. With
category terminology's 0.10 the two reached the threshold: on this repository's `v0.3.0..main`,
`test_the_risk_formula_in_the_skill_reproduces_every_score` was a candidate for twelve symbols
of `mcp_server.py` and for `ast_diff/compare.py`, and other scripts' tests for
`scripts/verify_wheel.py`.

Dropping the rule would have cost `distiller` a real candidate:
`test_an_unexpected_failure_is_one_line_that_names_only_its_class` imports `cli`, which imports
`messages`. A package import is now its own signal, "changed module's package imported", at
0.15 — enough to confirm a test whose name spells the changed symbol (0.35), not enough with
terminology or a body mention alone (0.25) — and it is not added on top of a direct import of
the module.

Measured: `distiller` keeps every candidate, `unexpected_failure`'s at 0.35 instead of 0.45
under the new reason. `meal-manager` (`8894749`, and `v0.2.0..HEAD` over `src/**`) is unchanged.
This repository's `v0.3.0..main` changes 118 of 147 candidate lists. 28 findings lose every
candidate, all of them a package import plus terminology, and score six or seven points higher;
elsewhere the dropped matches make room for tests of the changed module itself, so
`snapshot_symbol` now lists the probe tests of `extract.py` instead of LLM batching and MCP
tests. The 17-case corpus is unchanged.

## Output-affecting corrections (2026-10-13, `__main__` modules)

A package's `__main__.py` has no test that imports it: its tests run the package as a program.
Candidate mapping saw neither. Mirroring compared the file's stem, `__main__`, with a test
file's, `main`, and only imports counted as a structural relation, so on `distiller`
`main..skeleton` the changed `__main__.py` had no candidate and, at 49/100, was the riskiest
finding — while `tests/test_main.py` runs exactly `python -P -m distiller`.

A test file now names a module without the underscores that make it private or special, so
`test_main.py` mirrors `__main__.py` and `test_common.py` mirrors `_common.py`. The test index
also records the modules a test file runs as a program: `"-m"` followed by a module name in a
list or tuple, `-m pkg` inside a string, and `runpy.run_module("pkg")`, collected once per file
as imports are, so a command built at module level counts. A run of `pkg` relates the file's
tests to `pkg/__main__.py` as an import would, for 0.25. A first version related a run of any
module to it, and on this repository's `v0.3.0..main` the end-to-end MCP tests, which run
`python -m semantic_diff_weaver.mcp_server`, rose to 0.50 for every symbol of `mcp_server.py`
and displaced more specific tests in 19 lists. Running a module other than `__main__` is as
coarse as importing it, which is already counted, so only `__main__.py` gets the signal.

Measured: on `distiller`, `__main__` gains `test_python_m_distiller_exits_with_what_main_returns`
at 0.50 and falls from 49 to 42, which is now the overall risk. `meal-manager` (`8894749`, and
`v0.2.0..HEAD` over `src/**`), this repository's `v0.3.0..main`, and the 17-case corpus are
unchanged.

## Output-affecting corrections (2026-10-13, symbol names in tests)

Candidate mapping matched a changed symbol against the single words of a test's name and body:
each identifier was kept together with its underscore-separated words. A compound name was never
among a test name's words, so `pr_number` could not match `test_pr_number_rejects_zero`, nor
`messages.unexpected_failure` the `distiller` test named
`test_an_unexpected_failure_is_one_line_that_names_only_its_class`. A single word matched too much:
`report.run` found `run` in the name and body of `test_evaluate_gets_its_options_as_run_args` and
ranked that test of another module first. The body was read from the whole unparsed function, so
its `def` line put every word of the test's name in its body too, and a name match always scored
the body's 0.10 as well.

A symbol's name now matches a test name as whole words in sequence, as a module's already did:
`RunArgs` and `_run_args` both read as `run_args`. A body is read by whole identifiers, so
`run_args_from` is not `run`, and a test's own name is one identifier rather than its words. A
single word turns up in test names about something else, so it names the symbol only in a test
whose body uses it. Two first versions were wrong. Exact identifiers lost this repository's tests
that call `_serve(...)` and `_analyze(...)`, private wrappers of the changed `serve` and
`analyze`, so a leading or trailing underscore is ignored. Ignoring it everywhere made `__init__`
read as `init`, which matched `test_dii_init_resolves_aliases` to five constructors in
`meal-manager`, so a dunder stays whole and no test name names it. Category terminology still
reads words.

Measured: `distiller` `main..skeleton` changes 5 of 11 candidate lists.
`messages.unexpected_failure` gains the test named after it and falls from 49 to 42; `_run_args`
and `RunArgs` find `test_evaluate_gets_its_options_as_run_args` by name, and `report.run` no
longer proposes it. `meal-manager` `8894749` changes 8 of 91 lists. `atomic_write_json`,
`validate_state`, and `from_dict` gain their first candidates, six risk scores fall by six or
seven points, and `atomic_write_json`'s side-effect finding moves from `high` to `medium`.
`JsonTuningRepository.load` loses its one candidate, a test of another repository's `load`, and
rises from 46 to 52. `v0.2.0..HEAD` over `src/**` moves the same seven scores. This repository's
`v0.3.0..main` changes 16 of 142 lists, 14 of them in their first candidate, mostly to the test
named after the symbol — `test_counts_for_an_unmatched_file_are_zero` for
`CoverageMap.counts_for` — and one score. A generic word still finds unrelated tests:
`mcp_server._error` now ranks tests whose names say `error` and whose bodies read an `"error"`
key, a configuration test among them. The 17-case corpus has no test file and is unchanged.

## Output-affecting corrections (2026-10-13, merged obligations)

Equivalent obligations merged by normalized Given/When/Then, as the plan-conformance closure
below records, but a scenario template names no symbol, so equivalence reduced to "same
category". Every behavior of a category then shared its obligations. Their candidate tests were
pooled and cut to five, and the static status was `candidate_exists_unverified` once any of them
had a candidate. A behavior with no candidate was listed under another's tests, and the brief,
which shows a finding's label but not its test gap, gave no other way to tell. On `distiller`
`main..skeleton`, `__main__` and `messages.unexpected_failure` had no candidate, yet the one
obligation covering them listed `test_an_unexpected_failure_is_one_line_that_names_only_its_class`,
a test matched to `cli.main`. Grounded verdicts already demanded unanimity, and a merge of a
covered and an uncovered behavior fell back to static matching instead of reporting either.

The merge key now includes the behavior's coverage status, so an obligation's status and
candidate tests hold for every behavior it covers, and behaviors without a candidate share one
that says so, at the higher priority their test gap already gave them. A covered and an uncovered
behavior each keep their own verdict instead of losing both. The obligation grouping high-risk
overflow past `max_test_obligations` cannot be split without exceeding the cap, so like its
origin and confidence it reports its weakest link: the status of its least-tested behavior, in
the order uncovered, no candidate found, mapping incomplete, unverified candidate, covered.

Measured: no risk score changes, since obligations do not feed them. `distiller` goes from one
obligation to two. `meal-manager` `8894749` goes from 25 obligations to 42, and `v0.2.0..HEAD`
over `src/**` from 22 to 41. Of their findings, 67 of 91 and 98 of 122 had no candidate yet were
linked only to obligations listing other findings' tests, and each now has an obligation that
says none was found. This repository's `v0.3.0..main` goes from 25 obligations to 28, two
findings no longer under others' tests. The 17-case corpus, in which no obligation merges, is
unchanged.

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

The sampling half of this record was superseded on 2026-10-25: no family is sent sampling
parameters any more, and Claude Opus 5.5 and Claude Sonnet 5.5 are shaped for thinking of their
own. See *Anthropic requests carry no sampling parameters*.

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
