# Public benchmark

Measured 2026-11-02 in deterministic mode, by `scripts/run_benchmark.py`, against the labels of
`benchmark/cases.json` as they then stood: 60 pull requests merged into ten public Python
libraries, with every Python symbol they change outside their tests labeled by one annotator before
the analyzer's output on them was read, and every label then checked by a reviewer.
`benchmark/README.md` says how the sample was drawn and what each label means.

The cases came in two splits of 30. The **development** cases, six pull requests each of
`psf/requests`, `pallets/click`, `psf/black`, `encode/httpx`, and `python-attrs/attrs`, are the ones
the analyzer was changed against. The **held-out** cases, six each of `pallets/flask`,
`pallets/werkzeug`, `encode/starlette`, `Textualize/rich`, and `marshmallow-code/marshmallow`, were
labeled before the first such change and measured it on code it was not designed from.

Since then the first held-out cases have joined development, and a second held-out split of 30
pull requests, from `encode/django-rest-framework`, `jd/tenacity`, `urllib3/urllib3`,
`agronholm/anyio`, and `pypa/packaging`, has been labeled and reviewed before any further change.
Its baseline and the second round's correction, measured on it, come first below, then a
measurement of the development cases with a model; the first round's results follow.

Unlike the 18-case corpus of `docs/evaluation.md`, whose cases were written to exercise the
taxonomy, these are changes as maintainers made them.

## Baseline for the next correction

Measured 2026-11-03 with Semantic Diff Weaver 0.16.0 on the splits as they now stand: the 60
development cases, which are the first round's two splits together, and the 30 held-out cases of
the second split, before any correction is designed from the development ones.

| Measure | Development, 60 cases | Held-out, 30 cases |
| --- | --- | --- |
| Labeled symbols | 162 | 89 |
| Behavior changes | 76 | 44 |
| Unchanged symbols | 71 | 29 |
| Findings on Python | 184 | 123 |
| Precision, findings in an expected category | 32 of 171 (18.7%) | 16 of 107 (15.0%) |
| Precision, findings naming a behavior category | 32 of 84 (38.1%) | 16 of 50 (32.0%) |
| Recall, behavior changes given a finding | 76 of 76 | 44 of 44 |
| Recall, behavior changes given one in an expected category | 30 of 76 (39.5%) | 16 of 44 (36.4%) |
| Unchanged symbols given a finding | 28 of 71 (39.4%) | 18 of 29 (62.1%) |
| Unchanged symbols given one naming a behavior category | 16 of 71 (22.5%) | 7 of 29 (24.1%) |

The development column adds up the first round's two 0.16.0 columns below. Findings on an added or
removed symbol without a category, 13 in development and 16 held out, are neither right nor wrong
and are left out of precision. No finding fell on an unlabeled symbol, and the held-out split's
only files with findings are Python.

For the held-out split only these measures were recorded. Its findings by category and by case,
and each disagreement, stayed unread until the second round's correction, designed from the
development cases, was measured on it, so that nothing read from it could shape that correction.

## Second round

Designed from the 60 development cases alone, measured 2026-11-04 on both splits, and released in
0.17.0, the second round reads what only a type checker reads as changing no behavior: a
callable's parameter annotations, a module's or a plain class's annotations and type variables,
and a module's version string (`docs/decisions.md`, 2026-11-04, typing and versions).

| Measure | Development, 0.16.0 | Development, 0.17.0 | Held-out, 0.16.0 | Held-out, 0.17.0 |
| --- | --- | --- | --- | --- |
| Findings on Python | 184 | 172 | 123 | 119 |
| Precision, findings in an expected category | 32 of 171 (18.7%) | 32 of 159 (20.1%) | 16 of 107 (15.0%) | 16 of 103 (15.5%) |
| Precision, findings naming a behavior category | 32 of 84 (38.1%) | 32 of 78 (41.0%) | 16 of 50 (32.0%) | 16 of 50 (32.0%) |
| Recall, behavior changes given a finding | 76 of 76 | 76 of 76 | 44 of 44 | 44 of 44 |
| Recall, behavior changes given one in an expected category | 30 of 76 (39.5%) | 30 of 76 (39.5%) | 16 of 44 (36.4%) | 16 of 44 (36.4%) |
| Unchanged symbols given a finding | 28 of 71 (39.4%) | 17 of 71 (23.9%) | 18 of 29 (62.1%) | 14 of 29 (48.3%) |
| Unchanged symbols given one naming a behavior category | 16 of 71 (22.5%) | 10 of 71 (14.1%) | 7 of 29 (24.1%) | 7 of 29 (24.1%) |

