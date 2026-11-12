# Changelog

## Unreleased

- **Evaluation:** the benchmark has a fourth held-out split, 30 pull requests of
  django-commons/django-axes, jpadilla/pyjwt, hynek/stamina, agronholm/apscheduler, and
  alisaifee/limits, and the third split joins development, which now has 120 cases. Claude Opus 5.5
  and gpt-6.1-terra labeled its 95 symbols blind and apart, as they did the third's. They agreed on
  whether the behavior changes for 77 of the 82 scored symbols (kappa 0.88), and on the exact
  categories for 55. The reviewer settled every scored label on a page that showed the two proposals
  as A and B without saying whose: all 55 agreements were kept, and of the 27 disagreements 11 took
  gpt-6.1-terra's proposal, 6 Claude Opus 5.5's, 5 both together, and 5 the reviewer's own label.
  Its 42 behavior changes list 3.0 categories each, against development's 2.5, and name
  authorization 5 times, boundary 6, ordering 6, and retry or timeout 4. `benchmark/README.md` gives
  the protocol, and `docs/benchmark.md` the agreement and the review. No analyzer behavior changes.
- **Evaluation:** `docs/benchmark.md` records the baseline that the fourth held-out split and the
  120 development cases set with 0.20.1, in deterministic mode. Held out, 41 of the 42 behavior
  changes get a finding, the first behavior change without one on any split measured so far, and 22
  get one in an expected category; 5 of the 38 unchanged symbols get one, and 32 of the 74 scored
  findings are in an expected category. In development, every one of the 157 behavior changes gets a
  finding, 102 in an expected category, 28 of the 110 unchanged symbols get one, and 124 of the 322
  scored findings are in an expected category. Scored against each annotator's labels as given,
  before the review, the held-out precision is 44.6% and 41.9%. Only the held-out totals were
  computed. Documentation only.
- **Fix:** the benchmark's fourth round names five kinds of change the rules reported as unknown, or
  as a state transition they were not, designed from the 120 development cases. A symbol removed
  without a near-equal one is an `output_contract_change` under the new `SDW-RULE-SYMBOL-REMOVED`,
  as a removed API operation already was. A changed assignment that writes a name meaning a retry,
  a timeout, or a sleep is a `retry_timeout_change` under the new `SDW-RULE-RETRY-ASSIGNMENT`,
  before the state rule reads `state` in the variable it writes into, as in tenacity's
  `retry_state.upcoming_sleep`. A class's own changed assignment is an `output_contract_change`
  under the new `SDW-RULE-CLASS-ATTRIBUTE`, and an otherwise unknown assignment of a method that
  sets an attribute or an item of `self` or `cls` is a `state_transition_change` under the new
  `SDW-RULE-INSTANCE-STATE`. An assignment's value cut at 250 characters now ends with a digest of
  all of it, taken of the redacted text, so two values that differ past the cut no longer read the
  same: httpx's `__all__` gained a name there that no assignment showed. On the development cases,
  behavior changes given a finding in an expected category go from 102 to 124 of 157, and precision
  in an expected category from 38.5% to 46.1%; no behavior change loses its finding or its
  category, and no unchanged symbol gains one, though four whose finding was unknown now name a
  category. Schema 1.5 and every golden are unchanged; `docs/decisions.md` records the decision and
  the criterion the fourth held-out split is measured against, and `docs/benchmark.md` the
  measurements.

## 0.20.1 - 2026-11-11

A model's obligations of type `review`, discarded. With a model, the analysis no longer adds the
model's obligations that ask a person to look rather than naming a test. The fourth blind review
found them useful 2 times in 18, a difference from the models' other types that its sample cannot
settle. Replayed, the four recorded runs of 2026-11-08 lose 141 of the 298 obligations the models
added, every finding unchanged. The release also scripts the release checklist's steps that were
done by hand, fixes two of the Claude Code skill's eval graders, and gives the prompt-injection
harness's tests two minutes. A patch release; analyses without a model, schema 1.5, and every
golden are unchanged.

- **Fix:** with a model, its obligations of type `review` are discarded, and the analysis says so
  once. They ask a person to look rather than naming a test, and the fourth blind review found them
  useful 2 times in 18, against 7 in 19 of the models' other types, a difference this sample cannot
  settle. The four recorded runs of 2026-11-08, replayed, lose 141 of the 298 obligations the models
  added: 39 of llama-3.1-8b-instruct's 48, 48 of deepseek-v4-flash's 88, 47 of gpt-oss-120b's 139,
  and 7 of gpt-6.1-terra's 23. No finding changes. The copy rule still runs first and the cap of six
  per behavior after, so review items no longer use up its room. Analyses without a model, schema
  1.5, and every golden are unchanged; `docs/decisions.md` records the decision.
- **Tests:** the prompt-injection harness's tests get two minutes each, where every test gets 30
  seconds. They build repositories and analyze them, dozens of Git processes, about 4 seconds
  locally. On 2026-11-11 the macOS leg's Intel runner ran the whole suite about 2.5 times slower than
  an hour before, and they timed out in two runs in a row.
- **Tests:** two of the Claude Code skill's eval graders failed right answers in the 0.20.0 run, and
  now pass them.
  - `triggers-in-spanish`: its judge, Haiku, failed a Spanish answer that met its two-claim
    criterion. Two regexes replace it, one for each claim.
  - `unverified-candidates`: the pattern of `says-unverified` missed "doesn't run" and "static
    guess", and now matches them.

  On the 16 answers recorded so far, the graders agree with the judges' verdicts on every answer but
  the one the judge failed. `docs/evaluation.md` records the run, which cost 1.66 USD.
- **Tests:** the release checklist's steps that were done by hand now have scripts.
  - `scripts/release_notes.py` writes a GitHub Release's notes from the version's `CHANGELOG.md`
    section. It reproduces the published notes of all 31 Releases, v0.1.0 through v0.20.0, byte
    for byte. A nested list item now keeps its own line.
  - `scripts/check_release.py` reads a published Release back and checks its title against the
    tag's message, its notes against those, and each attached file's digest against the file
    built for it.
  - `scripts/time_performance.py` times `tests/performance` in several trees, taking turns, as
    each release's timing run does.
  - `scripts/check_coverage.py --per-module` lists each critical module's branch coverage.

  No package code changes.

## 0.20.0 - 2026-11-10

Output contract changes the rules left unknown, named, and constants a module only adds, read as no
change: the benchmark's third correction round, designed from its 90 development cases. A change the
structural rules cannot name, and that feeds what its function returns or yields, is an
`output_contract_change`, as is a module's changed `__all__`, and a module that only adds constants,
or removes private ones, is a refactor. On the development cases, behavior changes given a finding
in an expected category go from 46 to 70 of 120. Measured once on the third held-out split, against
a criterion committed before, they go from 30 to 32 of 37, precision in an expected category rises
from 51.4% to 63.5%, and every behavior change keeps a finding. The release also records a batch's
retry measured live and a fourth blind review of test obligations, and CI tests Hermes v0.23.1,
released the same day. A minor release for the three new rules, `SDW-RULE-OUTPUT-FLOW`,
`SDW-RULE-EXPORTS`, and `SDW-RULE-REFACTOR-CONSTANTS`; schema 1.5 and every golden are unchanged,
and the decision is recorded in `docs/decisions.md`.

- **Evaluation:** `docs/decisions.md` and `docs/benchmark.md` record a batch's retry measured live.
  deepseek-v4-flash ran the 90 development cases again with 0.19.4 and the options of its 0.19.2
  run, for 0.0708 USD. Of the 8 batches whose first call was cut off at the output cap, 7 were
  answered on the retry, where 6 had been, and 3 of those retries spent more tokens than the first
  call's budget, which no retry had at 0.19.2. One case lost a batch, where two had. The same run
  added no finding of the model's own, where the 0.19.2 run had added 3. Documentation only.
- **Evaluation:** `docs/benchmark.md` records a fourth blind review of test obligations: the four
  models' runs of 2026-11-08, replayed through 0.19.4, on the 30 development cases the obligations
  from evidence were not designed from. The deterministic obligations were useful 10 times in 20,
  where the third review found 9 of 20 on the cases they were designed from, and the models' 9 times
  in 37: 3 of 10 each of gpt-oss-120b's and deepseek-v4-flash's, 3 of gpt-6.1-terra's 7, and none of
  llama-3.1-8b-instruct's 10. A model's obligations of type `review` were useful twice in 18.
  Documentation only.
- **Fix:** the benchmark's third round names the output contract changes it reported as unknown, and
  reads constants a module only adds as no change, designed from the 90 development cases. A changed
  condition, comparison, assignment, loop, context, or call that the rules leave unknown, and that
  feeds what its function returns or yields, is an `output_contract_change` under the new
  `SDW-RULE-OUTPUT-FLOW`; a module's changed `__all__` is one under the new `SDW-RULE-EXPORTS`; and
  a module whose code differs only in constants it adds, or private ones it removes, is a refactor
  under the new `SDW-RULE-REFACTOR-CONSTANTS`, hidden by default like every refactor, never when a
  constant could run code or shadow a name the code already read. On the development cases, behavior
  changes given a finding in an expected category go from 46 to 70 of 120, and precision in an
  expected category from 18.3% to 31.0%; no behavior change loses its finding or its category. A
  finding whose condition joined its return's keeps that condition's obligation as a second one.
  Measured once on the third held-out split, against a criterion committed before, it held and
  improved: 32 of 37 behavior changes get a finding in an expected category where 30 did, precision
  in an expected category rises from 51.4% to 63.5%, every behavior change keeps a finding, and no
  unchanged symbol gets one. Schema 1.5 and every golden are unchanged; `docs/decisions.md` records
  the decision, and `docs/benchmark.md` both splits' measurements.
