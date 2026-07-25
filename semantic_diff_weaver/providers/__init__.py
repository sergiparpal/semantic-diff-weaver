"""Optional inference providers for standalone use.

Each provider implements `llm_client.LlmClient`. Nothing here is imported by the analysis
pipeline; the CLI resolves a provider lazily so the base install stays dependency-light and
a missing provider degrades to deterministic mode rather than failing.
"""

from __future__ import annotations

__all__: list[str] = []
