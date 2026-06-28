"""DispatchJob service — CRUD business logic and reclassification."""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.db.models.dispatch_job import ClassificationStatus, DispatchJob
from app.repositories import dispatch_job_repo, job_repo, openphone_repo
from app.schemas.dispatch_job import ClosingExtraction
from app.services.address_normalizer import normalize_address, normalize_phone
from app.services.classification import DEDUP_WINDOW_DAYS, JobClassificationService

logger = logging.getLogger(__name__)


class DispatchJobService:
    """Service for dispatch job CRUD and reclassification."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_job(self, job_id) -> DispatchJob:
        """Get a dispatch job by ID."""
        job = await dispatch_job_repo.get_by_id(self.db, job_id)
        if not job:
            raise NotFoundError(message="Dispatch job not found")
        return job

    async def list_jobs(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        status: str | None = None,
        company_id=None,
        since: datetime | None = None,
        until: datetime | None = None,
        exclude_statuses: list[str] | None = None,
        search: str | None = None,
    ) -> tuple[list[DispatchJob], int]:
        """List dispatch jobs with total count."""
        jobs = await dispatch_job_repo.list_dispatch_jobs(
            self.db,
            skip=skip,
            limit=limit,
            status=status,
            company_id=company_id,
            since=since,
            until=until,
            exclude_statuses=exclude_statuses,
            search=search,
        )
        total = await dispatch_job_repo.count_dispatch_jobs(
            self.db,
            status=status,
            company_id=company_id,
            since=since,
            until=until,
            exclude_statuses=exclude_statuses,
            search=search,
        )
        return jobs, total

    async def reclassify(self, job_id) -> DispatchJob:
        """Re-run classification on a dispatch job."""
        job = await dispatch_job_repo.get_by_id(self.db, job_id)
        if not job:
            raise NotFoundError(message="Dispatch job not found")

        message = await openphone_repo.get_incoming_message(self.db, job.incoming_message_id)
        if not message:
            raise NotFoundError(message="Original incoming message not found")

        classification_svc = JobClassificationService(self.db)

        # Reset job to pending and re-classify. The dedup step will reassign
        # job_id and classification_status; we clear them here so a stale
        # link doesn't survive a reclassify.
        await dispatch_job_repo.update_dispatch_job(
            self.db,
            job=job,
            classification_status="pending",
            classification_method=None,
            classification_error=None,
            company_id=None,
            job_id=None,
            address=None,
            job_type=None,
            total=None,
            parts=None,
            payment_method=None,
            tech_name=None,
            car_make=None,
            car_model=None,
            car_year=None,
            customer_name=None,
            customer_phone=None,
            scheduled_at=None,
            job_description=None,
            extraction_raw=None,
        )

        await classification_svc.classify_message(message)
        return job

    async def rematch_closing(self, dispatch_job_id) -> DispatchJob:
        """Re-attempt closing-to-Job matching for an unmatched closing.

        Used after the original Job lands later than the closing. No
        re-extraction — the stored ``extraction_raw`` (a ClosingExtraction
        dump) is replayed against the current Job table. The dispatch_job
        must already have ``company_id`` set (we don't re-run the company
        classifier here); if it doesn't, the operator should reclassify.
        """
        job = await dispatch_job_repo.get_by_id(self.db, dispatch_job_id)
        if not job:
            raise NotFoundError(message="Dispatch job not found")
        if job.classification_status != ClassificationStatus.CLOSING_UNMATCHED.value:
            raise ValidationError(
                message=(
                    "Rematch only valid for closing_unmatched dispatch jobs "
                    f"(current status: {job.classification_status})"
                )
            )
        if not job.company_id:
            raise ValidationError(message="Cannot rematch a closing without a classified company")
        if not job.extraction_raw:
            raise ValidationError(message="No stored closing extraction to replay")

        extraction = ClosingExtraction.model_validate(job.extraction_raw)
        normalized = normalize_address(extraction.address or "")
        customer_phone_e164 = normalize_phone(extraction.customer_phone)
        since = datetime.now(UTC) - timedelta(days=DEDUP_WINDOW_DAYS)

        original = await job_repo.find_for_closing(
            self.db,
            company_id=job.company_id,
            street_number=normalized.street_number,
            street_name=normalized.street_name,
            customer_phone_e164=customer_phone_e164,
            since=since,
        )
        if original is None:
            # Leave the row as closing_unmatched. The operator can
            # reclassify or wait for the original to land.
            logger.info(
                "REMATCH_CLOSING dispatch_job_id=%s no_original_found",
                dispatch_job_id,
            )
            return job

        await job_repo.mark_job_closed(
            self.db,
            job=original,
            closed_total=extraction.total,
            closed_parts=extraction.parts,
            closed_tip=extraction.tip,
            closed_payment_method=extraction.payment_method,
            closed_notes=extraction.notes,
            closed_at=datetime.now(UTC),
            closed_from_dispatch_job_id=job.id,
        )
        await dispatch_job_repo.update_dispatch_job(
            self.db,
            job=job,
            job_id=original.id,
            classification_status=ClassificationStatus.CLOSED.value,
            classification_error=None,
        )
        logger.info(
            "REMATCH_CLOSING dispatch_job_id=%s closed_original_id=%s",
            dispatch_job_id,
            original.id,
        )
        return job