In development 11 unchanged symbols lost their finding: five callables whose parameter
annotations alone widened, among them rich's three `save_*` methods; four classes or modules whose
annotations or type variables alone changed, such as requests' `Request`; and the version strings
of httpx and starlette. Held out, 4 did: encode/django-rest-framework's two releases, and the two
classes whose annotations tenacity#680 marked `ClassVar`. No behavior change lost its finding or
its category on either split. The held-out split has now been measured with a correction and its
disagreements read, so the next correction waits for a new held-out sample.

## With a model

Measured 2026-11-07 by `run_benchmark.py llm` with Semantic Diff Weaver 0.18.0 on the 60
development cases, with `openai/gpt-oss-120b` through OpenRouter at the interpreter's temperature
of 0.1 and low reasoning effort. No held-out case is ever sent to a model. The model answered every
batch of every case, so all 60 count. Its 81 calls cost 0.0236 USD, and an analysis with it took
4.4 seconds at the median and 11.3 at most.

| Measure | Development, without a model | Development, with gpt-oss-120b |
| --- | --- | --- |
| Findings on Python | 172 | 174 |
| Precision, findings in an expected category | 32 of 159 (20.1%) | 32 of 161 (19.9%) |
| Precision, findings naming a behavior category | 32 of 78 (41.0%) | 32 of 78 (41.0%) |
| Recall, behavior changes given a finding | 76 of 76 | 76 of 76 |
| Recall, behavior changes given one in an expected category | 30 of 76 (39.5%) | 30 of 76 (39.5%) |
| Unchanged symbols given a finding | 17 of 71 (23.9%) | 19 of 71 (26.8%) |
| Unchanged symbols given one naming a behavior category | 10 of 71 (14.1%) | 10 of 71 (14.1%) |
| Mean confidence, findings in an expected category | 0.80 over 32 | 0.80 over 32 |
| Mean confidence, findings in another category | 0.69 over 101 | 0.69 over 101 |
| Mean confidence, findings on an unchanged symbol | 0.71 over 26 | 0.71 over 28 |
| Test obligations | 186 | 407 |

**The model left the findings as they were, but for two wrong ones.** It cannot change a finding's
category (`docs/decisions.md`, 2026-11-06), and the two findings it added were unknown changes on
symbols labeled unchanged, rich#3941's `Console.save_html` and marshmallow#2935's `Nested.__init__`,
where the structural rules had found a refactor. Its support tells right findings from wrong ones
only weakly: it supported 23 of the 32 in an expected category and 74 of the 129 others, a
difference a two-sided Fisher test puts at p = 0.16, and the confidence it moved averages out,
leaving each outcome's mean where it was. No reply cited evidence the model was not shown; four
behaviors named a category their evidence did not support, and the analysis made them unknown
changes.

**A correction since keeps a model's claims of a change off a refactor's evidence**
(`docs/decisions.md`, 2026-11-07). Replaying a later run's replies through the analysis before the
correction and after it, the correction removes those two findings and two on symbols no label
covers, and leaves unchanged symbols given a finding at 17 of 71, as without a model. The replay
also shows how far two runs of one model differ: through the analysis before the correction, the
later run's replies added 192 obligations and five findings where the first run's added 221 and
two, and 6 of its 87 replies failed the response schema, each answered when retried, where none of
the first run's 81 did.

**What the model adds is test obligations.** Of the 407 with the model, 221 are its own; the run
without it has the other 186.

### The obligations, reviewed blind

A reviewer judged 40 of them on a page that showed each obligation's text with its pull request, its
symbols, and the hunks of the diff its evidence covers, but neither the run it came from nor the
existing tests the analysis named for it: 20 drawn at random from the model's 221 and 20 from the
deterministic 186, at most two from one case. An obligation was useful if the reviewer would write
that test or one much like it, noise if it was generic, repeated, or beside the change, and unsure
if the page did not show enough to decide, and each verdict came with a note saying why.

