"""Structural comparison of changed API schemas: OpenAPI and Swagger documents, and JSON Schemas.

Only the pipeline-facing surface is re-exported. The bounded loader, the document model, and the
comparison rules are internal to this package and are imported from their defining module.
"""

from __future__ import annotations

from .analyze import SchemaAnalysis, analyze_schemas

__all__ = ["SchemaAnalysis", "analyze_schemas"]
