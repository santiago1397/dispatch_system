"""Tests for pipeline infrastructure."""

import pytest

from app.pipelines.base import BasePipeline, PipelineResult


class TestPipelineResult:
    """Tests for PipelineResult dataclass."""

    def test_success_rate_all_processed(self):
        """Test success rate when all items processed."""
        result = PipelineResult(processed=10, failed=0)
        assert result.success_rate == 100.0

    def test_success_rate_with_failures(self):
        """Test success rate with some failures."""
        result = PipelineResult(processed=8, failed=2)
        assert result.success_rate == 80.0

    def test_success_rate_all_failed(self):
        """Test success rate when all items failed."""
        result = PipelineResult(processed=0, failed=10)
        assert result.success_rate == 0.0

    def test_success_rate_empty(self):
        """Test success rate with no items."""
        result = PipelineResult(processed=0, failed=0)
        assert result.success_rate == 100.0

    def test_has_errors_with_failures(self):
        """Test has_errors returns True when failures exist."""
        result = PipelineResult(processed=5, failed=1)
        assert result.has_errors is True

    def test_has_errors_with_error_messages(self):
        """Test has_errors returns True when error messages exist."""
        result = PipelineResult(processed=5, failed=0, errors=["Error 1"])
        assert result.has_errors is True

    def test_has_errors_no_errors(self):
        """Test has_errors returns False when no errors."""
        result = PipelineResult(processed=5, failed=0)
        assert result.has_errors is False

    def test_default_values(self):
        """Test default values are set correctly."""
        result = PipelineResult(processed=5)
        assert result.failed == 0
        assert result.errors == []
        assert result.metadata == {}


class TestBasePipeline:
    """Tests for BasePipeline abstract class."""

    @pytest.mark.anyio
    async def test_validate_returns_true_by_default(self):
        """Test validate method returns True by default."""

        class TestPipeline(BasePipeline):
            async def run(self) -> PipelineResult:
                return PipelineResult(processed=0)

        pipeline = TestPipeline()
        assert await pipeline.validate() is True

    @pytest.mark.anyio
    async def test_cleanup_does_nothing_by_default(self):
        """Test cleanup method does nothing by default."""

        class TestPipeline(BasePipeline):
            async def run(self) -> PipelineResult:
                return PipelineResult(processed=0)

        pipeline = TestPipeline()
        await pipeline.cleanup()  # Should not raise

    @pytest.mark.anyio
    async def test_run_must_be_implemented(self):
        """Test that run method must be implemented by subclasses."""
        # This test verifies the abstract method requirement
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            BasePipeline()

    @pytest.mark.anyio
    async def test_custom_pipeline_implementation(self):
        """Test a custom pipeline implementation."""

        class MyPipeline(BasePipeline):
            def __init__(self, items: list):
                self.items = items

            async def run(self) -> PipelineResult:
                processed = 0
                failed = 0
                errors = []

                for item in self.items:
                    if item > 0:
                        processed += 1
                    else:
                        failed += 1
                        errors.append(f"Invalid item: {item}")

                return PipelineResult(
                    processed=processed,
                    failed=failed,
                    errors=errors,
                )

        pipeline = MyPipeline([1, 2, 3, -1, 5])
        result = await pipeline.run()

        assert result.processed == 4
        assert result.failed == 1
        assert len(result.errors) == 1
        assert result.success_rate == 80.0