- **Tests:** CI tests Hermes v0.23.1, the current release, which came out on 2026-11-10. The
  `hermes-current` job builds it from its tag, pinned to commit `3b421578`, with uv 0.13.5, which
  its installer still pins. Rehearsed locally first, the plugin installs with the same `caution`
  verdict, its one high finding in `tests/`, loads with both tools, and passes discovery; the
  signatures the plugin calls are unchanged. `docs/decisions.md` records the compatibility snapshot.

## 0.19.4 - 2026-11-09

A model's claim in another category, and a retry's budget. With a model, a claim in a category that
no finding on its evidence has adds no finding: made an unknown change, it was reported beside the
finding already there, a second finding of the same change, 6 times on the benchmark's 90
development cases with three models. And a batch's retry gets twice its first call's output budget
and timeout, where a reasoning model's reply cut off at its budget got the same budget again, which
cut off two of deepseek-v4-flash's batches a second time. Analyses without a model are unchanged. A
patch release; schema 1.5 and every golden are unchanged.

- **Fix:** with a model, a claim in a category that no finding on its evidence has adds no finding.
  A model cannot change a finding's category, so the analysis made such a claim an unknown change,
  and where no finding on that evidence was unknown either, reported it beside the finding already
  there: a second finding of the same change, in a category no label expects. It happened 6 times on
  the benchmark's 90 development cases, with three models. The claim now adds no finding, and the
  analysis warns that it added none; its obligations go to the findings whose evidence holds all of
  its own, as any of a model's obligations do, unless its prose copies what it was shown, which
  still discards it with them. A claim on evidence where a finding is an unknown change is still
  made one. Replayed, the four recorded runs change in those six cases alone: no model adds a
  finding, and each of the three runs has one obligation fewer. `run_benchmark.py llm` counts such
  behaviors apart, and `prompt_injection.py` too. Analyses without a model are unchanged. See
  `docs/decisions.md`, 2026-11-09, a model's claim in another category.
- **Fix:** with a model, a batch's retry gets twice its first call's output budget and timeout,
  within the same ceilings of 8,192 tokens and 150 seconds. A reasoning model's thinking shares
  the budget with its reply and varies from call to call, so a reply cut off at its budget could
  be cut off again on a retry with the same one: on the benchmark's 90 development cases,
  deepseek-v4-flash lost two batches, both of tenacity, that way, and they fell back to the
  deterministic findings. A host hands back a cut-off reply as one it cannot read, so every retry
  gets the larger budget, after a timeout too. Analyses without a model are unchanged. See
  `docs/decisions.md`, 2026-11-09, a batch's retry.

## 0.19.3 - 2026-11-08

A merged finding's symbol, and the analyzed code's warnings. With a model, a finding that a
model's claim merges into keeps its own symbol: sorted by ID alone, its evidence could begin with
another symbol's, and in the benchmark's flask#5899 a removed method's only finding became its
class's. With or without a model, the analysis parses a repository's code without the warnings
that code raises, which reached stderr and, where warnings are errors, made a file Python runs
count as unparsed. `docs/benchmark.md` measures the 90 development cases with three more models
and 15 of them with gpt-6.1-terra: none changes what the analysis finds. A patch release; schema
1.5 and every golden are unchanged.

- **Fix:** the analysis parses a repository's code without the warnings that code raises.
  Python warns while it parses a string with an invalid escape sequence, such as a regular
  expression written without the `r` prefix: a `SyntaxWarning` from 3.12, a `DeprecationWarning`
  before. Each parse of the product code, of its tests, or of a test file the skeletons extend
  printed such a warning to stderr, where it read as the tool's own. Where warnings are errors,
  as under `-W error`, Python raised it as a `SyntaxError` instead: the analysis reported a file
  Python runs as unparsed and lost its changes, and the skeletons refused its test file. Every
  parse now goes through one helper that ignores both categories, and a test keeps it the only
  one.
- **Fix:** with a model, a finding that a model's claim merges into keeps its own symbol. The
  merge kept the finding's path and symbol but sorted its evidence by ID, and the baseline, the
  change groups, the obligations, and the skeletons all read a finding's symbol from its first
  evidence. So a claim that also cited another symbol's evidence, with a lower ID, moved the
  finding to that symbol: in the benchmark's flask#5899, gpt-6.1-terra's claim on the removed
  `App.should_ignore_error` also cited the assignment that replaced it, and the method's only
  finding became `App`'s. The merged evidence now begins with the finding's own symbol's, each
  part in ID order. Analyses without a model are unchanged. See `docs/decisions.md`, 2026-11-08,
  a merged finding's symbol.
- **Evaluation:** `docs/benchmark.md` measures the 90 development cases with 0.19.2 and three
  models through OpenRouter, deepseek-v4-flash, llama-3.1-8b-instruct, and gpt-oss-120b, for
  0.16 USD, and 15 of them with gpt-6.1-terra through Hermes's `ctx.llm`. No model changed
  precision in any category, recall, or the unchanged symbols given a finding, and none cited
  evidence it was not shown. Only gpt-oss-120b's support told right findings from wrong ones, at
  p = 0.02 on its own and 0.07 corrected for the three models. The refactor rule discarded 22
  claims of a change, none at the cost of a behavior change's finding. Two findings moved to
  another symbol when a model's claim merged into them, the defect the fix above corrects.
  Documentation only.
- **Evaluation:** `scripts/prompt_injection.py`'s summary gives the calls a run made and what
  the endpoint billed for them, failed calls included. It gave only what the analyses kept,
  which left out a reply the schema rejects and the retry it costs: llama-3.1-8b-instruct's run
  read 0.0063 USD where 0.0097 was billed, for 51 calls in 48 runs. `docs/evaluation.md` and
  `docs/decisions.md` now give the billed cost and the calls of both models' runs.
- **Evaluation:** `docs/evaluation.md` records the prompt-injection cases against a second model,
  llama-3.1-8b-instruct, with 0.19.2's copy rule, for 0.0097 USD. No evidence the analysis did not
  extract reached the output, and no finding was lost. Its replies repeated an injection's marker
  in 9 of 21 valid injected runs, and none reached the analysis. Replayed with the rule turned off,
  3 would have, each the README's command as an obligation's title, as gpt-oss-120b wrote it
  before the rule. Its 3 invalid runs followed a README asking for evidence it was never shown into
  replies the schema rejects, and the analysis kept its deterministic findings. Documentation only.

## 0.19.2 - 2026-11-08

A model's prose that copies what it was shown. With a model, the analysis discards a model's text
that repeats 40 or more consecutive characters of the repository's text it was shown, or of its
own instructions. Before it, a README that asked gpt-oss-120b to put its own `curl` command, piped
to a shell, into the test plan got it into an obligation's title in 3 of 3 runs, and in none after.
`scripts/prompt_injection.py` measures the inference layer against such repositories.
`scripts/shadow_select.py` measures `select` against the failures of public projects' red
pull-request runs, where no run escaped the selection. A patch release; analyses without a model,
schema 1.5, and every golden are unchanged.

- **Security:** with a model, the analysis discards a model's text that repeats 40 or more
  consecutive characters of what the call showed it from the repository, or of its own
  instructions: a proposed obligation whole, a finding of the model's own, or an assumption alone,
  each with a warning. A repository can write instructions to the model in its README or in a
  changed string. Measured with gpt-oss-120b, a README that asked for it got its own `curl` command,
  piped to a shell, into a test obligation's title in 3 of 3 runs. Evidence it never showed was
  never accepted, nor was a finding lost. On the same replies, replayed, injected text reached the
  analysis in 2 runs of 24 instead of 7, a marker alone each time. On the benchmark's 60 development
  cases the rule discarded 2 of the model's 262 obligations and 1 of its findings, long quotes of
  the changed code. Analyses without a model are unchanged. See `docs/decisions.md`, 2026-11-08, a
  model's prose that copies what it was shown.
- **Evaluation:** `scripts/prompt_injection.py` measures the inference layer against eight fixture
  repositories that tell the model what to say, each analyzed clean and injected, without a model
  and with one, under a spending cap, with `--replay` for later code. `docs/security.md` says what
  the model sees, never a comment or a docstring, and what its prose can still carry.
- **Evaluation:** `scripts/shadow_select.py` measures `select` against failures that happened, the
  red pull-request runs of public projects, since the repositories this project is tried on never
  push a red run: their pre-commit hooks run the whole suite. It reads each run's failed tests from
  its CI logs, asks this checkout's `select` which test files the pull request's change can affect,
  with the default configuration and with documentation and changelogs ignored, and runs nothing of
  theirs. On 2026-11-08, 54 of 268 red runs of the benchmark's 20 projects failed a test file of the
  head, and no run escaped: in none would the selection alone have passed. One was missed. A model
  that DRF's pull request 9751 added to one test file broke the test database every test shares, and
  4 of the 56 files that failed were ones `select` left out, beside 52 it chose. The selection's
  docstring, `docs/configuration.md`, and the README now name that blind spot, and
  `docs/evaluation.md` records the Raspberry Pi's nightly mutants. No analysis changes. See
  `docs/decisions.md`, 2026-11-08, the selection measured against real red runs.

