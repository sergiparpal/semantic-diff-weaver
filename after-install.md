# semantic-diff-weaver — first steps

1. **Enable it** — skip this if you installed with `--enable`; plugins are opt-in:

   ```bash
   hermes plugins enable semantic-diff-weaver
   hermes plugins list
   ```

   The listing should show `semantic-diff-weaver` enabled, and on a current Hermes
   `hermes tools list` shows its `semantic_diff_weaver` toolset enabled too. If not, set
   `HERMES_PLUGINS_DEBUG=1` and read the Hermes plugin logs.

2. **Runtime dependencies.** The plugin needs Pydantic 2 and PyYAML 6, and Git on `PATH`: the
   analyzer reads committed objects through Git plumbing. Current Hermes releases (0.22.7 and
   0.23.0 are verified) read the two from this repository's `pyproject.toml` and install them into
   an environment of their own as the plugin is installed, once you consent; `--yes-deps` consents
   without a prompt. If the plugin still names one as missing, let Hermes rebuild that
   environment, then restart Hermes:

   ```bash
   hermes pm repair
   ```

   Hermes 0.19.0 and earlier install nothing: there the two have to be importable in the
   environment that runs Hermes, and the plugin names the missing one with the
   `python -m pip install` command for it. Installing the package from a checkout instead,
   `python -m pip install .`, brings both and registers the `hermes_agent.plugins` entry point,
   the other supported way for Hermes to find the plugin.

3. **Authorize the repositories it may read.** A *model* chooses `repo_path` under Hermes, so the
   default authorized root is the Hermes process working directory and nothing else. To analyze
   checkouts elsewhere, a trusted operator lists the bounded roots, separated by the platform path
   separator:

   ```bash
   export SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS=/work/project:/work/shared-profiles
   ```

   `repo_path`, an external `risk_profile`, `coverage_report`, or `baseline_report`, and the
   skeleton tool's `analysis_path` must each resolve below one of these roots. A filesystem root
   is never accepted as an authorization root.

4. **Inference needs nothing extra.** The tool uses the provider, model, and profile your Hermes
   session already has, and overrides none of them. Without a usable model the analysis degrades
   to deterministic structural findings rather than failing — the same for a provider that errors,
   times out, or returns output the schema rejects. The `anthropic` extra and `ANTHROPIC_API_KEY`
   belong to the standalone CLI and are not read on this path.

5. **Use it:** ask the agent to review a change — *"analyze the diff between `main` and `HEAD` in
   this repository and tell me what to test"*. It calls `analyze_semantic_diff`, which is advisory
   and read-only: it never imports, executes, builds, installs, tests, or modifies the repository
   it analyzes. Ask it to scaffold the obligations you approve, and `generate_test_skeletons`
   returns a patch of failing tests for them, which it never applies. Its IDs are those of the
   analysis without inference, so for an analysis your session's model took part in, save that
   analysis's JSON and pass its path as `analysis_path`.

Configuration is optional. `.hermes/semantic-diff-weaver.yaml` or `.semantic-diff-weaver.yaml` in
the analyzed repository tunes paths, critical-path weights, and reporting thresholds — see
[docs/configuration.md](docs/configuration.md).
