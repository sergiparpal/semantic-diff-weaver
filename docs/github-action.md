# GitHub Action

The action is a thin wrapper around the standalone CLI. It contains no analysis logic: it sets
up Python, installs this package, runs `semantic-diff-weaver --format both`, and upserts the
resulting brief as a single pull-request comment.

It is a **composite** action rather than a Docker one — nothing to publish to a registry, a
faster cold start, and the dependency surface stays readable in `action.yml` instead of inside
an image.

## Complete workflow

```yaml
name: pr-review

on:
  pull_request:

permissions:
  contents: read
  pull-requests: write

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          fetch-depth: 0        # required — see below
      - uses: sergiparpal/semantic-diff-weaver@v0.2.0
        with:
          include: |
            src/**/*.py
          fail-on: none
```

`permissions` is the complete set the action needs: `contents: read` to check out, and
`pull-requests: write` to post the comment. Grant nothing more. If `comment: false`, drop
`pull-requests: write` as well.

Every example on this page pins third-party actions to a full commit SHA with a version
comment, as `.github/workflows/ci.yml` and `.github/workflows/pr-review.yml` in this repository
do. A moving tag such as `@v7` is a mutable reference: whoever controls it can change what runs
inside a job that holds `pull-requests: write`. Copy the examples as written, and update the
SHAs deliberately.

That reasoning does not stop applying because the action is this one, so **no moving major tag is
published**. There is no `@v0` to float on: the examples name the immutable release tag
`@v0.2.0`, and a full commit SHA is equally accepted if you want exactly the guarantee you are
asking of `actions/checkout`. Upgrades are a deliberate edit, and `CHANGELOG.md` says what each
one changes.

## `fetch-depth: 0` is required

The analyzer reads committed Git objects and **never fetches** — no network, no ref lookup.
`actions/checkout` defaults to a shallow clone that does not contain the pull request's base
commit, so without `fetch-depth: 0` the base revision simply is not on disk.

The action checks for this before analyzing and fails with an explicit message naming the
missing revision, rather than silently analyzing the wrong range.

## Inputs

| Input | Default | Meaning |
| --- | --- | --- |
| `base-ref` | `${{ github.event.pull_request.base.sha }}` | base revision |
| `head-ref` | `${{ github.event.pull_request.head.sha }}` | head revision |
| `include` | *(empty)* | newline-separated include globs |
| `exclude` | *(empty)* | newline-separated exclude globs |
| `coverage-report` | *(empty)* | coverage.py JSON or lcov `.info` path |
| `risk-profile` | *(empty)* | additional bounded YAML configuration file |
| `fail-on` | `none` | fail the step at this overall risk level |
| `comment` | `true` | post or update the pull-request comment |
| `python-version` | `3.12` | interpreter used to run the analyzer |

Outputs: `analysis` (path to the JSON envelope under `RUNNER_TEMP`) and `overall-risk`.

Set `base-ref` explicitly on any event that is not `pull_request` — the default expression is
empty there, and the action fails with a message saying so rather than guessing a range.

## Passing a coverage report from a prior job

The tool consumes a coverage report and never produces one. Produce it in the job that runs
your tests, upload it, and hand the path to the action:

```yaml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
      - run: pip install -e . pytest pytest-cov
      - run: pytest --cov --cov-report=json:coverage.json
      - uses: actions/upload-artifact@330a01c490aca151604b8cf639adc76d48f6c5d4 # v5.0.0
        with:
          name: coverage
          path: coverage.json

  review:
    needs: test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0
        with:
          fetch-depth: 0
      - uses: actions/download-artifact@018cc2cf5baa6db3ef3c5f8a56943fffe632ef53 # v6.0.0
        with:
          name: coverage
      - uses: sergiparpal/semantic-diff-weaver@v0.2.0
        with:
          coverage-report: coverage.json
```

A changed file absent from the report is reported as **unknown, never uncovered**, so a
report generated in a different working directory shows up as a warning rather than as a fake
coverage gap. See [configuration](configuration.md#coverage-grounding).

## Comment behavior

The comment carries a hidden marker as its first line:

```text
<!-- semantic-diff-weaver:v1 -->
```

`scripts/pr_comment.py` lists the pull request's comments, finds the one starting with that
marker, and `PATCH`es it if present or `POST`s a new one if not. **Re-runs edit; they never
append.** The marker is the only thing matched on, so the behavior does not depend on the
comment's author, its position, or state carried between runs.

GitHub caps a comment at 65,536 characters. A longer brief is truncated at a section boundary
— never mid-sentence — and gains an explicit notice reporting how many lines and findings were
dropped, pointing the reader at the workflow log for the complete output.

`gh` is invoked with argument lists and `shell=False`, and the comment body is passed through a
file rather than the command line, so untrusted repository content never reaches a shell.

The repository and pull-request number are interpolated into the `gh api` resource path, so both
are constrained to their documented shapes first: `owner/repo` against
`^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$`, and the pull request to digits. The argument-list boundary
already ruled out shell injection; this stops a value containing `/` or `..` from retargeting the
request at a different endpoint.

## Fork pull requests

By default, a workflow triggered by `pull_request` from a fork receives a **read-only**
`GITHUB_TOKEN`, regardless of the `permissions` block. The analysis still runs and the step
summary still shows the result, but the comment step cannot post and the job fails at that
step.

This is a deliberate GitHub protection, and this action does not work around it — the usual
workaround (`pull_request_target`) runs workflow code with a writable token against the fork's
content, which is exactly the exposure this tool exists to avoid. Options, in order of
preference:

1. **Set `comment: false` for fork pull requests** and read the brief in the workflow log:
   ```yaml
   with:
     comment: ${{ github.event.pull_request.head.repo.fork == false }}
   ```
2. **Use `fail-on`** to gate the merge on risk without needing to comment at all.
3. Accept that fork pull requests get log output only.

## What the action deliberately does not emit

**No SARIF and no check annotations.** See `docs/decisions.md` — SARIF's rule/level/location
model flattens the risk-versus-confidence separation this tool exists to maintain, and the
Markdown brief already carries obligations, review questions, and stated limitations.
