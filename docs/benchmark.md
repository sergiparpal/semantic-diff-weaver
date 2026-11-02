# Public benchmark

Measured 2026-11-02 in deterministic mode, by `scripts/run_benchmark.py`, against the labels of
`benchmark/cases.json`: 60 pull requests merged into ten public Python libraries, with every
Python symbol they change outside their tests labeled by one annotator before the analyzer's output
on them was read. `benchmark/README.md` says how the sample was drawn and what each label means.

The cases come in two splits of 30. The **development** cases, six pull requests each of
`psf/requests`, `pallets/click`, `psf/black`, `encode/httpx`, and `python-attrs/attrs`, are the ones
the analyzer is changed against. The **held-out** cases, six each of `pallets/flask`,
`pallets/werkzeug`, `encode/starlette`, `Textualize/rich`, and `marshmallow-code/marshmallow`, were
labeled before the first such change and measure it on code it was not designed from.

Unlike the 18-case corpus of `docs/evaluation.md`, whose cases were written to exercise the
taxonomy, these are changes as maintainers made them.

## Results

Each split measured with Semantic Diff Weaver 0.15.0, and with the analyzer after the three
corrections of `docs/decisions.md` (2026-11-02, benchmark categories), not yet released.

| Measure | Development, 0.15.0 | Development, now | Held-out, 0.15.0 | Held-out, now |
| --- | --- | --- | --- | --- |
| Labeled symbols | 72 | 72 | 90 | 90 |
| Behavior changes | 31 | 31 | 43 | 43 |
| Unchanged symbols | 34 | 34 | 39 | 39 |
| Findings on Python | 80 | 64 | 131 | 120 |
| Precision, findings in an expected category | 10 of 73 (13.7%) | 10 of 57 (17.5%) | 16 of 125 (12.8%) | 16 of 114 (14.0%) |
| Precision, findings naming a behavior category | 10 of 32 (31.2%) | 10 of 23 (43.5%) | 16 of 61 (26.2%) | 16 of 61 (26.2%) |
| Recall, behavior changes given a finding | 31 of 31 | 31 of 31 | 43 of 43 | 43 of 43 |
| Recall, behavior changes given one in an expected category | 10 of 31 (32.3%) | 10 of 31 (32.3%) | 15 of 43 (34.9%) | 15 of 43 (34.9%) |
| Unchanged symbols given a finding | 18 of 34 (52.9%) | 7 of 34 (20.6%) | 34 of 39 (87.2%) | 23 of 39 (59.0%) |
| Unchanged symbols given one naming a behavior category | 6 of 34 (17.6%) | 1 of 34 (2.9%) | 17 of 39 (43.6%) | 17 of 39 (43.6%) |

Findings on an added symbol, 7 in development and 6 held out, are neither right nor wrong and are
left out of precision. No finding fell on an unlabeled symbol. `black.schema.json`, which black#5643
also changes, is the one finding outside Python, counted apart. A finding names a behavior category
unless it is `unknown_semantic_change`, which says something changed without saying what, or
`refactor_likely_no_behavior_change`, which says nothing did.

## What the numbers say

**The corrections removed findings on code that does not run differently, and moved no category to
a right one.** In development, 11 of the 18 unchanged symbols with a finding lost it: seven modules
whose imports or `if TYPE_CHECKING:` blocks alone changed, and httpx#3519's four reworded messages.
Five dependency interactions became unknown changes, calls into the package's own code such as
requests' `_t.has_read(data)` and black's `is_yield(node)`. Every behavior change still has a
finding, and the 10 in an expected category are the same 10.

**The held-out split confirms the first correction only.** 11 of its 39 unchanged symbols lost
their finding, and all 11 were a module's imports or type-checking block: werkzeug's, flask's,
starlette's, and rich's. None of its findings was a reworded message, and none of its dependency
interactions called only its package's own code, so the categories it names did not change: 16 of
61 are right, as before. The held-out split was measured once after the corrections; a refinement
for a development case and the safeguards of `docs/security.md` came after, and changed none of its
results.

