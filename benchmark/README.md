# Labeled benchmark of real pull requests

`cases.json` is a public benchmark for analyzers of behavioral change: 60 pull requests merged into
ten public Python repositories, each pinned by two commits, with every Python symbol it changes
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
how many symbols and files each pull request changes and never a finding.

## The splits

Each case's `split` says what it is for. The 30 `development` cases, from `psf/requests`,
`pallets/click`, `psf/black`, `encode/httpx`, and `python-attrs/attrs`, are the ones the analyzer is
changed against: their disagreements are read to design a correction. The 30 `held_out` cases, from
`pallets/flask`, `pallets/werkzeug`, `encode/starlette`, `Textualize/rich`, and
`marshmallow-code/marshmallow`, were labeled and committed before the analyzer was first changed
against the development ones, and measure a correction on code it was not designed from. Once a
correction has been measured on them, a new held-out sample is labeled before the next one is
designed.

## The labels

One annotator labeled every case from its diff and the files at its two commits, before reading
the analyzer's output on it; the labels were committed before the first measurement. A reviewer
then checked every label on a page that showed each symbol's diff and never the analyzer's
findings, and corrected 26. Each label names a symbol as the analyzer does — `Class.method`,
`function.nested` for a function defined directly in another's body, a class's own statements as
`Class`, and a module's top-level statements as `<module>` — and says whether it was `modified`,
`added`, or `removed`. A definition inside an `if` or a `try` belongs to the symbol around it, as
werkzeug's module-level `__getattr__` under `if not t.TYPE_CHECKING:` belongs to `<module>`.

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
