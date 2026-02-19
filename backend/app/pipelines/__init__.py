"""Background processing pipelines.

This module contains ETL pipelines, data processing workflows,
and batch operations that run as background tasks.
"""

from app.pipelines.base import BasePipeline, PipelineResult

__all__ = ["BasePipeline", "PipelineResult"]
