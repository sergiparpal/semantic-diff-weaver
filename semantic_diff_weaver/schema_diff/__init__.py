"""Structural comparison of changed documents: API schemas — OpenAPI and Swagger documents, and
JSON Schemas — and the configuration files a repository names.

Only the pipeline-facing surface is re-exported. The bounded loader, the document model, and the
comparison rules are internal to this package and are imported from their defining module.
"""

from __future__ import annotations

from .analyze import DocumentAnalysis, analyze_schemas, analyze_settings

__all__ = ["DocumentAnalysis", "analyze_schemas", "analyze_settings"]
