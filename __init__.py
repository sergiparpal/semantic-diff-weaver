"""Hermes directory-plugin compatibility shim — the plugin's load-time entry point.

Hermes loads a directory plugin by executing the ``__init__.py`` at the plugin *directory* root.
That directory is ``semantic-diff-weaver`` (hyphenated, so not a valid import name), while the
implementation lives in the sibling ``semantic_diff_weaver`` package. This file re-exports its
``register`` entry point.

Hermes loads this file through ``importlib`` (``spec_from_file_location`` with
``submodule_search_locations``) and does *not* put the plugin directory on ``sys.path``, so the
absolute import below resolves only when the package is also pip-installed. That is exactly the
case `hermes plugins install OWNER/REPO` produces: the installer clones the repository into the
Hermes plugins directory, it does not build or install it. Appending this file's own directory
makes ``semantic_diff_weaver`` importable by its real name in both cases. Appending rather than
inserting keeps a pip-installed copy authoritative when both exist, and the package's own modules
keep importing each other relatively, so nothing about the installed path changes.

A clone carries no environment with it either, so the runtime dependencies must already be
importable where Hermes runs. A missing one is named here, with the command that fixes it, rather
than surfacing as a bare ``ModuleNotFoundError`` from somewhere inside the import chain.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Resolved, so a plugin directory that is a symlink to a checkout finds the real package next to
# the real file rather than next to the link.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.append(_HERE)

# Import name to requirement, because the two differ for PyYAML and the message quotes the
# requirement the user has to install.
_RUNTIME_DEPENDENCIES = {
    "pydantic": "pydantic>=2.10,<3",
    "yaml": "PyYAML>=6.0.2,<7",
}

try:
    # Deliberately below the bootstrap above: the absolute name resolves because of it.
    from semantic_diff_weaver.plugin import register
except ModuleNotFoundError as exc:
    _requirement = _RUNTIME_DEPENDENCIES.get(str(exc.name).partition(".")[0])
    if _requirement is None:
        raise
    raise ImportError(
        f"semantic-diff-weaver needs {exc.name} in the environment that runs Hermes. Installing "
        "this plugin clones the repository and never builds it, so its runtime dependencies are "
        f"not installed with it. Run: python -m pip install '{_requirement}' (or python -m pip "
        "install semantic-diff-weaver, which installs both and registers the entry point) into "
        "that environment, then restart Hermes."
    ) from exc

__all__ = ["register"]