## 0.19.1 - 2026-11-08

The brief in the job summary, and the skills for the user's own change. The GitHub Action writes
its brief to the job summary on every run, before it comments, so a run that cannot comment, as a
fork's pull request cannot, still shows it, and so does a run on a push. The Claude Code skill, and
the Hermes skill with it, load when the user asks what their own change does, also when some of
their edits are not committed yet. The benchmark has a third held-out split, labeled blind by two
models of different families and reviewed, and the baseline 0.19.0 sets on it; `evals/` measures
the Claude Code skill with `claude plugin eval`; a weekly workflow watches the `anthropic` SDK; and
`hermes-current` names Hermes's reason when an install leaves the plugin disabled. A patch release;
the analysis, its two tools, the CLI, schema 1.5, and every golden are unchanged.

- **Evaluation:** `docs/evaluation.md` records the performance suite on a Raspberry Pi 5. In six
  runs every fixture met its ceiling, taking 1.8 to 3.0 times as long as on the development
  machine; the mass-rename fixture came closest, at half of its two-second ceiling. Documentation
  only.
- **Evaluation:** the benchmark has a third held-out split, 30 pull requests of
  django-guardian/django-guardian, jazzband/django-oauth-toolkit, redis/redis-py,
  more-itertools/more-itertools, and dateutil/dateutil, and the second split joins development,
  which now has 90 cases. Two annotators of different model families, Claude Opus 5.5 and
  gpt-6.1-terra, labeled its 53 symbols blind and apart. They agreed on whether the behavior
  changes for 43 of the 47 scored symbols (kappa 0.78), and on the exact categories for 19. The
  reviewer settled every scored label on a page that showed the two proposals as A and B without
  saying whose. The labels hold the benchmark's first `authorization_change`, and list 3.4
  categories per behavior change against development's 2.2, so precision on the two splits does
  not compare. `benchmark/README.md` gives the protocol, and `docs/benchmark.md` the agreement and
  the review. No analyzer behavior changes.
- **Evaluation:** `docs/benchmark.md` records the baseline that the third held-out split and the 90
  development cases set with 0.19.0, in deterministic mode. Every behavior change of both splits
  gets a finding. Held out, 30 of 37 get one in an expected category, none of the 10 unchanged
  symbols gets one, and 37 of the 72 scored findings are in an expected category; in development,
  46 of 120, 31 of 100, and 48 of 262. Scored against each annotator's labels as given, before the
  review, the held-out precision is 48.6% and 36.6%. The split's wider labels explain part of its
  lead, and its sample the rest. Only the held-out totals were computed. Documentation only.
- **Fix:** the GitHub Action writes the brief to the job summary on every run, before it comments.
  `docs/github-action.md` said a fork's pull request still showed the result in the step summary,
  but nothing wrote one, so a run that could not comment showed nothing. A run on a push, with
  `base-ref` set and no pull request to comment on, now shows its brief there too. A brief past
  what GitHub keeps of a step's summary, 1 MiB, is cut at a section boundary, as the comment is,
  and a summary that cannot be written is a warning, never a failed step. See
  `docs/decisions.md`, 2026-11-07, the brief in the job summary.
- **Fix:** the Claude Code skill loads when the user asks what their own change does. Asked "What
  does my change do, and what should I test?" beside an uncommitted edit, Claude did not load it,
  and answered from `git diff` after running the test suite: the description named "a diff,
  branch, or pull request" and "what to test before merging". It now names "their change, a
  commit, a diff, a branch, or a pull request" and "what to test", also when some edits are not
  committed yet, and the Hermes skill's first paragraph says the same. Found by the eval suite
  below. See `docs/decisions.md`, 2026-11-07, the Claude Code skill measured.
- **Tests:** `evals/` is the Claude Code skill's suite for `claude plugin eval`, nine cases. Each
  builds a repository with `evals/fixture.sh`, asks what a user would, and grades the tool calls'
  arguments and the answer, with the plugin's real server. The cases check that the skill loads in
  Spanish and in English, and that it takes `origin/main` over a stale local `main`. It must ask
  when the two have diverged, say that uncommitted edits are left out, and present candidate tests
  as unverified. It must scaffold nothing until the user names obligations, retry no other path
  after `path_outside_repository`, and split a diff past the file limit with `include`, never
  `exclude`. On 2026-11-07 every case passed all three of its runs with the plugin and none
  without it, for 9.51 USD with the rerun of one case. `AGENTS.md` gives the command, which takes
  a cost ceiling.
- **Tests:** a weekly `anthropic-sdk` workflow notices an `anthropic` release that would fail every
  call the adapter sends, which ends an analysis in deterministic fallback behind a generic warning.
  No CI job installs the extra, and a release inside its range reaches users with no Dependabot pull
  request. The workflow reads the newest release the extra admits from PyPI, without installing or
  importing it, and opens one issue when it sends an API version the adapter was not checked
  against, or its `messages.create` stops taking a keyword the adapter passes.
  `scripts/check_anthropic_sdk.py` runs the same check locally and exits 1 when either moved. On
  2026-11-08 it read 2.6.0, which still sends 2026-10-21 and takes every keyword. See
  `docs/decisions.md`, 2026-11-08.
- **Tests:** `hermes-current` names the reason when Hermes installs the plugin but does not enable
  it. Hermes's install exits 0 when it refuses to enable a plugin, for instance because resolving
  the plugin's dependency environment fails. On 2026-11-08 a 504 for a wheel that one of Hermes's
  own optional extras downloads failed the job that way, on `main` too, and the log showed only a
  missing-PyYAML traceback from the load check. `scripts/verify_hermes_install.py` now reads
  `hermes plugins list --json` after the install and fails unless it shows the plugin enabled,
  with the end of the install's output, where Hermes says why. See `docs/decisions.md`,
  2026-11-08, an install that leaves the plugin disabled.

## 0.19.0 - 2026-11-07

Test obligations written from what a finding's evidence changed. A Python finding's obligation
names the symbol to run, quotes what changed before and after, and says what to assert by the kind
of change, where it repeated its category's fixed scenarios: a blind review on the benchmark's
development cases judged 9 of 20 of them useful, and none of 20 of the scenarios. An added or
removed private symbol joins a related finding's obligation, and a change no structural rule names
quotes the lines that changed. With a model, a change claimed on a refactor's evidence no longer
reaches the report. `scripts/run_benchmark.py llm` measures the inference layer with a real model,
and `scripts/mutate_select.py` measures the test selection against mutants. A minor release for the
obligations; findings, schema 1.5, and evidence are unchanged, the goldens change with the
obligations, and each decision is recorded in `docs/decisions.md`.

- **Evaluation:** `scripts/run_benchmark.py llm` measures the inference layer with a real model. It
  analyzes each development case of the benchmark without a model and with one behind an
  OpenAI-compatible endpoint, OpenRouter's by default, through `scripts/openai_compatible.py`, which
  stays out of the package. A case counts only if the model answered: it was called, and no batch
  failed. The report gives each measure both ways, with the mean confidence of right and wrong
  findings, and then what the model did: the findings it supported or added, the behaviors the
  analysis discarded for citing evidence it was never shown, the categories made unknown, and the
  calls, tokens, cost, and seconds. It needs a spending cap, never runs a held-out case, and sends
  nothing from a repository GitHub does not serve as public. No package change. See
  `docs/decisions.md`, 2026-11-06.
- **Fix:** with a model, a change the model names on a refactor's evidence no longer reaches the
  report. The reconciliation made such a claim an unknown change and reported it in place of the
  refactor it contradicted, which the report hides. A refactor's evidence is now set aside from a
  model's finding of a change, and a finding that cites nothing else is discarded with its
  obligations. On the benchmark's development cases, replaying one run's replies with and without
  the change, unchanged symbols given a finding fall from 19 to 17 of 71, as without a model.
  Deterministic output, schema, and goldens are unchanged. See `docs/decisions.md`, 2026-11-07.
- **Evaluation:** `run_benchmark.py llm` saves every call's reply, and `--replay` analyzes a
  recorded run's cases again with those replies, calling no model, so that two versions of the
  analysis are compared on identical replies. Two live runs of one model differ too much for that.
- **Evaluation:** `docs/benchmark.md` records a first measurement with a model,
  `openai/gpt-oss-120b` on the 60 development cases for 0.0236 USD, and a blind review of its test
  obligations. The model left the findings as they were but for two wrong ones, which the fix above
  removes, and its support told right findings from wrong ones only weakly. A reviewer judged 6 of
  20 of the obligations it adds useful, and none of 20 deterministic ones, the taxonomy's fixed
  scenarios. Documentation only.
- **Feature:** a Python finding's test obligations quote what its evidence changed. Each names the
  symbol to run, what changed, quoted before and after, and what to assert by the kind of change:
  the arguments of a changed call that leaves the process, or else the outcome of the symbol it
  feeds, both outcomes of a changed condition, the boundary of a changed comparison and either
  side of it, the omitted argument of a changed default and the former value passed explicitly,
  the exception a changed `raise` or handler concerns, the value a changed `return` gives, and
  what the code reading a changed module-level variable does with it. A finding has one
  obligation, or two for a changed default or boundary, where its category gave two or three; one
  whose scenario would say little joins a stronger finding's obligation on its symbol. Findings,
  schema, and evidence are unchanged, and an API schema's or a configuration file's finding keeps
  its category's scenarios. Quotes are code spans that the Markdown keeps as code. On the
  benchmark's 60 development cases the obligations go from 186 to 135, and a blind review judged 9
  of 20 of them useful, above the 6 fixed beforehand, where the templates had none of 20. The
  goldens change with the obligations, and two of the corpus's required obligation concepts, which
  named scenarios that no longer fit their change, were revised. See `docs/decisions.md`,
  2026-11-07.