| Obligations | Drawn from | Cases | Useful | Noise | Unsure |
| --- | --- | --- | --- | --- | --- |
| The model's | 221 | 18 | 6 | 13 | 1 |
| Deterministic | 186 | 15 | 0 | 20 | 0 |

Six of 20 against none of 20 is a difference a two-sided Fisher test puts at p = 0.02. With 20 a
side, 95% Wilson intervals put the model's useful share between 14.5% and 51.9%, and the
deterministic one below 16.1%.

**The deterministic obligations are templates that name nothing to test.** They are the fixed
scenarios `taxonomy.py` keeps for each category and read the same in every case: "Handle dependency
success" is given "the dependency returns a successful response", when "the changed interaction
runs", then "the success response is consumed as intended". Thirteen of the 20 were the three
scenarios of a dependency interaction, a category wrong in 32 of its 43 findings in development, but
the nine that belong to a finding in an expected category were noise too. Nearly every note names a
test the diff does support instead, such as that `[::1]:8080` splits into the host `::1` and the
port 8080 for flask#6096, and some scenarios do not fit their change: a dependency failure where no
dependency fails, or the "value, status, fields, and container shape" of a function that returns
text.

**The model's useful obligations name the behavior a test should pin**: for starlette#3736, that an
endpoint wrapped in `functools.partial` is resolved and listed in the schema; for click#3777, that
the temporary file is removed when the pager fails; for click#3769, that a finished progress bar
writes its end once. None of the six belongs to a finding in an expected category. Three are on
symbols labeled unchanged, rewrites whose kept behavior is still worth a test, such as requests'
`_encode_files`, and three on findings in another category. All 20 of the model's that were drawn
belong to findings the run without it also reports, so the model added tests to findings, not
findings.

**Most of the model's noise states no check.** Five of its 13 ask for documentation, three of them
for documentation already written; one asks to investigate a contract the diff documents, and one
calls the contract unknown. Two misread the diff, taking a context line for the change, or
overloads for a change of spacing. Four propose a check that would miss the change or add nothing:
callers matching an error message by substring, a generator expression that need not reach the new
branch, a `None` the signature does not allow, and a count of a list's elements that another of its
obligations already covers. The one unsure is a call whose definition the page's diff did not show.

The review has limits of its own. One person judged all 40, and the page hid each obligation's run
but not its wording, which for a deterministic obligation is the same wherever it appears. Drawing
at most two from a case weighs the cases with many obligations less than the pool does. And it is
one run of one model, whose next run proposes other obligations.

### The obligations, reviewed again

A correction since writes a Python finding's obligations from its evidence (`docs/decisions.md`,
2026-11-07, obligations from evidence), and was measured as the first review was, with its
criterion fixed beforehand: at least 6 of 20 deterministic obligations useful. The second review
drew 40 obligations with another seed from a replay of the later run's replies through the
correction's first version.

| Obligations | Drawn from | Cases | Useful | Noise | Unsure |
| --- | --- | --- | --- | --- | --- |
| The model's | 182 | 14 | 12 | 8 | 0 |
| Deterministic, quoting their evidence | 176 | 15 | 3 | 16 | 1 |

It missed the criterion. Three against none of the first review is no difference 20 a side can
tell from chance (two-sided Fisher p = 0.23), and neither is the model's 12 against its 6 (p =
0.11), which shows how far one round differs from the next. A spy on a changed call was useful once
in six, on black#5645's HTTP request, and pinned the code's writing the other five times, on `next`,
`isinstance`, `str.lower`, `environ.get`, or a file's `close`. Obligations on an assignment, or on
"the code that reads" a module-level name, were noise all six times: they said where to look, but
not what to call or assert. The correction was iterated once on exactly those, as its decision
records, and is measured again by a third review.

## Results

Each split measured with Semantic Diff Weaver 0.15.0, and with 0.16.0, which makes the three
corrections of `docs/decisions.md` (2026-11-02, benchmark categories), against the reviewed labels.

