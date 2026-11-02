# Public benchmark

Measured 2026-11-02 with Semantic Diff Weaver 0.15.0 in deterministic mode, by
`scripts/run_benchmark.py`, against the labels of `benchmark/cases.json`: 30 pull requests merged
into `psf/requests`, `pallets/click`, `psf/black`, `encode/httpx`, and `python-attrs/attrs`, six of
each, with every Python symbol they change outside their tests labeled by one annotator before the
analyzer's output was read. `benchmark/README.md` says how the sample was drawn and what each label
means.

Unlike the 18-case corpus of `docs/evaluation.md`, whose cases were written to exercise the
taxonomy, these are changes as maintainers made them, and the analyzer was not adjusted to them.
They are the first measured numbers on real code.

## Results

| Measure | Value |
| --- | --- |
| Cases | 30 |
| Labeled symbols | 72: 31 behavior changes, 34 unchanged, and 7 added without a scored category |
| Findings on Python | 80, 7 of them on an added symbol; none on an unlabeled symbol |
| Precision, findings in an expected category | 10 of 73 (13.7%) |
| Precision, findings naming a behavior category | 10 of 32 (31.2%) |
| Recall, behavior changes given a finding | 31 of 31 (100.0%) |
| Recall, behavior changes given one in an expected category | 10 of 31 (32.3%) |
| Unchanged symbols given a finding | 18 of 34 (52.9%) |
| Unchanged symbols given one naming a behavior category | 6 of 34 (17.6%) |
| Findings outside Python, not scored | 1 |

A finding names a behavior category unless it is `unknown_semantic_change`, which says something
changed without saying what, or `refactor_likely_no_behavior_change`, which says nothing did.

| Category found | Findings | In an expected category | In another | On an unchanged symbol |
| --- | --- | --- | --- | --- |
| `unknown_semantic_change` | 41 | 0 | 28 | 13 |
| `dependency_interaction_change` | 17 | 3 | 13 | 1 |
| `output_contract_change` | 9 | 6 | 2 | 1 |
| `error_handling_change` | 5 | 1 | 0 | 4 |
| `side_effect_change` | 1 | 0 | 1 | 0 |

## What the numbers say

**Every behavior change was found, and so were half the symbols that did not change.** No labeled
change went without a finding, but 18 of the 34 symbols whose behavior is unchanged got one too, so
being found tells a reviewer little on its own. What separates the two is the category.

**Half the findings name no category.** 41 of the 80 are unknown changes: 28 are on behavior
changes, where the analyzer abstains from naming one, and 13 on unchanged symbols, where it reports
a change that is not there. Of those 13, seven are a module's imports and type aliases, as `import
os` added or an alias under `TYPE_CHECKING`, two are class annotations, one is a version string, and
three are rewrites that keep their behavior.

**A changed call is named a dependency interaction.** 17 findings are dependency interactions and 3
of them are right: `blackd`'s client now builds its headers and encodes its request body
differently, and click's temp-file pager passes `PAGER`'s parameters to the program it starts. The
other 13 sit on behavior changes whose call is the mechanism rather than the effect: requests'
`prepare_body` now calls `_t.has_read(data)`, which accepts an object that only proxies `read`, and
what changed is which inputs count as file-like.

**Output contracts are named best.** 6 of the 9 output contract findings are right, among them the
four of click's pager functions, which now yield `None` for an undecided color. One of the other
three is a widened annotation, `Editor.edit_files`, with no change at run time.

**A reworded error message reads as an error-handling change.** Four of the five error-handling
findings are httpx's typo fixes, "an sync" to "a sync". By the labeling rule a message reworded
with the same information changes no behavior, while one that now names a value, httpx#3571's
proxy scheme, does, and that one is found.

**One edit can be two findings.** 22 symbols got two or three findings, most often a dependency
interaction beside an unknown change on the same lines; precision counts each.

## Per case

| Case | Labels | Findings | Correct | Wrong category | False positive | Missed |
| --- | --- | --- | --- | --- | --- | --- |
| `psf/requests#7505` | 5 | 8 | 0 | 4 | 3 | 0 |
| `psf/requests#7502` | 1 | 1 | 0 | 1 | 0 | 0 |
| `psf/requests#7498` | 1 | 1 | 0 | 0 | 1 | 0 |
| `psf/requests#7496` | 6 | 1 | 0 | 0 | 1 | 0 |
| `psf/requests#7460` | 1 | 0 | 0 | 0 | 0 | 0 |
| `psf/requests#7441` | 3 | 3 | 0 | 0 | 3 | 0 |
| `pallets/click#3781` | 3 | 4 | 0 | 2 | 2 | 0 |
| `pallets/click#3777` | 4 | 6 | 1 | 4 | 0 | 0 |
| `pallets/click#3776` | 6 | 12 | 4 | 6 | 1 | 0 |
| `pallets/click#3764` | 1 | 1 | 0 | 1 | 0 | 0 |
| `pallets/click#3769` | 1 | 1 | 0 | 1 | 0 | 0 |
| `pallets/click#3739` | 1 | 2 | 0 | 2 | 0 | 0 |
| `psf/black#5649` | 1 | 2 | 0 | 2 | 0 | 0 |
| `psf/black#5647` | 2 | 3 | 0 | 2 | 1 | 0 |
| `psf/black#5493` | 1 | 2 | 0 | 2 | 0 | 0 |
| `psf/black#5646` | 1 | 2 | 0 | 2 | 0 | 0 |
| `psf/black#5645` | 2 | 4 | 2 | 2 | 0 | 0 |
| `psf/black#5643` | 7 | 11 | 1 | 6 | 1 | 0 |
| `encode/httpx#3699` | 2 | 2 | 0 | 2 | 0 | 0 |
| `encode/httpx#3651` | 2 | 0 | 0 | 0 | 0 | 0 |
| `encode/httpx#3571` | 1 | 1 | 1 | 0 | 0 | 0 |
| `encode/httpx#3519` | 4 | 4 | 0 | 0 | 4 | 0 |
| `encode/httpx#3445` | 1 | 1 | 0 | 0 | 1 | 0 |
| `encode/httpx#3442` | 1 | 2 | 1 | 1 | 0 | 0 |
| `python-attrs/attrs#1636` | 1 | 1 | 0 | 0 | 1 | 0 |
| `python-attrs/attrs#1625` | 1 | 0 | 0 | 0 | 0 | 0 |
| `python-attrs/attrs#1606` | 2 | 3 | 0 | 2 | 0 | 0 |
| `python-attrs/attrs#1571` | 8 | 2 | 0 | 1 | 0 | 0 |
| `python-attrs/attrs#1593` | 1 | 1 | 0 | 1 | 0 | 0 |
| `python-attrs/attrs#1589` | 1 | 0 | 0 | 0 | 0 | 0 |

The "Labels" column counts added symbols, whose findings are neither right nor wrong: attrs#1571's
two findings are one on `__all__` and one on the new `ne`. `black.schema.json`, which black#5643
also changes, is the one finding outside Python.

## What the benchmark is not

One annotator labeled it, so a label is one reviewer's reading, and `reviewed_by` is empty for
every case until someone checks them. Thirty pull requests of at most ten changed symbols each,
sampled from five libraries' recent history, say nothing of large changes or of applications. The
analyzer ran without a model, as the MCP server always runs it and the CLI does without a
provider; an inference pass may name categories differently. A finding is matched to a label by the
path and symbol of its first evidence, so two overloads of one name share a label.

These numbers are a baseline. The analyzer's categories can be changed against the same labels,
and a release that changes them records its measure here; the labels change only when one is found
wrong, with the reason.