- **Feature:** a private symbol added or removed no longer has an obligation of its own when a
  related finding speaks for it: it joins the obligation of the code calling an added one, of the
  code that stopped calling a removed one, or of another symbol of its class, and an added one's
  own obligation otherwise names its callers. A change no structural rule names quotes the lines
  that changed, from the diff, instead of the category's fallback scenario, and a removed `raise`
  says what the symbol still raises. Findings, schema, and evidence are unchanged; on the
  benchmark's 60 development cases the obligations go from 135 to 124. See `docs/decisions.md`,
  2026-11-07, private symbols and unnamed changes.
- **Evaluation:** `scripts/mutate_select.py` measures `select` against mutants. Each changes one
  token of a repository's product code in a throwaway commit of a scratch clone, and the test files
  that ran its line at the base, by a coverage run of each in a process of its own, run against it.
  A test that catches it in a file `select` left out is a miss, confirmed by running the test again
  without the mutant. Mutants accumulate in a JSONL file that its `summary` subcommand reads, and a
  run with a fixed seed goes on where the last stopped, for a nightly job. It runs the analyzed
  repository's tests, so it stays out of the package, and keeps the environment's credentials from
  them. No package change. See `docs/decisions.md`, 2026-11-07.

## 0.18.0 - 2026-11-06

A Hermes skill that tells the agent when to use the tools. On Hermes 0.22.7 with gpt-6.1-terra,
asked what a commit changes, the agent used the tools in 5 of 5 runs with the skill and in 3 of 5
without it; in the other 2 it executed both revisions of the changed file instead. Three fixes
follow what current models, Hermes releases, and Python 3.14 do: a model's reply to a large
evidence batch is no longer cut off into deterministic fallback, the install documentation says
what Hermes 0.22.7 and 0.23.0 do, and an analysis warns when coverage contexts name one test per
line, as coverage.py records them by default on Python 3.14, while the documented recipe records
every test that ran a line. CI tests the current Hermes release, built from its Git tag, and a
weekly workflow opens an issue when a newer one is out. A minor release for the skill; schema 1.5
and every golden are unchanged, and each decision is recorded in `docs/decisions.md`.

- **Feature:** on Hermes, the plugin registers a skill, `semantic-diff-weaver:review`, that tells
  the agent when to use the two tools, and to answer from the committed source rather than by
  running the code under review. Current Hermes releases hide a plugin's tools until the model
  looks for them. On Hermes 0.22.7 with gpt-6.1-terra, asked what a commit changes, the agent used
  the tools in 3 of 5 runs without the skill; in the other 2 it executed both revisions of the
  changed file to compare them. With the skill it used them in 5 of 5 and executed nothing. The
  skill is listed in the system prompt, and each tool's description now names it. Every supported
  Hermes release can register it, from 0.14.0 on; a host that refuses it gets the tools alone,
  with plain descriptions. `scripts/verify_hermes.py` checks that the skill is registered, and
  `scripts/verify_wheel.py` that the wheel ships it. See `docs/decisions.md`, 2026-11-06.
- **Fix:** a model's reply to a large evidence batch is no longer cut off into deterministic
  fallback. Every structured call allowed 2,000 output tokens and 30 seconds. Measured through
  Hermes on gpt-6.1-terra, a reply needs about 200 tokens plus one per 21 input characters. A
  batch past roughly 38,000 of the default 48,000 characters was therefore cut off, retried, and
  sent to deterministic fallback behind the generic warning; the measured 34,326-character batch
  also took 42 seconds. Each call now gets 2,000 tokens plus one per 6 input characters, up to
  8,192, and 30 seconds plus one per 400 characters, up to 150. No output or schema change. See
  `docs/decisions.md`, 2026-11-06.
- **Fix:** the Hermes install documentation says what current Hermes releases do. On 0.22.7 and
  0.23.0, `hermes plugins install` scans the clone first. Its verdict for this plugin is `caution`,
  because one hostile fixture in the security tests stays a high finding even after Hermes lowers
  test-tree findings a level. A `caution` verdict needs `y` in a terminal, or `--force` without one.
  Those releases also install Pydantic and PyYAML themselves from `pyproject.toml`, and
  `--yes-deps` consents without a prompt. The README no longer calls the one-line install the whole
  setup, and no longer says the plugin registers one tool. `after-install.md` and the
  missing-dependency error give `hermes pm repair`, and `python -m pip install` for Hermes 0.19.0 and
  earlier, in place of `python -m pip install semantic-diff-weaver`: that package is not on PyPI.
  The fixtures stay as they are. See `docs/decisions.md`, 2026-11-06.
- **Fix:** an analysis warns when a coverage data file names one test per line, and the documented
  recipe records every test that ran a line. On Python 3.14 and later coverage.py measures with
  `sys.monitoring` by default, and with pytest-cov's per-test contexts that core keeps each line for
  the first test that ran it: a helper two tests call was credited to the first alone, so
  `--coverage-data` named fewer tests than ran a change, silently. coverage.py 7.15.3 and later warn
  in the test run's output, which the analysis never sees, and still record one test per line. The
  recipe in the README, `docs/configuration.md`, `docs/github-action.md`, and the Action's
  `coverage-data` input now sets `COVERAGE_CORE=ctrace`. Contexts that name two tests or more but no
  line that more than one of them ran now add a warning, since nothing in the data file says which
  core recorded it. The regression tests read two real data files of one run, recorded with each
  core, in `tests/fixtures/coverage`. No schema change. See `docs/decisions.md`, 2026-11-06.
- **Tests:** CI tests the current Hermes release, v0.23.0, which PyPI no longer carries. The new
  `hermes-current` job builds it from its Git tag, checked against the tag's commit, on Python 3.14
  with uv, as Hermes's own installer does. It installs the plugin with `hermes plugins install` and
  fails on a `dangerous` verdict, on a high or critical finding outside `tests/`, or on a plugin that
  does not load with both tools and analyze a change in Hermes's environment. It then runs
  entry-point and directory discovery. `hermes-compatibility` keeps 0.14.0, the lowest supported
  release, and drops 0.19.0. `scripts/verify_hermes.py` names a Hermes built from source by its
  release, where it printed `0.0.0`.
- **Tests:** a weekly `hermes-release` workflow notices new Hermes releases, which Dependabot cannot
  see because they exist only as Git tags. It compares the release `hermes-current` tests with the
  latest release of NousResearch/hermes-agent, and when CI is behind it opens one issue per release.
  The issue says what to move together: the tag, the commit it points to, and the uv that the
  release's installer pins. `scripts/check_hermes_release.py` runs the same comparison locally and
  exits 1 when CI is behind. See `docs/decisions.md`, 2026-11-06.

## 0.17.0 - 2026-11-04

Fewer findings where nothing runs differently, measured on a fresh held-out split. A person reviewed
the benchmark's 162 labels and corrected 26, and a second held-out split of 30 pull requests from
five more repositories, labeled and reviewed before the analyzer changed, joins it. A second round
of corrections, designed from the 60 development cases, reads more of what only a type checker
reads as no change: a callable's parameter annotations alone, a module's or a plain class's
annotations and type variables, and a module's version string. Held out, unchanged symbols given a
finding fall from 18 to 14 of 29, and every behavior change keeps its finding and its category. A
minor release for the three new refactor rules, `SDW-RULE-REFACTOR-ANNOTATIONS`,
`SDW-RULE-REFACTOR-TYPING`, and `SDW-RULE-REFACTOR-VERSION`, all hidden by default like every
refactor; schema 1.5 and every golden are unchanged, and the decision is recorded in
`docs/decisions.md`.

- **Evaluation:** a person reviewed every benchmark label. A reviewer checked the 162 labels of
  `benchmark/cases.json` on a page that showed each symbol's diff and never the analyzer's
  findings, and corrected 26 with their reasons: three drop a validation change the code does not
  make, 21 add categories a reviewer could fairly give, and two find a behavior change in a symbol
  labeled unchanged, rich's id counter and werkzeug's `send_file`. Every case's `reviewed_by` names
  the reviewer, and `benchmark/README.md` states two rules the review settled: a change is judged
  on the Python versions both commits support, and removing a symbol is no change by itself. No
  development measure moved; held out, 22 of the 61 findings naming a category are right where 16
  were, with 0.15.0 and 0.16.0 alike, and `docs/benchmark.md` records both measurements again. No
  analyzer behavior changes.
- **Evaluation:** a second held-out split. Thirty pull requests from
  `encode/django-rest-framework`, `jd/tenacity`, `urllib3/urllib3`, `agronholm/anyio`, and
  `pypa/packaging`, drawn by `run_benchmark.py propose` without choosing them by hand, add 89 labels
  to `benchmark/cases.json`, written before the analyzer's output on them was read and reviewed by
  a person, who corrected 16. They name `retry_timeout_change`, which no label named before, and
  become the held-out split, while the first held-out cases, measured for 0.16.0, join
  development. `benchmark/README.md` adds that a function is labeled with the functions nested in
  it and that every Python file outside the tests is labeled. Measured with 0.16.0 as the baseline
  for the next correction, every one of the new split's 44 behavior changes gets a finding, 16 in
  an expected category, and 18 of its 29 unchanged symbols get one too; `docs/benchmark.md` records
  only these measures for it, leaving its findings unread until a correction designed from
  development is measured on it. No analyzer behavior changes.