| Measure | Development, 0.15.0 | Development, 0.16.0 | Held-out, 0.15.0 | Held-out, 0.16.0 |
| --- | --- | --- | --- | --- |
| Labeled symbols | 72 | 72 | 90 | 90 |
| Behavior changes | 31 | 31 | 45 | 45 |
| Unchanged symbols | 34 | 34 | 37 | 37 |
| Findings on Python | 80 | 64 | 131 | 120 |
| Precision, findings in an expected category | 10 of 73 (13.7%) | 10 of 57 (17.5%) | 22 of 125 (17.6%) | 22 of 114 (19.3%) |
| Precision, findings naming a behavior category | 10 of 32 (31.2%) | 10 of 23 (43.5%) | 22 of 61 (36.1%) | 22 of 61 (36.1%) |
| Recall, behavior changes given a finding | 31 of 31 | 31 of 31 | 45 of 45 | 45 of 45 |
| Recall, behavior changes given one in an expected category | 10 of 31 (32.3%) | 10 of 31 (32.3%) | 20 of 45 (44.4%) | 20 of 45 (44.4%) |
| Unchanged symbols given a finding | 18 of 34 (52.9%) | 7 of 34 (20.6%) | 32 of 37 (86.5%) | 21 of 37 (56.8%) |
| Unchanged symbols given one naming a behavior category | 6 of 34 (17.6%) | 1 of 34 (2.9%) | 15 of 37 (40.5%) | 15 of 37 (40.5%) |

Findings on an added symbol, 7 in development and 6 held out, are neither right nor wrong and are
left out of precision. No finding fell on an unlabeled symbol. `black.schema.json`, which black#5643
also changes, is the one finding outside Python, counted apart. A finding names a behavior category
unless it is `unknown_semantic_change`, which says something changed without saying what, or
`refactor_likely_no_behavior_change`, which says nothing did.

## What the review changed

A reviewer checked all 162 labels on a page that showed each symbol's diff and never the analyzer's
findings, and corrected 26, each with its reason in the commit that made it: three drop a
validation change the code does not make, 21 add categories a reviewer could fairly give, and two
find a behavior change in a symbol labeled unchanged. One is rich#3845's id counter, which draws
from the shared random generator as its module loads; the other is werkzeug#3332's `send_file`,
which now stores a `Request` in the caller's environ even when `conditional` is false.

No development measure moved. Held out, six findings that were wrong are now right: four
dependency interactions, on marshmallow's `Field.deserialize` and `Nested.schema`, werkzeug's
`wrap_file`, and werkzeug's `serving` module, the error-handling change on marshmallow's
`Email.__call__`, and `send_file`'s side effect. With 0.16.0, 22 of the 61 findings naming a
category are right where 16 were, 20 of 45 behavior changes have a finding in an expected category
where 15 of 43 had, and 21 of 37 unchanged symbols have a finding where 23 of 39 had. 0.15.0 moved
alike, since none of these findings is one the 0.16.0 corrections changed.

## What the numbers say

**The corrections removed findings on code that does not run differently, and moved no category to
a right one.** In development, 11 of the 18 unchanged symbols with a finding lost it: seven modules
whose imports or `if TYPE_CHECKING:` blocks alone changed, and httpx#3519's four reworded messages.
Five dependency interactions became unknown changes, calls into the package's own code such as
requests' `_t.has_read(data)` and black's `is_yield(node)`. Every behavior change still has a
finding, and the 10 in an expected category are the same 10.

**The held-out split confirms the first correction only.** 11 of its 37 unchanged symbols lost
their finding, and all 11 were a module's imports or type-checking block: werkzeug's, flask's,
starlette's, and rich's. None of its findings was a reworded message, and none of its dependency
interactions called only its package's own code, so the categories it names did not change: 22 of
61 are right, as before. The held-out split was measured once after the corrections, and again
against the reviewed labels; a refinement for a development case and the safeguards of
`docs/security.md` came after the first, and changed none of its results.

**What remains on unchanged symbols in development** is a class's annotations, requests' `Request`
twice; a widened annotation, `Editor.edit_files`, named an output contract; a version string; and
three rewrites that keep their behavior, `get_pager_file`, `astuple`, and `_encode_files`.

**Held out, a module's constants and a signature's annotations still read as changes.** Of the 21
unchanged symbols with a finding, eight are modules: seven add, drop, or rewrite a constant — a
compiled pattern such as starlette's `PARAM_FORMAT_REGEX`, a `TypeVar`, a string — and one is
starlette's version string. Five of the seven name a dependency interaction for the `re.compile`
or `TypeVar` call that builds the constant. Five more are signatures whose annotations alone
change, rich's three `save_*` methods among them, named output contracts.

