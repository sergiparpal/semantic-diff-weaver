# Release checklist

- [x] Choose and add an explicit license (MIT).
- [x] Run the full tests, lint, format, coverage, evaluation, and performance gates.
- [x] Build wheel and source distribution.
- [x] Inspect artifacts for caches, tests, secrets, local paths, metadata, and license inclusion.
- [x] Install the wheel in an isolated environment and inspect `hermes_agent.plugins` entry points.
- [x] Verify the `semantic-diff-weaver` console script is declared, importable, and runs `--help`
      and `skeletons --help` from the installed environment (`scripts/verify_wheel.py` asserts
      each).
- [x] Confirm `pyproject.toml`, `plugin.yaml`, `.claude-plugin/plugin.json`, and
      `semantic_diff_weaver.__version__` carry the same version; they must not drift.
      `tests/unit/test_regressions.py` pins the four together, after `__version__` was found
      reporting a stale release.
- [x] Confirm every third-party action is pinned to a full commit SHA with a version comment,
      and that the examples in `README.md` and `docs/` name the same commits the workflows do.
      `scripts/check_action_pins.py` enforces this on every pull request; run it with
      `--verify-remote --check-latest` to also resolve each pin against the GitHub API and list
      anything upstream has moved past.
- [x] Confirm the action's declared permissions are still exactly `contents: read` and
      `pull-requests: write`.
- [x] Run the full suite with no `ANTHROPIC_API_KEY` present; it must pass offline.
- [x] Record the lowest real Hermes release passing discovery tests (0.14.0).
- [x] Test the current Hermes release (v0.23.0) through `hermes plugins install` and through
      entry-point and directory discovery: CI's `hermes-current` job, which builds it from its
      Git tag, since PyPI stops at 0.19.0. First compare the latest release of
      NousResearch/hermes-agent with the job's `HERMES_TAG`, using `python
      scripts/check_hermes_release.py` as the weekly `hermes-release` workflow does. Move the
      tag, its commit, and `UV_VERSION` together.
- [x] Update changelog and evaluation measurements if behavior changed.
- [x] Bump the `uses: sergiparpal/semantic-diff-weaver@vX.Y.Z` examples in `README.md` and
      `docs/github-action.md` to the new tag. No moving major tag is published, so nothing
      re-points itself and a stale example is the only way a reader lands on an old release.
      The pin check keeps those examples agreeing with each other, but it cannot know which
      tag is current until the tag exists — `--verify-remote` fails once the examples name a
      release that was never pushed.
- [x] Once the tag is pushed, create its GitHub Release under the same authorization: titled
      with the tag's one-line message, its notes the version's `CHANGELOG.md` section with each
      paragraph on one line, and the wheel and source distribution the gate built and
      `scripts/verify_wheel.py` checked attached; `gh release create --verify-tag` never makes
      or moves a tag. No workflow runs on a release. The releases of v0.1.0 through v0.14.0 were
      created on 2026-11-02 from their tags, with notes alone.
- [x] Publish or push only with separate authorization.
