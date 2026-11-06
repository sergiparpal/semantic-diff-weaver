"""Install the plugin on a current Hermes the way a user does, and check what that user gets.

`verify_hermes.py` checks the plugin against an installed Hermes runtime: entry-point and directory
discovery, and the signatures the plugin calls. It never runs `hermes plugins install`, the path a
user takes. On current releases that path does three things 0.19.0 did not (0.22.7 and 0.23.0 are
verified):

- It scans every file of the clone before installing it. A `dangerous` verdict blocks the install
  outright. A `caution` verdict needs the user's confirmation in a terminal, or `--force` without
  one. The test tree is scanned too, because Hermes's loader makes it importable, and each finding
  there is lowered one severity, because fixtures hold hostile strings on purpose (hermes-agent
  #89610). This plugin's security tests leave one high finding there, so its verdict is `caution`.
  This check fails on a `dangerous` verdict and on any high or critical finding outside `tests/`:
  those are findings no user should be asked to accept.
- It reads the plugin's Python dependencies from `pyproject.toml` and, with the user's consent,
  installs them into an environment of its own. `--yes-deps` gives that consent here.
- It loads the plugin in that environment. This check loads it there with Hermes's own plugin
  manager, requires both tools, and runs one analysis on Hermes's versions of Pydantic and PyYAML.

Run it from a checkout, with the Python of a Hermes environment that this project's wheel has not
been installed into:

    python scripts/verify_hermes_install.py

CI's `hermes-current` job builds that environment from the current release's Git tag. The
checkout's committed HEAD is installed into a temporary Hermes home; the real home and the checkout
are left untouched. Hermes internals are read only here, never by the plugin: its scanner, its
plugin manager, and where it keeps the dependency environment.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, Protocol

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.verify_hermes import PLUGIN_NAME, TOOL_NAMES, hermes_version_label  # noqa: E402

TEST_TREE = "tests/"
SEVERITIES = ("critical", "high", "medium", "low")
UNACCEPTABLE_SEVERITIES = frozenset({"critical", "high"})
# A user's non-interactive install: enable it at once, consent to the Python dependencies, and
# accept a caution verdict, which this check then judges. No flag accepts a dangerous one.
INSTALL_FLAGS = ("--enable", "--yes-deps", "--force")
ALLOWED_ROOTS_ENV = "SEMANTIC_DIFF_WEAVER_ALLOWED_ROOTS"
# Installing resolves and downloads the plugin's dependencies; every other step is local.
INSTALL_TIMEOUT_SECONDS = 900
STEP_TIMEOUT_SECONDS = 300
OUTPUT_TAIL_LINES = 60

# Run with the interpreter Hermes runs the plugin with, so that it imports what a user's Hermes
# imports: the plugin manager loads the installed clone under the temporary home's configuration,
# then the plugin's own handler analyzes a two-commit repository. It prints one JSON report.
LOAD_CHECK = r"""
import importlib.metadata
import json
import sys

import hermes_cli.plugins as plugins

plugin_name, repository = sys.argv[1], sys.argv[2]
manager = plugins.PluginManager()
manager.discover_and_load()
report = {
    "entries": [
        {key: item.get(key) for key in ("enabled", "error", "tools")}
        for item in manager.list_plugins()
        if item.get("name") == plugin_name
    ],
    "versions": {name: importlib.metadata.version(name) for name in ("pydantic", "PyYAML")},
}
try:
    import semantic_diff_weaver.plugin as plugin
except ImportError as exc:
    report["analysis"] = {"error": f"the plugin does not import: {exc}"}
else:
    report["plugin_file"] = plugin.__file__
    result = json.loads(
        plugin.handle_analyze_semantic_diff(
            {"repo_path": repository, "base_ref": "HEAD~1", "output_format": "json"}
        )
    )
    report["analysis"] = {
        "error": result.get("error"),
        "behavior_changes": len(result.get("behavior_changes", [])),
    }