**What remains on unchanged symbols in development** is a class's annotations, requests' `Request`
twice; a widened annotation, `Editor.edit_files`, named an output contract; a version string; and
three rewrites that keep their behavior, `get_pager_file`, `astuple`, and `_encode_files`.

**Held out, a module's constants and a signature's annotations still read as changes.** Of the 23
unchanged symbols with a finding, nine are modules: eight add, drop, or rewrite a constant — a
compiled pattern such as starlette's `PARAM_FORMAT_REGEX`, a `TypeVar`, an id counter, a string —
and one is starlette's version string. Six of the eight name a dependency interaction for the
`re.compile`, `TypeVar`, or `count` call that builds the constant. Five more are signatures whose
annotations alone change, rich's three `save_*` methods among them, named output contracts.

**Half the findings still name no category.** 34 of the 57 scored findings in development and 53 of
the 114 held out are unknown changes, nearly all on behavior changes, where the analyzer abstains
from naming one.

**A changed call is still most often named a dependency interaction by mistake.** 3 of the 12 in
development and 4 of the 32 held out are right. Most of the rest are calls on local values, such as
`cmd_name.casefold()`, or library calls that are the mechanism of a change rather than its effect.

## Per category

With the analyzer after the corrections. Development:

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
| `unknown_semantic_change` | 53 | 0 | 37 | 16 |
| `dependency_interaction_change` | 32 | 4 | 19 | 9 |
| `output_contract_change` | 12 | 5 | 1 | 6 |
| `error_handling_change` | 6 | 2 | 3 | 1 |
| `side_effect_change` | 4 | 2 | 1 | 1 |
| `default_behavior_change` | 3 | 2 | 1 | 0 |
| `validation_change` | 2 | 1 | 0 | 1 |
| `boundary_change` | 1 | 0 | 0 | 1 |
| `ordering_change` | 1 | 0 | 1 | 0 |

## Per case

With the analyzer after the corrections. The "Labels" column counts added symbols, whose findings
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
| `pallets/werkzeug#3332` | 7 | 4 | 0 | 1 | 2 | 0 |
| `pallets/werkzeug#3331` | 7 | 8 | 2 | 4 | 0 | 0 |
| `pallets/werkzeug#3329` | 4 | 6 | 0 | 4 | 2 | 0 |
| `pallets/werkzeug#3327` | 4 | 5 | 1 | 4 | 0 | 0 |
| `pallets/werkzeug#3322` | 10 | 12 | 4 | 6 | 2 | 0 |
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
| `Textualize/rich#3845` | 6 | 12 | 0 | 10 | 2 | 0 |
| `marshmallow-code/marshmallow#3004` | 1 | 1 | 1 | 0 | 0 | 0 |
| `marshmallow-code/marshmallow#3016` | 1 | 1 | 0 | 1 | 0 | 0 |
| `marshmallow-code/marshmallow#2988` | 1 | 2 | 0 | 0 | 2 | 0 |
| `marshmallow-code/marshmallow#2799` | 7 | 12 | 1 | 5 | 5 | 0 |
| `marshmallow-code/marshmallow#2937` | 4 | 9 | 0 | 6 | 3 | 0 |
| `marshmallow-code/marshmallow#2935` | 2 | 3 | 0 | 2 | 1 | 0 |

## What the benchmark is not

One annotator labeled it, so a label is one reviewer's reading, and `reviewed_by` is empty for
every case until someone checks them. Sixty pull requests of at most ten changed symbols each,
sampled from ten libraries' recent history, say nothing of large changes or of applications. The
analyzer ran without a model, as the MCP server always runs it and the CLI does without a
provider; an inference pass may name categories differently. A finding is matched to a label by the
path and symbol of its first evidence, so two overloads of one name share a label.

The held-out split measures a correction only while no correction is designed from it. A change to
the analyzer's categories is made against the development cases, measured on both splits, and
recorded here with the release that ships it; once the held-out cases have been read for a change,
a new held-out sample is labeled before the next. The labels change only when one is found wrong,
with the reason.
