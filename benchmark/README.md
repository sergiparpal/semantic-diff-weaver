# Labeled benchmark of real pull requests

`cases.json` is a public benchmark for analyzers of behavioral change: 90 pull requests merged into
fifteen public Python repositories, each pinned by two commits, with every Python symbol it changes
outside its tests labeled by the categories a reviewer expects of it, or by none when its behavior
does not change. `scripts/run_benchmark.py` measures Semantic Diff Weaver against it, and
`docs/benchmark.md` holds the results.

Only labels are published here. The code stays in its repositories, under their own licenses, and
is fetched from them when the benchmark is measured; the labels and their rationales are this
repository's, under its MIT license.

## The sample

The latest pull requests merged into the default branch of each repository before the commit
`sampled_from` names for it, six per repository, read back along the branch's first-parent history as
`scripts/validate_real_prs.py` reads it: a squash merge reviewed from its parent, a merge commit
from where its pull request branched off. A pull request was taken when the analyzer's own count of
the Python symbols it changes outside the tests is between 1 and 10 and none of its scope was left
unread, and was never chosen by hand. `run_benchmark.py propose` reproduces the sample; it prints
how many symbols and files each pull request changes and never a finding. Ten repositories were
sampled first, and five more later for a second held-out split.

## The splits

Each case's `split` says what it is for. The 60 `development` cases are the ones the analyzer is
changed against: their disagreements are read to design a correction. They are the first sample's
30 development cases, from `psf/requests`, `pallets/click`, `psf/black`, `encode/httpx`, and
`python-attrs/attrs`, and its 30 held-out cases, from `pallets/flask`, `pallets/werkzeug`,
`encode/starlette`, `Textualize/rich`, and `marshmallow-code/marshmallow`, which were labeled before
the analyzer was first changed and joined development once that change had been measured on them.
The 30 `held_out` cases, from `encode/django-rest-framework`, `jd/tenacity`, `urllib3/urllib3`,
`agronholm/anyio`, and `pypa/packaging`, were labeled, reviewed, and committed before the
analyzer's next change, and measure it on code it was not designed from. Once a correction has
been measured on them, a new held-out sample is labeled before the next one is designed.

## The labels

One annotator labeled every case from its diff and the files at its two commits, before reading
the analyzer's output on it; the labels were committed before the first measurement. A reviewer
then checked every label on a page that showed each symbol's diff and never the analyzer's
findings, and corrected 26 of the first sample's 162 labels and 16 of the second's 89. Each label
names a symbol as the analyzer does — `Class.method`, `function.nested` for a function defined
directly in another's body, a class's own statements as `Class`, and a module's top-level
statements as `<module>` — and says whether it was `modified`, `added`, or `removed`. A definition
inside an `if` or a `try` belongs to the symbol around it, as werkzeug's module-level `__getattr__`
under `if not t.TYPE_CHECKING:` belongs to `<module>`.

- **A behavior change** lists every category of `docs/semantic-diff-weaver-spec.md` §7 a reviewer
  could fairly give it; a finding in any of them is right. `unknown_semantic_change` and
  `refactor_likely_no_behavior_change` are never expected: a reviewer names what a change does.
- **No behavior change** is an empty list on a modified symbol: a comment, a docstring, a type
  annotation or a type-checker comment, an import that loads what was loaded before, formatting,
  the same predicate moved into a helper, a version string, or an error message reworded with the
  same information. A message that now tells something it did not, as one that names a value it
  held as placeholder text, is an error-handling change.
- **A change is judged on the Python versions both commits support.** A branch that only a version
  the head drops could run is no behavior change, as flask#6168's for Python 3.10 is.
- **A function is labeled with its whole body**, the functions nested in it included, as the
  analyzer reads it: when only `retry.wrap` changes, `retry` changes too and is labeled with what
  its callers see, as werkzeug#3329's `run_wsgi` is with `run_wsgi.execute`.
- **Every Python file outside the tests is labeled**, as the analyzer reads it: a benchmark, a
  `noxfile.py`, or a typing example as much as the library's own code.
- **An added symbol** with an empty list is not scored for its category: a new function is new
  behavior rather than a change to any.
- **A removed symbol** lists what its callers lose, an output contract change when they could call
  it; removing a symbol is not a change by itself. Its list is empty, and like an added symbol's
  not scored, when what it did is still served, as the module's `__getattr__` serves werkzeug's
  `wsgi.wrap_file`, or is labeled on the only symbol that used it.
- An `__all__` that exports a new name changes the module's public contract, an output contract
  change.

Each label carries one line of rationale. A case's `reviewed_by` names whoever checks its labels
after the first annotator; a label found wrong is corrected with its reason in the commit that
changes it, and never to agree with the analyzer.

## Running it

```bash
python scripts/run_benchmark.py --scratch /tmp/sdw-benchmark --disagreements
```

`--split development` or `--split held_out` measures one split alone.

Each repository is cloned whole, without a checkout, into the scratch directory, which must lie
outside this working tree; the analyzer reads its Git objects in deterministic mode and nothing
from it is installed or run. The script prints the measures, a table per category and per case,
and with `--disagreements` every finding not in an expected category and every behavior change
missed. Findings on files that are not Python, such as an API schema, are counted apart, since only
Python is labeled.

## Measuring with a model

```bash
python scripts/run_benchmark.py llm --model MODEL --max-cost-usd 0.5 --scratch /tmp/sdw-benchmark
```

`llm` analyzes each development case twice, without a model and with `MODEL` behind an
OpenAI-compatible endpoint: OpenRouter's by default, with its key in `OPENROUTER_API_KEY`, or the
one `--base-url` names, with its key in the variable `--api-key-env` names. No held-out case is ever
sent to a model. A case counts only if the model answered: it was called, and no batch failed. A
case where the analysis kept nothing the model said still counts, since its output is what the
answer left, and the report says how many there were. The others are listed by why they did not
count. The script prints each measure without the model and with it on the cases that count, then
what the model did in all and per case, and saves a report, with each case's two analyses and every
call's reply, under `model-runs/` in the scratch directory.

The run sends each case's changed code to the endpoint. So it first checks that GitHub serves every
repository as public, and it makes no call once the calls so far cost `--max-cost-usd`, as the
endpoint reports it; `--usd-per-mtok INPUT OUTPUT` estimates the cost where an endpoint reports
none. Give the key a credit limit as well. `--case` and `--limit` measure fewer cases.
`--reasoning-effort` sets a reasoning model's effort, since its reasoning shares the reply's token
budget, and `--no-temperature` leaves out the temperature some models reject.

A model never changes a finding's category. It can support a finding or add one, and move a
finding's confidence, and with it whether the finding is reported (`docs/decisions.md`,
2026-11-06). The rest of what it changes is the wording of findings and obligations, which the saved
analyses keep for reading.

A model does not answer the same way twice, so two runs of one model differ, and a change to the
analysis cannot be measured by comparing them. Measure it on the same replies instead:

```bash
python scripts/run_benchmark.py llm --replay RUN_DIR --scratch /tmp/sdw-benchmark
```

`--replay` takes a run's directory under `model-runs/` and analyzes the cases it measured again,
answering each call with the reply that run got for the same request, and calls no model, so it
needs neither a key nor a cap. A request the recorded run never made fails its batch, and the case
then does not count. Replaying a run from another checkout of the analyzer compares the two versions
on identical replies.

