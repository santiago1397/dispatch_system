"""Base pipeline classes."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class PipelineResult:
    """Result of a pipeline execution.

    Attributes:
        processed: Number of items successfully processed.
        failed: Number of items that failed processing.
        errors: List of error messages for failed items.
        metadata: Additional metadata about the pipeline run.
    """

    processed: int
    failed: int = 0
    errors: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        """Calculate success rate as a percentage."""
        total = self.processed + self.failed
        if total == 0:
            return 100.0
        return (self.processed / total) * 100

    @property
    def has_errors(self) -> bool:
        """Check if any errors occurred."""
        return self.failed > 0 or len(self.errors) > 0


class BasePipeline(ABC):
    """Base class for all pipelines.

    Pipelines are used for background processing tasks like:
    - ETL operations
    - Batch data processing
    - Embedding generation
    - Data synchronization

    Subclasses must implement the `run` method.
    """

    @abstractmethod
    async def run(self) -> PipelineResult:
        """Execute the pipeline.

        Returns:
            PipelineResult with processing statistics.
        """
        pass

    async def validate(self) -> bool:
        """Validate pipeline configuration before running.

        Override this method to add custom validation logic.

        Returns:
            True if validation passes, False otherwise.
        """
        return True

    async def cleanup(self) -> None:  # noqa: B027
        """Cleanup resources after pipeline execution.

        Override this method to add custom cleanup logic.
        Default implementation does nothing.
        """