**Half the findings still name no category.** 34 of the 57 scored findings in development and 53 of
the 114 held out are unknown changes, nearly all on behavior changes, where the analyzer abstains
from naming one.

**A changed call is still most often named a dependency interaction by mistake.** 3 of the 12 in
development and 8 of the 32 held out are right. Most of the rest are calls on local values, such as
`cmd_name.casefold()`, or library calls that are the mechanism of a change rather than its effect.

## Per category

With 0.16.0. Development:

| Category found | Findings | In an expected category | In another | On an unchanged symbol |
| --- | --- | --- | --- | --- |
| `unknown_semantic_change` | 34 | 0 | 28 | 6 |
| `dependency_interaction_change` | 12 | 3 | 9 | 0 |
| `output_contract_change` | 9 | 6 | 2 | 1 |
| `error_handling_change` | 1 | 1 | 0 | 0 |
| `side_effect_change` | 1 | 0 | 1 | 0 |

Held out:

| Category found | Findings | In an expected category | In another | On an unchanged symbol |
| --- | --- | --- | --- | --- |
| `unknown_semantic_change` | 53 | 0 | 39 | 14 |
| `dependency_interaction_change` | 32 | 8 | 16 | 8 |
| `output_contract_change` | 12 | 5 | 1 | 6 |
| `error_handling_change` | 6 | 3 | 2 | 1 |
| `side_effect_change` | 4 | 3 | 1 | 0 |
| `default_behavior_change` | 3 | 2 | 1 | 0 |
| `validation_change` | 2 | 1 | 0 | 1 |
| `boundary_change` | 1 | 0 | 0 | 1 |
| `ordering_change` | 1 | 0 | 1 | 0 |

## Per case

With 0.16.0. The "Labels" column counts added symbols, whose findings
are neither right nor wrong: attrs#1571's two findings are one on `__all__` and one on the new `ne`.