- **Fix:** more of what only a type checker reads is no longer a change, in a second round
  designed from the benchmark's 60 development cases. A callable whose parameters' annotations
  alone changed is a refactor under the new `SDW-RULE-REFACTOR-ANNOTATIONS` when nothing it is
  decorated with or defined in can read them and each changed one is deferred or evaluates only the
  standard typing vocabulary; a changed return annotation stays an output contract change. A module
  or a plain class whose annotations or type variables alone changed is one under the new
  `SDW-RULE-REFACTOR-TYPING`, and a module whose `__version__` string alone changed under the new
  `SDW-RULE-REFACTOR-VERSION`; like every refactor, all three are hidden by default. Unchanged
  symbols given a finding fall from 28 to 17 of 71 in development and, measured once after the
  correction, from 18 to 14 of 29 held out, and no behavior change loses its finding or its
  category. The decision is recorded in `docs/decisions.md` and its safeguards in
  `docs/security.md`; schema 1.5 and every golden are unchanged.

## 0.16.0 - 2026-11-02

Sharper categories, measured on real code. A labeled public benchmark of 60 pull requests from ten
repositories, half a development split and half a held-out one labeled before the analyzer
changed, measures it, and three corrections designed against it read code that runs no differently
as no change: what only a type checker reads, imports in step with the code using them, and a
reworded exception message; a changed call into the package's own code is an unknown change rather
than a dependency interaction. Held out, unchanged symbols given a finding fall from 34 to 23 of 39,
every behavior change still has one, and the categories named stay as they were. A minor release
for the two new refactor rules, `SDW-RULE-REFACTOR-IMPORTS` and `SDW-RULE-REFACTOR-MESSAGE`, both
hidden by default like every refactor; schema 1.5 and every golden are unchanged, and the decision
is recorded in `docs/decisions.md`.

- **Evaluation:** a labeled public benchmark. `benchmark/cases.json` pins 60 pull requests merged
  into ten public repositories by their commits, sampled without choosing them by hand, and labels
  every Python symbol they change outside their tests with the categories a reviewer expects, or
  none when its behavior does not change; one annotator wrote the labels before reading the
  analyzer's output. Half are a `development` split, from `psf/requests`, `pallets/click`,
  `psf/black`, `encode/httpx`, and `python-attrs/attrs`, which the analyzer is changed against, and
  half a `held_out` split, from `pallets/flask`, `pallets/werkzeug`, `encode/starlette`,
  `Textualize/rich`, and `marshmallow-code/marshmallow`, labeled before it first was.
  `scripts/run_benchmark.py` clones the repositories outside the working tree, measures one split or
  both, and lists each disagreement, and `docs/benchmark.md` records the measurements.
- **Fix:** code that runs no differently is no longer a change. A module's or a class's own
  statements are compared as they run, without the blocks of an `if TYPE_CHECKING:` that the file
  binds to typing's constant and cannot rebind; a module whose other statements are the same is a
  refactor, under the new `SDW-RULE-REFACTOR-IMPORTS`, when its imports changed in step with the
  code using them; and a body that differs only in the prose of the messages it raises is one under
  the new `SDW-RULE-REFACTOR-MESSAGE`. Like every refactor, both are hidden unless
  `rules.emit_low_risk_refactors` is set. On the benchmark, unchanged symbols given a finding fell
  from 18 to 7 of 34 in development and from 34 to 23 of 39 held out, and every behavior change
  still has one.
- **Fix:** a changed call into the package's own code is no longer a dependency interaction. What a
  relative import binds, or an import naming the top-level package the file sits in, is the
  project's own code, like the module's own functions, so a changed call that reaches only such code
  is an unknown change under `SDW-RULE-UNKNOWN-INTERNAL-CALL`, whose summary now says "the project's
  own code". Five development findings of the benchmark changed; none held out did.

## 0.15.0 - 2026-11-02

Tests to run. Every analysis names the test files its change can affect, by what they import, or
says why the whole suite has to run, and the new `select` subcommand prints them for a test runner,
exiting 4 when the whole suite has to run; the Action gains the `run-all-tests` and `selected-tests`
outputs. A changed file that is not Python runs the whole suite unless the new `selection.ignore`
says no test reads it. With it, the roadmap's v1.0 regression selection is partly done. A minor
release for schema 1.5, whose only addition is the analysis's `test_selection`; no finding, score,
verdict, or obligation changes, every golden changes in `schema_version` and gains `test_selection`,
and the decision is recorded in `docs/decisions.md`.

- **Feature:** the tests a change can affect. Every analysis names, as the new `test_selection`,
  the test files its change can affect — those that import a changed file, directly or through the
  repository's other modules, those below a changed `conftest.py` or plugin it names, and the
  changed test files themselves — or says why the whole suite has to run: a changed file that is
  not Python, unless the new `selection.ignore` says no test reads it, a changed `setup.py`, a
  changed file never read by policy, or a head revision whose tests' imports cannot be followed
  within the bounds. A test file that loads modules by a name it does not write out or from a file
  path, or that starts another Python, is selected whenever Python changed. The brief gains "Tests
  to run"; the new `select` subcommand prints the test files alone, one per line, without
  analyzing what changed, and exits 4 when the whole suite has to run; and the Action gains the
  `run-all-tests` and `selected-tests` outputs. A selected path is only letters, digits, and
  `_./-`, or the whole suite runs. Schema 1.5: the analysis gains `test_selection`.

## 0.14.0 - 2026-11-02

Tests that ran a change. A coverage run measured with per-test contexts says which test executed
each changed line, and each such test is now a candidate for the finding on its own, at 0.65 when it
executed every changed line the report measures and 0.55 when it executed some, and still
unverified. The contexts come from the run's `.coverage` data file, named beside its report and read
as an untrusted SQLite database through a hardened connection, or from a small JSON report written
with `show_contexts`. With 0.12.0's test history, this completes the roadmap's composition with test
and coverage history, the last open part of its v0.2. A minor release for the new input and its
setting; schema 1.4 and every golden are unchanged, both tools' input schemas gain `coverage_data`,
and the decision is recorded in `docs/decisions.md`.

- **Feature:** tests that ran a change. A run measured with per-test contexts, as `pytest
  --cov-context=test` records them, says which test executed each changed line, and each such test
  is now a candidate test for the finding on its own: at 0.65 when it executed every changed line
  the report measures and 0.55 when some, added to its other evidence and never verified, with the
  reason "executed 2 of the 4 changed lines in the coverage report". The contexts come from the
  run's `.coverage` data file, passed beside the report as the new `coverage_data`,
  `--coverage-data`, `coverage.data_path`, or the Action's `coverage-data`, or from a small JSON
  report written with `show_contexts`. The data file is an untrusted SQLite database, read from
  memory through a hardened `sqlite3` connection under the new `rules.max_coverage_data_bytes`, 100
  MB by default. Both tools' input schemas gain `coverage_data`; the analysis's schema is unchanged.

## 0.13.0 - 2026-11-01

Connected changes through module variables. A changed module-level variable is grouped with the
changed code that reads it, in its own module or through an import, and with whatever reaches that
code through calls: a changed `TIMEOUT_SECONDS = 60` in `limits.py` and the changed `fetch` that
reads it in `client.py` are one change to test through `fetch`, and the brief names the variable.
With it, the roadmap's cross-file change grouping is done. A minor release for schema 1.4, whose
only addition is each change group's `variables`; no finding, score, verdict, or obligation changes,
the 18 goldens change in `schema_version` alone, and the decision is recorded in
`docs/decisions.md`.

- **Feature:** connected changes through module variables. A finding in a module's top-level code
  that changed a variable's assignment now joins the group of each changed symbol that reads the
  variable, in its own module or through an import of it or of its module, or that reaches through
  calls code that does: a changed `TIMEOUT_SECONDS = 60` in `limits.py` is connected to the changed
  `fetch` that reads it in `client.py`. A name the code binds itself, such as a parameter, is its
  own. The brief names each variable: "`fetch` (src/shop/client.py, `bc-003`) reaches
  `TIMEOUT_SECONDS` (src/shop/limits.py, `bc-004`)". Schema 1.4: each change group gains
  `variables`, the names a finding in top-level code is reached through.

## 0.12.0 - 2026-11-01

Test history. The commits up to the base say which test files kept changing with a module: one that
changed with a changed Python module in two or more of the last `rules.max_history_commits` is a
candidate for its findings, as a whole file, whatever its name or imports say, and adds to a
candidate other evidence found. A minor release for the new evidence and its setting; schema 1.3,
both tools' interfaces, and every golden are unchanged, and the decision is recorded in
`docs/decisions.md`.

- **Feature:** test history. The commits up to the base are read for the files each one changed,
  and a test file that changed with a changed Python module in two or more of them is a candidate
  for its findings, as a whole file, whatever its name or imports say; for a test other evidence
  already ties to the module, it adds to its score. Its reason says how many: "changed with
  src/shop/money.py in 4 of its last 20 commits". Merges and commits of more than 30 files are left
  out. The new `rules.max_history_commits` sets how many commits are read, 200 by default and `0` to
  read none. The analysis's schema is unchanged.