print(json.dumps(report, sort_keys=True))
"""


class VerificationError(RuntimeError):
    """The install was refused or failed, or a user of the installed plugin would hit a fault."""


class ScanFinding(Protocol):
    """The fields of Hermes's `tools.skills_guard.Finding` this check reads."""

    pattern_id: str
    severity: str
    category: str
    file: str
    line: int


def unacceptable_findings(verdict: str, findings: Iterable[ScanFinding]) -> list[str]:
    """Why a user should not be asked to accept this scan result; empty when nothing says so.

    The documentation asks users to accept a `caution` verdict after reading its findings, which
    is honest only while every high or critical finding lies in the test tree, where Hermes itself
    expects hostile fixtures. A `dangerous` verdict no install can accept at all.
    """
    problems = []
    if verdict == "dangerous":
        problems.append("the verdict is dangerous, which no Hermes install can accept")
    for finding in findings:
        if finding.severity in UNACCEPTABLE_SEVERITIES and not finding.file.startswith(TEST_TREE):
            problems.append(
                f"a {finding.severity} {finding.category} finding outside {TEST_TREE}: "
                f"{finding.file}:{finding.line} ({finding.pattern_id})"
            )
    return problems


def dependency_python(home: Path) -> Path:
    """The interpreter that runs the installed plugin.

    Hermes installs a plugin's dependencies into an environment of its own, under
    `installs/<install>/environments/<environment>/venv` in its home, an internal layout read only
    here. A fresh home holds one. With none, the plugin runs in Hermes's own environment, and the
    load check finds out whatever is missing there.
    """
    found = sorted(home.glob("installs/*/environments/*/venv/bin/python"))
    if not found:
        return Path(sys.executable)
    if len(found) > 1:
        listed = ", ".join(str(path) for path in found)
        raise VerificationError(f"expected one dependency environment in a fresh home: {listed}")
    return found[0]


def load_problems(report: dict[str, Any]) -> list[str]:
    """Why the load check's report shows a plugin a user could not use; empty when it can."""
    problems = []
    entries = report.get("entries") or []
    if len(entries) != 1:
        problems.append(f"expected one {PLUGIN_NAME} plugin in Hermes's listing, found {entries!r}")
    else:
        entry = entries[0]
        if not entry.get("enabled") or entry.get("error") is not None:
            problems.append(f"the plugin did not load cleanly: {entry!r}")
        elif entry.get("tools") != len(TOOL_NAMES):
            problems.append(f"the plugin did not register exactly {list(TOOL_NAMES)!r}: {entry!r}")
    analysis = report.get("analysis") or {}
    if analysis.get("error") is not None:
        problems.append(f"the analysis failed: {analysis['error']!r}")
    elif not analysis.get("behavior_changes"):
        problems.append("the analysis found no behavior change in a changed comparison")
    return problems


def _tail(completed: subprocess.CompletedProcess[str]) -> str:
    lines = (completed.stdout + completed.stderr).splitlines()
    return "\n".join(lines[-OUTPUT_TAIL_LINES:])