| Case | Labels | Findings | Correct | Wrong category | False positive | Missed |
| --- | --- | --- | --- | --- | --- | --- |
| `psf/requests#7505` | 5 | 4 | 0 | 2 | 1 | 0 |
| `psf/requests#7502` | 1 | 1 | 0 | 1 | 0 | 0 |
| `psf/requests#7498` | 1 | 1 | 0 | 0 | 1 | 0 |
| `psf/requests#7496` | 6 | 0 | 0 | 0 | 0 | 0 |
| `psf/requests#7460` | 1 | 0 | 0 | 0 | 0 | 0 |
| `psf/requests#7441` | 3 | 1 | 0 | 0 | 1 | 0 |
| `pallets/click#3781` | 3 | 3 | 0 | 2 | 1 | 0 |
| `pallets/click#3777` | 4 | 6 | 1 | 4 | 0 | 0 |
| `pallets/click#3776` | 6 | 12 | 4 | 6 | 1 | 0 |
| `pallets/click#3764` | 1 | 1 | 0 | 1 | 0 | 0 |
| `pallets/click#3769` | 1 | 1 | 0 | 1 | 0 | 0 |
| `pallets/click#3739` | 1 | 2 | 0 | 2 | 0 | 0 |
| `psf/black#5649` | 1 | 2 | 0 | 2 | 0 | 0 |
| `psf/black#5647` | 2 | 1 | 0 | 1 | 0 | 0 |
| `psf/black#5493` | 1 | 2 | 0 | 2 | 0 | 0 |
| `psf/black#5646` | 1 | 1 | 0 | 1 | 0 | 0 |
| `psf/black#5645` | 2 | 4 | 2 | 2 | 0 | 0 |
| `psf/black#5643` | 7 | 10 | 1 | 6 | 0 | 0 |
| `encode/httpx#3699` | 2 | 2 | 0 | 2 | 0 | 0 |
| `encode/httpx#3651` | 2 | 0 | 0 | 0 | 0 | 0 |
| `encode/httpx#3571` | 1 | 1 | 1 | 0 | 0 | 0 |
| `encode/httpx#3519` | 4 | 0 | 0 | 0 | 0 | 0 |
| `encode/httpx#3445` | 1 | 1 | 0 | 0 | 1 | 0 |
| `encode/httpx#3442` | 1 | 2 | 1 | 1 | 0 | 0 |
| `python-attrs/attrs#1636` | 1 | 1 | 0 | 0 | 1 | 0 |
| `python-attrs/attrs#1625` | 1 | 0 | 0 | 0 | 0 | 0 |
| `python-attrs/attrs#1606` | 2 | 3 | 0 | 2 | 0 | 0 |
| `python-attrs/attrs#1571` | 8 | 2 | 0 | 1 | 0 | 0 |
| `python-attrs/attrs#1593` | 1 | 1 | 0 | 1 | 0 | 0 |
| `python-attrs/attrs#1589` | 1 | 0 | 0 | 0 | 0 | 0 |
| `pallets/flask#6168` | 4 | 4 | 0 | 0 | 4 | 0 |
| `pallets/flask#6133` | 1 | 1 | 0 | 0 | 0 | 0 |
| `pallets/flask#6096` | 3 | 3 | 2 | 1 | 0 | 0 |
| `pallets/flask#6013` | 1 | 2 | 1 | 1 | 0 | 0 |
| `pallets/flask#5917` | 1 | 1 | 0 | 1 | 0 | 0 |
| `pallets/flask#5899` | 4 | 5 | 1 | 3 | 1 | 0 |
| `pallets/werkzeug#3338` | 4 | 8 | 1 | 3 | 4 | 0 |
| `pallets/werkzeug#3332` | 7 | 4 | 1 | 2 | 0 | 0 |
| `pallets/werkzeug#3331` | 7 | 8 | 3 | 3 | 0 | 0 |
| `pallets/werkzeug#3329` | 4 | 6 | 0 | 4 | 2 | 0 |
| `pallets/werkzeug#3327` | 4 | 5 | 1 | 4 | 0 | 0 |
| `pallets/werkzeug#3322` | 10 | 12 | 5 | 5 | 2 | 0 |
| `encode/starlette#3736` | 2 | 2 | 0 | 2 | 0 | 0 |
| `encode/starlette#3772` | 1 | 2 | 1 | 1 | 0 | 0 |
| `encode/starlette#3768` | 1 | 1 | 0 | 0 | 1 | 0 |
| `encode/starlette#3728` | 1 | 1 | 0 | 0 | 1 | 0 |
| `encode/starlette#3725` | 2 | 5 | 1 | 2 | 2 | 0 |
| `encode/starlette#3599` | 1 | 1 | 0 | 1 | 0 | 0 |
| `Textualize/rich#4079` | 1 | 2 | 0 | 2 | 0 | 0 |
| `Textualize/rich#4077` | 1 | 1 | 0 | 0 | 0 | 0 |
| `Textualize/rich#4076` | 1 | 2 | 0 | 2 | 0 | 0 |
| `Textualize/rich#4075` | 3 | 1 | 0 | 1 | 0 | 0 |
| `Textualize/rich#3941` | 4 | 3 | 0 | 0 | 3 | 0 |
| `Textualize/rich#3845` | 6 | 12 | 0 | 12 | 0 | 0 |
| `marshmallow-code/marshmallow#3004` | 1 | 1 | 1 | 0 | 0 | 0 |
| `marshmallow-code/marshmallow#3016` | 1 | 1 | 0 | 1 | 0 | 0 |
| `marshmallow-code/marshmallow#2988` | 1 | 2 | 0 | 0 | 2 | 0 |
| `marshmallow-code/marshmallow#2799` | 7 | 12 | 2 | 4 | 5 | 0 |
| `marshmallow-code/marshmallow#2937` | 4 | 9 | 1 | 5 | 3 | 0 |
| `marshmallow-code/marshmallow#2935` | 2 | 3 | 1 | 1 | 1 | 0 |

## What the benchmark is not

A model labeled it and one person reviewed every label, so a label is one model's reading that one
reviewer accepted or corrected. Ninety pull requests of at most ten changed symbols each, sampled
from fifteen libraries' recent history, say nothing of large changes or of applications. Outside
"With a model" the analyzer ran without one, as the MCP server always runs it and the CLI does
without a provider; that section measures one run of one model on the development cases, and one
reviewer's reading of 40 test obligations. A finding is matched to a label by the path and symbol
of its first evidence, so two overloads of one name share a label.

The held-out split measures a correction only while no correction is designed from it. A change to
the analyzer's categories is made against the development cases, measured on both splits, and
recorded here with the release that ships it; once the held-out cases have been read for a change,
a new held-out sample is labeled before the next. The labels change only when one is found wrong,
with the reason.