## 0.11.1 - 2026-11-01

Code spans without backslashes. A symbol or a path the brief writes in a code span now shows as it
is: Markdown reads no escape inside a code span, so `parse_amount` had shown as `parse\_amount` in
every evidence line, candidate test, resolved baseline finding, and connected change. A patch
release; the analysis, schema 1.3, both tools' interfaces, and every golden are unchanged.

- **Fix:** a symbol or path in a code span of the brief no longer shows backslashes: Markdown reads
  no escape inside a code span, so `parse_amount` showed as `parse\_amount` in every evidence line,
  candidate test, resolved baseline finding, and connected change. A code span now holds its text
  as it is, control characters still made visible, between one backtick more than the longest run
  it holds. The analysis and its schema are unchanged.

## 0.11.0 - 2026-11-01

Connected changes. The findings in Python code that calls connect are grouped under the entry points
a test reaches the rest through: a changed `checkout` that calls a changed `parse_amount` is one
change to test through `checkout`, listed in the new `change_groups` and said in the brief under
"Connected changes". With it, the roadmap's dependency graph is done. A minor release for schema
1.3, whose only addition is `change_groups`; no finding, score, verdict, or obligation changes, and
the decision is recorded in `docs/decisions.md`.

- **Feature:** the findings in Python code that calls connect are grouped. A finding reaches
  another when its symbol's code calls the other's, directly or through unchanged code, and each
  group is led by an entry point, a finding no other of the group reaches, with every finding it
  reaches: a test through it can exercise them all. The analysis lists the groups in the new
  `change_groups`, and the brief says them under "Connected changes". Two findings that only share
  a caller are not connected, and grouping changes no finding, score, or obligation.
- **Schema:** 1.3, for the additive `change_groups`, an empty list when no call connects two
  findings. The 18 canonical goldens change in `schema_version` and that empty list alone.

## 0.10.0 - 2026-11-01

Configuration files. The configuration files a repository names in the new `paths.config_files`
globs are compared key by key, beside its Python and its API schemas: a changed retry count,
timeout, limit, switch, or service address is a finding with the old and new value, redacted, as its
evidence, and its candidate tests are those that use the key's names. With API schemas in 0.9.0,
this completes the roadmap's config and API schema awareness. A minor release for the new input;
schema 1.2 and both tools' interfaces are unchanged, and the decision is recorded in
`docs/decisions.md`.

- **Feature:** configuration files are compared where the repository names them, in the new
  `paths.config_files` globs. Each changed YAML, JSON, or TOML file named there is compared key by
  key: every value added, removed, or changed is a finding named by its dotted key path, with the
  old and new value, redacted, as its evidence, and classified by its key's words — access, retry
  or timeout, a limit on a number, validation, a switch, or a connection — or as a change of default
  behavior. Its candidate tests are those that use the key's names. Naming a file includes it,
  whatever `include` says; `exclude` still leaves one out. A named file that cannot be read, or
  does not parse, leaves the analysis incomplete. Schema 1.2 is unchanged.

## 0.9.0 - 2026-11-01

API schemas. A changed OpenAPI 3, Swagger 2, or JSON Schema document, in YAML or JSON, is now
compared beside the Python change, operation by operation and keyword by keyword, and an
operation's findings map to the tests that request its route. Whether a file is a schema, its
content decides, and every other YAML or JSON file is accounted for without costing an analysis
its `pass`. A minor release for the new input; schema 1.2 and both tools' interfaces are
unchanged, and the decision is recorded in `docs/decisions.md`.

- **Feature:** API schemas are analyzed beside Python. A changed YAML or JSON file whose root
  says it is an OpenAPI 3, Swagger 2, or JSON Schema document is compared operation by operation
  and keyword by keyword — operations, parameters, request bodies, responses, media types,
  security, servers, and the schema keywords that decide what is accepted or returned — and each
  change is classified by whether it is part of a request, a response, or a reusable schema. An
  operation's candidate tests are the tests that request its route. Every other YAML or JSON file
  is counted as `not_api_schema`, and one that cannot be read as `unreadable_document`, neither of
  which keeps an analysis from passing; a schema that does not parse is a finding, and does. The
  default `include` gains `**/*.yaml`, `**/*.yml`, and `**/*.json`, and
  `rules.analyze_api_schemas: false` turns the comparison off. Schema 1.2 is unchanged; the
  limitation for a diff with nothing to analyze now says "no included changed Python source or API
  schema", and the brief's line for no finding no longer says Python.

## 0.8.2 - 2026-11-01

Test skeletons beside their candidates. A module without a test file of its name now gets its
new one beside the obligation's first candidate test rather than in the first test root, so a
layered suite keeps its layout; the first test root stands for an obligation without such a
candidate. The analysis, both tools' schemas, the CLI, and the patch format are unchanged.

- **Fix:** a test skeleton for a module without a test file of its name now goes in a new file
  beside the obligation's first candidate test, rather than in the first test root: where a
  module's tests live in `tests/unit/`, its new `test_<module>.py` does too. A candidate places it
  only if it is a test file at the analyzed commit, a module's obligations share one new file
  whatever their candidates, and none overwrites a module already at that path. Without a
  candidate, the first test root stands.

## 0.8.1 - 2026-11-01

A packaging patch. A source distribution built from a working copy no longer carries Claude
Code's per-user `.claude/settings.local.json`, and the artifact check rejects one that does;
hatchling, the build backend, moves to 1.33.0, which packages the same files. The package, its
two tools, the CLI, schema 1.2, and every output are unchanged.

- **Fix:** a source distribution built from a working copy no longer carries Claude Code's
  per-user `.claude/settings.local.json`. Hatchling honors the project's `.gitignore` but not a
  global Git ignore, which was all that kept the file out of this checkout's commits, so a local
  build packed it; CI builds from a clean checkout, and no source distribution was published.
  The sdist now excludes `/.claude`, `.gitignore` lists the settings file, and
  `scripts/verify_wheel.py` rejects a distribution with a `.claude` path.

## 0.8.0 - 2026-11-01

Test skeletons as a tool. `generate_test_skeletons` joins `analyze_semantic_diff` on the Hermes
plugin and the MCP server: given the IDs of the obligations the user approved, it returns the
patch of failing tests the `skeletons` subcommand prints, and never applies it. It names the
analysis by the analysis tool's own arguments, analyzing again without inference so a brief's
IDs carry over, or by `analysis_path` for a saved one. A minor release for the new tool; the
analysis tool, its schema 1.2, and every output of an analysis are unchanged. The decision is
recorded in `docs/decisions.md`.

- **Feature:** the `generate_test_skeletons` tool, served by the Hermes plugin and the MCP server
  beside `analyze_semantic_diff`. It is the `skeletons` subcommand as a tool: given the IDs of the
  obligations the user approved, it returns a patch adding a failing test for each, and never
  applies it. It names the analysis either by the analysis tool's own arguments, which it analyzes
  again without inference so a brief's IDs name the same obligations, or by `analysis_path`, the
  JSON of a saved analysis, for one made with inference. The Claude Code skill scaffolds only the
  obligations the user names. The analysis tool, its schema, and every output of an analysis are
  unchanged.

## 0.7.0 - 2026-11-01

Test skeletons for approved obligations. The new `skeletons` subcommand reads an analysis saved
with `--format json` or `both` and prints a patch adding a failing pytest or unittest test for
each obligation the user names, at the end of the changed module's test file or in that file
created in the first test root; the user reviews the patch and applies it, and nothing is
written. A minor release for the new subcommand and the new, additive `analysis_unreadable`
error code. The analysis command, its schema 1.2, and every output of an analysis are
unchanged. The decision is recorded in `docs/decisions.md`.

- **Feature:** test skeletons for approved obligations. `semantic-diff-weaver skeletons --analysis
  analysis.json --obligation to-001` reads an analysis saved with `--format json` or `both` and
  prints a patch adding a failing test for each approved obligation: at the end of the test file
  named for the changed module, one of the obligation's candidate tests first, or in that file
  created in the first test root. `--framework unittest` writes `unittest.TestCase` methods instead
  of pytest functions, and `--format json` returns the patch with where each test went and why any
  obligation got none. The patch is printed for review and `git apply`, never applied, and each
  test fails until it is written. The analysis is read as untrusted data, and its text reaches the
  test file only as escaped comments. An unusable analysis is the new, additive
  `analysis_unreadable` error; the analysis command and its schema are unchanged.

## 0.6.2 - 2026-11-01

Two classifier corrections. A changed `print` is a side effect rather than a dependency
interaction, and a changed assignment reads only the assignments that changed: assignments were
the last probed feature to read every value its delta showed. The schema stays at 1.2 and no
field, taxonomy value, or error code changes. On this repository's history and
`adaptive-policy-compilation`'s, 32 call classifications move from a dependency interaction to a
side effect, which raises their risk, and 11 state transitions become unknown changes, which
lowers it; one range's verdict moves, from `warn` to `review_required`. Both corrections are
measured in `docs/decisions.md`.

- **Fix:** a changed `print` is a side effect. It was a dependency interaction, with obligations
  about a dependency's failures and unexpected responses; `print` is now a side-effect term beside
  `warn`, so a changed line of output gets the side-effect obligations, the write occurring as
  intended and not otherwise, as a changed `sys.stdout.write()` already did. Each finding that
  moves gains 8 risk points. On this repository's history 4 of 348 call classifications move and
  no verdict changes; on `adaptive-policy-compilation` 28 of 179 move, and one range's verdict
  moves from `warn` to `review_required`, a rename of the identifiers its scripts print. The real
  pull requests of `docs/real-pr-validation.md` and the evaluation corpus are unchanged.
