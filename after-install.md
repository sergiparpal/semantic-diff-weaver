# semantic-diff-weaver — first steps

1. **Enable it** — skip this if you installed with `--enable`; plugins are opt-in:

   ```bash
   hermes plugins enable semantic-diff-weaver
   hermes plugins list
   ```

   The listing should show `semantic-diff-weaver` enabled, with one tool and no error. If it
   does not, set `HERMES_PLUGINS_DEBUG=1` and read the Hermes plugin logs.

2. **Runtime dependencies.** Installing a plugin clones this repository; it does not build or
   install it. Pydantic 2 and PyYAML 6 therefore have to be importable in the environment that
   runs Hermes. If one is missing the plugin refuses to load and names it; the fix is a single
   install into that same environment:

   ```bash
   python -m pip install semantic-diff-weaver
   ```

   That also registers the `hermes_agent.plugins` entry point, which is the other supported way
   for Hermes to find the plugin. Git must be on `PATH` as well — the analyzer reads committed
   objects through Git plumbing.

3. **Authorize the repositories it may read.** A *model* chooses `repo_path` under Hermes, so the
   default authorized root is the Hermes process working directory and nothing else. To analyze
   checkouts elsewhere, a trusted operator lists the bounded roots, separated by the platform path
   separator:

   ```bash
   export SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS=/work/project:/work/shared-profiles
   ```

   `repo_path`, an external `risk_profile`, and an external `coverage_report` must each resolve
   below one of these roots. A filesystem root is never accepted as an authorization root.

4. **Inference needs nothing extra.** The tool uses the provider, model, and profile your Hermes
   session already has, and overrides none of them. Without a usable model the analysis degrades
   to deterministic structural findings rather than failing — the same for a provider that errors,
   times out, or returns output the schema rejects. The `anthropic` extra and `ANTHROPIC_API_KEY`
   belong to the standalone CLI and are not read on this path.

5. **Use it:** ask the agent to review a change — *"analyze the diff between `main` and `HEAD` in
   this repository and tell me what to test"*. It calls `analyze_semantic_diff`, which is advisory
   and read-only: it never imports, executes, builds, installs, tests, or modifies the repository
   it analyzes.

Configuration is optional. `.hermes/semantic-diff-weaver.yaml` or `.semantic-diff-weaver.yaml` in
the analyzed repository tunes paths, critical-path weights, and reporting thresholds — see
[docs/configuration.md](docs/configuration.md).