def _run(
    arguments: Sequence[str], environment: dict[str, str], cwd: Path, timeout: int
) -> subprocess.CompletedProcess[str]:
    """Argument lists only, `shell=False`, bounded, as every script and the package run Git."""
    try:
        return subprocess.run(
            list(arguments),
            cwd=cwd,
            env=environment,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            shell=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerificationError(f"{arguments[0]} {arguments[1]} ran past {timeout} s") from exc


def _git(git: str, *arguments: str) -> None:
    completed = _run([git, *arguments], dict(os.environ), REPOSITORY_ROOT, STEP_TIMEOUT_SECONDS)
    if completed.returncode != 0:
        raise VerificationError(f"git {arguments[0]} failed:\n{_tail(completed)}")


def _clone_head(git: str, destination: Path) -> Path:
    """A clone of the checkout's committed HEAD, on a branch for Hermes's installer to clone.

    A pull request's checkout is a detached, shallow merge commit; cloning it and naming a branch
    there gives the installer what cloning a repository from GitHub gives it.
    """
    _git(git, "clone", "--quiet", "--no-hardlinks", str(REPOSITORY_ROOT), str(destination))
    _git(git, "-C", str(destination), "checkout", "--quiet", "-B", "verify-hermes-install")
    return destination


def _two_commit_repository(git: str, destination: Path) -> Path:
    """A repository whose last commit changes a comparison, for the plugin to analyze."""
    (destination / "pkg").mkdir(parents=True)
    (destination / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    module = destination / "pkg" / "core.py"
    module.write_text("def cap(x):\n    return x if x < 10 else 10\n", encoding="utf-8")
    identity = ["-c", "user.name=verify", "-c", "user.email=verify@example.invalid"]
    _git(git, "init", "--quiet", str(destination))
    _git(git, "-C", str(destination), "add", "--all")
    _git(git, "-C", str(destination), *identity, "commit", "--quiet", "--message", "base")
    module.write_text("def cap(x):\n    return x if x <= 20 else 20\n", encoding="utf-8")
    _git(git, "-C", str(destination), *identity, "commit", "--quiet", "--all", "--message", "head")
    return destination


def _print_scan(verdict: str, findings: Sequence[ScanFinding]) -> None:
    counts = Counter(finding.severity for finding in findings)
    summary = ", ".join(f"{counts[level]} {level}" for level in SEVERITIES)
    print(f"Install-time scan: {verdict} ({summary})")
    for finding in findings:
        if finding.severity in UNACCEPTABLE_SEVERITIES:
            print(
                f"  {finding.severity} {finding.category} {finding.file}:{finding.line} "
                f"({finding.pattern_id})"
            )


def main() -> int:
    hermes = Path(sys.executable).with_name("hermes")
    if not hermes.is_file():
        raise VerificationError(
            f"no hermes command beside {sys.executable}: run this with a Hermes environment's Python"
        )
    git = shutil.which("git")
    if git is None:
        raise VerificationError("git is not on PATH")
    label = hermes_version_label()
    with tempfile.TemporaryDirectory(prefix="semantic-diff-weaver-hermes-install-") as temporary:
        root = Path(temporary)
        home, working = root / "home", root / "work"
        home.mkdir()
        working.mkdir()
        source = _clone_head(git, root / "source")
        environment = {**os.environ, "HERMES_HOME": str(home)}

        install = _run(
            [str(hermes), "plugins", "install", source.as_uri(), *INSTALL_FLAGS],
            environment,
            working,
            INSTALL_TIMEOUT_SECONDS,
        )
        installed = home / "plugins" / PLUGIN_NAME
        if install.returncode != 0 or not installed.is_dir():
            raise VerificationError(
                "Hermes refused or failed the install; `--force` accepts only a caution verdict:\n"
                + _tail(install)
            )

        from tools.plugin_guard import scan_plugin

        scan = scan_plugin(installed, source=source.as_uri())
        _print_scan(scan.verdict, scan.findings)
        problems = unacceptable_findings(scan.verdict, scan.findings)
        if problems:
            raise VerificationError("the install-time scan reports\n  " + "\n  ".join(problems))

        repository = _two_commit_repository(git, root / "repository")
        python = dependency_python(home)
        check = _run(
            [str(python), "-I", "-c", LOAD_CHECK, PLUGIN_NAME, str(repository)],
            {**environment, ALLOWED_ROOTS_ENV: str(repository)},
            working,
            STEP_TIMEOUT_SECONDS,
        )
        if check.returncode != 0:
            raise VerificationError(f"the load check failed under {python}:\n{_tail(check)}")
        report = json.loads(check.stdout.strip().splitlines()[-1])
        problems = load_problems(report)
        if problems:
            raise VerificationError("the installed plugin\n  " + "\n  ".join(problems))

    versions = report["versions"]
    print(
        f"Hermes {label}: installed with a {scan.verdict} verdict, every high finding in "
        f"{TEST_TREE}; loaded with {len(TOOL_NAMES)} tools and analyzed a change on Pydantic "
        f"{versions['pydantic']} and PyYAML {versions['PyYAML']}, in "
        f"{'its dependency environment' if python != Path(sys.executable) else 'its own environment'}"
    )
    return 0


def _main() -> int:
    try:
        return main()
    except VerificationError as exc:
        print(f"verify_hermes_install: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