- **Fix:** a changed assignment reads only the assignments that changed. It read the targets of
  every assignment its delta showed, so an unchanged `state = ...` beside a changed `count = ...`
  made the change a state transition; every probed feature now reads only what changed, as
  comparisons, conditions, loops, and calls already did. On this repository's history 10 of 23
  state transitions among 389 assignment classifications become unknown changes, and on
  `adaptive-policy-compilation` 1 of 3 among 201; no range's highest risk rises and no verdict
  moves. The real pull requests of `docs/real-pr-validation.md` and the evaluation corpus are
  unchanged.

## 0.6.1 - 2026-10-31

A changed call is classified by what it reaches. A call to the module's own code — its functions and
classes, its classes' methods, and builtins that compute in-process — is an unknown change, not a
dependency interaction with obligations about a dependency's failures; the call rule reads its
keywords from the calls that changed; and a new warning is a side effect. The schema stays at 1.2
and no field, taxonomy value, or error code changes, but ranges that change calls are classified
differently, mostly from a dependency interaction to an unknown change: their findings and
obligations fall, and a range's highest risk falls in 31 pairs and rises in 2. The correction is
measured in `docs/decisions.md`.

- **Fix:** a changed call to the module's own code is no longer a dependency interaction.
  `SDW-RULE-DEPENDENCY` made every changed call that no keyword explained a
  `dependency_interaction_change`, with obligations about a dependency's failures and unexpected
  responses, so `self.get_help_spec(ctx)` replacing `self.make_metavar(ctx)` was one. A call
  change is now an unknown change, under the new `SDW-RULE-UNKNOWN-INTERNAL-CALL`, when every call
  that changed reaches the module's own code: a function or class it defines, a method one of its
  classes defines, called through `self`, `cls`, or the class, or a builtin that computes
  in-process, such as `len`. What an attribute holds is not known, so `self.client.fetch()` stays
  a dependency call. The call rule also reads its keywords from the calls that changed, so an
  unchanged `write_text(...)` no longer makes the call changed beside it a side effect, and `warn`
  is a side-effect term, so an added `warnings.warn` is one. On this repository's history 126 of
  348 call classifications move, and on `adaptive-policy-compilation` 62 of 179; a range's highest
  risk falls in 31 pairs and rises in 2. On the real pull requests of `docs/real-pr-validation.md`
  the dependency findings fall from 15 to 10. The evaluation corpus is unchanged.

## 0.6.0 - 2026-10-31

The GitHub Action compares each push with the previous one. Its pull-request comment keeps a
hidden digest of its analysis, and the next run reads it back and passes it to `--baseline`, so
the brief says which findings are new, updated, unchanged, or resolved since; the new `baseline`
input defaults to `previous`. The action now edits, and reads its baseline from, only the comment
its own identity wrote. A minor release for the new input; the analyzer, its schema 1.2, its CLI,
and every output are unchanged. The decision is recorded in `docs/decisions.md`.

- **Feature:** the GitHub Action compares each push with the previous one. Its comment now ends
  with a hidden digest of its analysis — each finding's category, path, symbol, risk, and an
  evidence fingerprint, compressed, with no evidence text — and the next run reads it back and
  passes it to `--baseline`, so the brief says which findings are new, updated, unchanged, or
  resolved since. The new `baseline` input defaults to `previous`; `none` turns it off, and a
  path compares with an earlier analysis's JSON. A digest that cannot be read or compared is
  reported in the log and left out, never failing the run.
- **Security:** the action edits, and reads its baseline from, only the comment its own identity
  wrote. The marker alone matched before, so a comment anyone posted starting with it, ahead of
  the action's first, was edited in its place, which GitHub refuses, and would have been read as
  the baseline. `scripts/pr_comment.py` gains `--author`, `github-actions[bot]` by default.

## 0.5.0 - 2026-10-31

An analysis now says what it advises and how it moved since an earlier one. The summary carries
an advisory `pass`, `warn`, or `review_required` verdict read from the overall risk under
configurable thresholds, which an analysis that left scope unread never passes, and `--baseline`
marks each finding new, updated, unchanged, or resolved against an earlier analysis's JSON. A
minor release, because the schema moves to 1.2 with the new `verdict` field and the optional
`baseline` object; every existing field, value, finding, and risk is unchanged, and
`baseline_unreadable` is a new, additive error code. Each decision is recorded in
`docs/decisions.md`.

- **Breaking (schema):** `SCHEMA_VERSION` is now `"1.2"`. `summary` gains a required `verdict`,
  `pass`, `warn`, or `review_required`, and the analysis an optional `baseline` object, `null`
  without a baseline. Every existing field and value is unchanged, but a consumer that validates
  the result strictly sees new fields and needs a version signal. `baseline_unreadable` is a new,
  additive error code.
- **Feature:** compare with an earlier analysis. `--baseline PATH`, or `baseline_report` in the
  tool arguments, names the JSON an earlier run wrote; each finding is then `new`, `updated`, or
  `unchanged`, matched by category, path, and symbol and compared without line numbers, and the
  earlier findings no longer reported are `resolved`. The brief counts each group and marks new
  and updated findings beside their IDs. The baseline is untrusted input: bounded by the new
  `rules.max_baseline_bytes`, validated field by field, and never rendered by its own prose, so a
  crafted file cannot write into the brief. An unusable one is a `baseline_unreadable` error.
- **Feature:** an advisory verdict. The summary says `pass`, `warn`, or `review_required`, read
  from the overall risk under the new `verdict` configuration section — `warn` from `warn_at`,
  `medium` by default, and `review_required` from `review_required_at`, `high` by default, either
  of them `none` to turn it off. An analysis that left scope unread or compared a commit with
  itself is never `pass`, whatever the thresholds say. The brief, and so the pull-request comment,
  opens with the verdict, and the GitHub Action exposes it as the `verdict` output. Nothing blocks
  on it: `--fail-on` still decides the exit code. On the evaluation corpus it is 3 `pass`, 12
  `warn`, and 3 `review_required`.

## 0.4.1 - 2026-10-31

Findings are classified by what changed. A comparison or condition is a boundary only when what
changed orders values, a loop is a retry only when it names one, a signature change that only
touches typing says so, and a return change is no longer hidden by an unrelated comparison;
the real pull-request validation now samples squash-merged pull requests. The schema stays at
1.1 and no taxonomy value or error code changes, but ranges that change comparisons, conditions,
or loops are classified differently, nearly always from a boundary or a retry to an unknown
change, and no range's highest risk rises. Each correction is measured in `docs/decisions.md`.

- **Tests:** `scripts/validate_real_prs.py` samples squash-merged pull requests and counts each
  pull request once. It took the two-parent merges reachable from the default branch, so
  `psf/black` and `encode/httpx`, which squash, contributed merges from 2018 and 2019, and a pull
  request merged into another branch was reported alone and again inside the merge that brought
  that branch in. It now reads the default branch's first-parent history and takes each commit
  whose subject names a pull request, reviewing a squash merge from its parent and a merge commit
  from where its pull request branched off. `docs/real-pr-validation.md` is regenerated on the
  five latest pull requests of the same four repositories, with a new hand-reviewed sample: four
  of ten findings are plausible as presented, against two in the previous one.

- **Fix:** an output-contract finding says what changed. A signature change that leaves what a
  caller passes alone — only its annotations, return annotation, type parameters, or type comments
  differ — is now worded as typing alone, under the new `SDW-RULE-SIGNATURE-ANNOTATIONS`, with the
  assumption that annotations matter only where something reads them; it stays an
  `output_contract_change`, where it used to ask callers to supply different arguments, and gives
  way to any other change its finding holds. And a changed comparison no longer drops every return
  change in its symbol, only one whose changed values each show it: click's
  `Argument.get_help_record` no longer returning `None` was hidden that way. On this repository's
  history 29 of 89 signature classifications are typing alone and 34 return changes are reported
  again, on `adaptive-policy-compilation` 1 of 54 and 26; no classification moves and no range's
  highest risk rises. One corpus golden changes its text and confidence, and precision and recall
  stay at 100%.

- **Fix:** a changed loop is a retry only when its names say so. `SDW-RULE-LOOP` made every
  changed loop a `retry_timeout_change`, so a `for` over a command's parameters and a `while` that
  trimmed words until a help text fit came with obligations to recover before a retry limit and
  to stop on a terminal failure. A loop is now a retry when what it iterates over or stops on, or
  its symbol's name, says `attempt`, `retry`, `timeout`, `sleep`, `backoff`, or `deadline` — not
  `limit`, which every loop has — read from a new AST probe of the loops that changed; any other
  is an unknown change under the new `SDW-RULE-UNKNOWN-LOOP`. All 54 loop classifications on this
  repository's history and all 45 on `adaptive-policy-compilation` move to unknown, none of them
  a retry, and no range's highest risk rises. The evaluation corpus is unchanged.

- **Fix:** a changed comparison or condition is a boundary only when what changed orders values.
  `SDW-RULE-BOUNDARY` made every changed comparison a `boundary_change` without reading its
  operator, so `is None` and `in STARS` were boundaries, and the condition rule counted every `==`
  and `!=` in every condition its delta showed, changed or not. A boundary is now an ordering, or
  an equality with a number or a `len()`, among the values that changed: `method == 'GET'` and
  `ch.type == token.STAR` order nothing. A comparison that is no boundary is read like such a
  condition, by its authorization, validation, and retry terms or as an unknown change under the
  new `SDW-RULE-UNKNOWN-COMPARISON`, and joins the condition's finding; `user.role == 'admin'`
  becomes an authorization guard. Both rules read their keywords from the changed values too, so
  an unchanged `row['valid']` no longer makes the change beside it a validation guard. Of 1,594
  classifications on this repository's history and 3,186 on `adaptive-policy-compilation`, 169
  and 86 move, nearly all from boundary to unknown, and no range's highest risk rises; on the
  real pull requests of `docs/real-pr-validation.md` the boundary findings fall from 13 to 2. The
  evaluation corpus is unchanged.

## 0.4.0 - 2026-10-31

The tool reaches Claude Code, and its candidate tests follow the code that reaches a change. A
Claude Code plugin serves the same tool over MCP; candidate mapping follows calls through
instances, packages, `importlib`, and module variables; changed tests are no longer analyzed as
product code; the GitHub Action reviews a pull request from where it branched off; and the
Anthropic adapter works with SDK 1.x and 2.x and the 5.5 models. The schema stays at 1.1 and no
error code changes, but findings, obligations, and candidate tests change on most ranges: each
output-affecting correction is recorded in `docs/decisions.md`.

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

- **Tests:** `scripts/validate_real_prs.py` reviews each merge from where its pull request
  branched off. It compared a merge's two parents as they were, so every commit the target branch
  gained after the branch point read as reverted by the pull request: on the 14 merges of
  `docs/real-pr-validation.md`, 150 of the 163 findings today's analyzer reports that way come
  from such commits, and the 166-finding merge of the July page changed no Python file. The page
  is regenerated with a correction note and a new hand-reviewed sample. `SDW-RULE-BOUNDARY` still
  calls any changed comparison a boundary, identity tests included, and `SDW-RULE-LOOP` any changed
  loop a retry.

- **Tests:** the Hermes compatibility job tests 0.19.0, the current release, in place of 0.18.2,
  and checks the parameters the interpreter really passes. Its list of `complete_structured`
  parameters was kept by hand: it required `json_mode`, which the plugin never passes, and left
  out `temperature` and `purpose`, so a Hermes release that dropped either would have sent every
  model call to deterministic fallback with CI green. `scripts/verify_hermes.py` and the
  real-runtime test now require the parameters `LlmClient` declares. 0.14.0, 0.18.2, and 0.19.0
  all accept them, and nothing the plugin uses changed in 0.19.0. See `docs/decisions.md`.

- **Fix:** the Anthropic adapter works with SDK 1.x and 2.x, and no longer sends `temperature`.
  The extra allowed only 0.x releases, and the adapter passed `temperature` 0.1 to the models
  that still honor it, such as `claude-sonnet-4-6`, as a keyword argument that SDK 1.0 removed.
  Routing it through `extra_body` instead would not survive SDK 2.0, which sends API version
  2026-10-21, on which `temperature`, `top_p`, and `top_k` return HTTP 400 on every model. Both
  failures send every call to deterministic mode without an error. The value is now never sent:
  those models sample at their default temperature instead of 0.1, the default model,
  `claude-opus-5`, never received it, and `claude-haiku-5-5`, which rejects anything but the
  default, now works. See `docs/decisions.md`.

- **Fix:** the `anthropic` extra no longer admits releases before 0.77. Every request carries
  `output_config`, which `messages.create` accepts only from 0.77, so on 0.40 through 0.76, which
  the extra allowed, each call raised `TypeError` before it was sent and fell back to
  deterministic mode. The extra is now `anthropic>=0.77,<3`.

- **Fix:** `claude-opus-5-5` and `claude-sonnet-5-5` no longer fall back to deterministic mode on
  every call. The adapter turns thinking off so the interpreter's small `max_tokens` bounds the
  JSON, and sent both `{"type": "disabled"}`, which each rejects with HTTP 400. Claude Opus 5.5's
  thinking is always on, so it is now sent no `thinking` parameter, as the Fable and Mythos
  families are; Claude Sonnet 5.5 is sent `{"type": "between_tools"}`, its way of turning
  thinking off.

- **Fix:** an added module that is only a docstring is no longer a finding. Each package of
  `adaptive-policy-compilation` opens with an `__init__.py` that only describes it, and each was
  reported as new behavior beside the package's functions, while an empty `__init__.py`, whose
  diff has no hunk, never was. An added module whose top-level code is only a docstring, comments,
  or nothing now produces no finding; its definitions are still reported, and so are a removed
  module and an added one that runs anything. On `adaptive-policy-compilation`, 16 findings go and
  no risk score moves, and two ranges at the 100-symbol bound fit one more real symbol each.

- **Fix:** the GitHub Action reviews a pull request from where it branched off. `base-ref`
  defaulted to `github.event.pull_request.base.sha`, the base branch as it stood when the pull
  request opened, and the analyzer compares two trees as they are: commits the base branch gained
  before then read as reverted by the pull request, and once the head merged the base branch, as
  its own. Unless `base-ref` is set, the action now reviews from `git merge-base` of the base
  branch as checked out, which `fetch-depth: 0` already fetches, and the head, with the event's
  base commit standing in when that branch is missing. An explicit `base-ref` is compared as
  given.

- **Fix:** an obligation that several findings share no longer gives every place to one test
  file. Findings sharing an obligation pool their candidate tests, cut to five, and among equal
  scores the cut went by path: on `adaptive-policy-compilation`, a new package's 37 findings and a
  changed `CODE_ROOTS` tuple shared one obligation, every candidate scored 0.60, and all five
  places went to `tests/test_escalations.py`. Among equal scores the test files now take turns,
  so that obligation shows three of those tests and both tests reading `CODE_ROOTS`. A lower score
  never comes before a higher one, and no finding, score, or coverage status moves; 65 of 493
  obligations there and 184 of 726 on this repository show other tests.

- **Fix:** the tests that read a changed module variable are named. A change to a module's
  top-level code has the symbol `<module>`, which no test can name, so its tests stood only as
  their file: adding an entry to `adaptive-policy-compilation`'s `CODE_ROOTS` tuple got
  `tests/test_provenance.py` as a whole file, although two of its tests read the tuple. An
  assignment change now carries the variables it writes to, `CODE_ROOTS` alone among the
  module's assignments, and a test whose body or name uses one is listed by name with the reason
  `changed module variable in bounded test body` or `changed module variable in test name`; the
  rest of its file still stands as the file. Over their first-parent histories, 31 module findings
  of `adaptive-policy-compilation` gain 86 named tests and 31 of this repository's gain 55, and
  five risk scores fall where a module change without a candidate gains one.

- **Fix:** a changed assignment is a state transition only when it is named for state.
  `SDW-RULE-STATE` made every changed assignment a `state_transition_change`, so adding an entry
  to a module constant, `CODE_ROOTS`, led a brief with obligations to verify, reject, and repeat
  a transition, and set its overall risk at 51 where every other finding scored 42. The rule now
  needs `state`, `status`, or `transition` in what the assignment writes to or in its symbol's
  name, read from the AST so that prose and values do not count; any other changed assignment is
  an `unknown_semantic_change` under the new `SDW-RULE-UNKNOWN-ASSIGNMENT`. Of 197 assignment
  classifications on `adaptive-policy-compilation` and 329 on this repository, 194 and 310 move
  to unknown, no other classification moves, and every risk score that changes falls. A new
  corpus case, `module constant`, keeps it, and the corpus stays at 100% precision and recall.

- **Fix:** a test that runs a changed function comes before one that only loads its module.
  `semantic_candidates.py` runs its classifiers from `CLASSIFIERS`, a table no call names them in,
  so the call graph reached `_classify_condition` only by loading the module, alike for every test
  importing it: `test_every_condition_site_records_its_probe`, which extracts symbols, led ahead
  of `test_the_condition_rule_reads_comparisons_from_operators`. Code reading a module-level
  variable now uses what its value names without calling it — functions in a tuple, list, set,
  or dict, through the variables and imports it copies and a call's arguments — while
  `repo = Repository(path)` called its class when the module loaded and names none. Among equal
  scores and shared words, a test reaching the symbol through calls now comes before one that
  only loads a module naming it: `_classify_condition` is led by the condition rule's test,
  `verify_wheel._installed_script` by `test_main_installs_only_after_both_archives_pass`, and
  `meal-manager`'s repository constructors by the tests that make a repository. Only the order
  changes: no score, reason, or risk score moves.

- **Fix:** the words a test shares with a change weigh by how much they single it out. Every
  shared word counted once, so `json`, in the name of every repository of `meal-manager`, put a
  test of atomic JSON writes before `test_add_dish_dict` for `JsonDishRepository`. A word now
  weighs by how few symbols of the modules in the changed file's directory name it too, an
  inverse document frequency, and a function named by one word, a verb such as `run` or
  `_uses`, adds none, as the name signal already held: `CallGraph._uses` keeps the graph's own
  tests rather than the mapper's tests whose names say "uses". Only the order changes again:
  `JsonDishRepository`, `JsonAliasRepository`, `JsonHistoryRepository`, and
  `JsonTuningRepository` keep their own domain's tests instead of the atomic-write ones.

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
