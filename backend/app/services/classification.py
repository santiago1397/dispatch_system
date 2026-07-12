"""Job classification service — hybrid regex + AI pipeline.

Three-tier classification for incoming dispatch messages:
1. Sender phone number lookup (primary)
2. Regex pattern matching on message content
3. AI fallback classification

After company identification, extracts 13 structured fields via AI, then
runs a 14-day dedup lookup against the parent ``Job`` table. The dedup
matches when **either** the normalized address (street_number AND
street_name) **or** the normalized customer phone matches a Job inside
the window — ``job_type`` is not part of the match, so different
job types at the same address still flag as duplicates:

- Same-company match → append-only, the new ``DispatchJob`` is LINKED to
  the existing ``Job`` and gets ``classification_status=LINKED``.
- Cross-company match → a NEW ``Job`` is created with
  ``is_duplicate=True`` and ``duplicate_of=<first-seen-id>``. The flag is
  informational only — no side effect, no alert.
- No match → a NEW ``Job`` is created and the ``DispatchJob`` is
  ``CLASSIFIED``.
"""

import json
import logging
import re
import uuid
from datetime import UTC, datetime, timedelta

from langchain_openai import ChatOpenAI
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.models.company import Company
from app.db.models.dispatch_job import ClassificationStatus, DispatchJob
from app.db.models.job_lifecycle_event import LifecycleEventSource
from app.db.models.openphone import IncomingMessage
from app.repositories import company_repo, dispatch_job_repo, job_repo, phone_binding_repo
from app.schemas.dispatch_job import ClosingExtraction, CompanyClassification, JobExtraction
from app.services.address_normalizer import normalize_address, normalize_phone
from app.services.app_settings import AppSettingsService
from app.services.lifecycle import LifecycleService, LifecycleStatus
from app.services.timeparse import parse_iso8601

logger = logging.getLogger(__name__)

# === Job Detection Patterns ===

PHONE_PATTERN = re.compile(
    r"(?:\+?1[-.\s]?)?"
    r"(?:\(?\d{3}\)?[-.\s]?)?"
    r"\d{3}[-.\s]?\d{4}"
)

ADDRESS_PATTERNS = [
    # Labeled: "Address: 123 Main St, Chicago, IL 60601"
    re.compile(r"(?:Address|Addr|address|addr)\s*[:\.]\s*.+", re.IGNORECASE),
    # City/State/ZIP: "Chicago, IL 60601" or "Chicago, IL, 60601"
    re.compile(
        r"[A-Za-z\s]+,\s*[A-Z]{2}\s*,?\s*\d{5}",
        re.IGNORECASE,
    ),
    # Unlabeled street: "123 Main St, IL 60601"
    re.compile(
        r"\d+\s+[A-Za-z\s]+(?:St|Street|Ave|Avenue|Blvd|Boulevard|Dr|Drive|Ln|Lane|Rd|Road|Way|Ct|Court|Pl|Place)",
        re.IGNORECASE,
    ),
    # Precision: 2+ digit number + street name + zip
    re.compile(r"\d{2,}\s+[A-Za-z\s]+,\s*[A-Z]{2}\s*\d{5}", re.IGNORECASE),
]

DEDUP_WINDOW_DAYS = 14

# Closing pipeline branches on this chat_jid. Any WhatsApp message whose
# raw_payload.chat_jid equals this value is routed through
# ``_process_closing_message`` instead of the normal classifier — the
# phone+address gate and 13-field extractor don't apply to closings.
CLOSING_CHAT_JID = "wa-local:dispatch_closing"

# === WhatsApp DOM pollution normalizer ===
#
# When the extension takes ``textContent`` of the outer ``div.copyable-text``
# instead of the inner ``span.selectable-text``, the sender header and the
# trailing UI affordances ("Update? @<name>", time footer) get glued to the
# message body. That defeats every body-anchored regex pattern. The
# extension-side fix lives in ``content/scraper.js``; this normalizer keeps
# already-ingested rows (and any future regression) classifiable without
# rewriting the stored ``IncomingMessage.content``.
_LEADING_SENDER_HEADER = re.compile(r"^\s*\S.*?\+\d[\d\s().\-]{6,}\d")
_TRAILING_UPDATE_AFFORDANCE = re.compile(
    r"Update\?\s*@[^\n]*$",
    re.IGNORECASE,
)
_TRAILING_TIME = re.compile(
    r"\s*\d{1,2}:\d{2}\s*[ap]\.?\s*m\.?\s*$",
    re.IGNORECASE,
)


def _clean_for_match(content: str) -> str:
    """Strip WhatsApp DOM pollution before regex/AI sees the body.

    Removes the leading "<Sender Name>+<phone>" header and the trailing
    "Update? @<name>" + "H:MM p. m." footer that ``textContent`` flattens
    into the body. Idempotent — clean bodies pass through unchanged.
    """
    s = _TRAILING_TIME.sub("", content)
    s = _TRAILING_UPDATE_AFFORDANCE.sub("", s)
    s = _LEADING_SENDER_HEADER.sub("", s, count=1)
    return s.strip()


def _message_timestamp(message: IncomingMessage) -> datetime | None:
    """Real send time of ``message``, when known — not when we processed it.

    WhatsApp messages carry the DOM-scraped timestamp in
    ``raw_payload.timestamp`` (see ``whatsapp.py:ingest_batch``); a batch
    scrape can mirror a message hours or days after it was actually sent,
    so ``message.created_at`` is a poor proxy for "when did this job come
    in". OpenPhone messages have no such field — ``created_at`` there is
    already close to real-time (webhook delivery), so ``None`` is fine.
    """
    raw = (message.raw_payload or {}) if message.raw_payload else {}
    raw_ts = raw.get("timestamp")
    if not raw_ts:
        return None
    try:
        return datetime.fromisoformat(raw_ts)
    except (TypeError, ValueError):
        return None


class JobClassificationService:
    """Hybrid regex + AI classification with cross-message dedup."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def classify_message(self, message: IncomingMessage) -> DispatchJob:
        """Main entry point: detect job, classify company, extract fields, dedup."""
        # Pull the SW's batch_id from the IncomingMessage so every stage
        # transition log below ties back to the same FLUSH_CHUNK_REQUEST
        # the SW emitted. The whatsapp service stamps it in raw_payload
        # before kicking off classification; OpenPhone messages don't
        # have it and fall back to "-".
        batch_id = (message.raw_payload or {}).get("batch_id") or "-"
        logger.info(
            "CLASSIFY_ENTER batch_id=%s incoming_id=%s source=%s",
            batch_id,
            message.id,
            getattr(message, "source", "?"),
        )

        # 1. Get or create the DispatchJob in PENDING state. Reclassify
        # resets the existing row to pending before calling us, so reuse
        # it — the (incoming_message_id) unique index would otherwise
        # reject a second INSERT.
        try:
            job = await dispatch_job_repo.get_by_message_id(self.db, message.id)
            if job is None:
                job = await dispatch_job_repo.create_dispatch_job(
                    self.db,
                    incoming_message_id=message.id,
                )
                stage = "created_pending"
            else:
                stage = "reused_pending"
        except Exception as exc:
            logger.error(
                "CLASSIFY_CREATE_FAILED batch_id=%s incoming_id=%s exc_type=%s exc=%r",
                batch_id,
                message.id,
                type(exc).__name__,
                exc,
            )
            raise
        logger.info(
            "CLASSIFY_STAGE batch_id=%s stage=%s job_id=%s",
            batch_id,
            stage,
            job.id,
        )

        content = message.content or ""
        # Strip WhatsApp sender-header / time-footer pollution from the
        # text we hand to the matchers. The stored IncomingMessage.content
        # is left as-is so the raw payload remains auditable.
        match_content = _clean_for_match(content)

        # Closing-pipeline branch. Messages from the "Dispatch closing"
        # WhatsApp group carry payment/closing info for jobs that were
        # already classified from other tracked chats — they must skip
        # the strict phone+address job-detection gate (a closing note
        # rarely restates the full address) and run the closing-specific
        # extractor + matcher instead. They still need *some* matching key
        # (a phone or an address) to have any chance of finding the
        # original job — a bare "ok"/"thanks" has neither, so route those
        # to NOT_A_JOB instead of burning an AI extraction call just to
        # land on CLOSING_UNMATCHED.
        chat_jid = (message.raw_payload or {}).get("chat_jid")
        if getattr(message, "source", None) == "whatsapp" and chat_jid == CLOSING_CHAT_JID:
            if not self._has_matching_key(match_content):
                logger.info(
                    "CLASSIFY_STAGE batch_id=%s stage=not_a_job reason=closing_no_phone_or_address "
                    "job_id=%s",
                    batch_id,
                    job.id,
                )
                return await self._update_status(
                    job,
                    ClassificationStatus.NOT_A_JOB,
                    error="Closing chat message has no phone or address to match",
                )
            logger.info(
                "CLASSIFY_STAGE batch_id=%s stage=closing_branch chat_jid=%s job_id=%s",
                batch_id,
                chat_jid,
                job.id,
            )
            return await self._process_closing_message(
                job, match_content, batch_id, at=_message_timestamp(message)
            )

        if not content.strip():
            logger.info(
                "CLASSIFY_STAGE batch_id=%s stage=not_a_job reason=empty_content job_id=%s",
                batch_id,
                job.id,
            )
            return await self._update_status(
                job, ClassificationStatus.NOT_A_JOB, error="Empty message content"
            )

        # 2. Try sender phone number lookup (primary).
        company = await company_repo.get_by_phone_number(self.db, message.from_number)
        method = "phone" if company else None
        logger.info(
            "CLASSIFY_STAGE batch_id=%s stage=phone_lookup hit=%s company=%s",
            batch_id,
            bool(company),
            company.name if company else None,
        )

        # 3. Strict gate: must contain both a phone and an address pattern.
        if not self._is_job_message(match_content):
            logger.info(
                "CLASSIFY_STAGE batch_id=%s stage=not_a_job reason=no_phone_or_address job_id=%s",
                batch_id,
                job.id,
            )
            return await self._update_status(job, ClassificationStatus.NOT_A_JOB)

        # 4. Regex company match (if no phone match).
        if not company:
            company = await self._classify_company_regex(match_content)
            if company:
                method = "regex"
        logger.info(
            "CLASSIFY_STAGE batch_id=%s stage=regex_lookup hit=%s company=%s",
            batch_id,
            bool(company),
            company.name if company else None,
        )

        # 4.5. Operator-curated phone-binding fallback (OpenPhone only).
        # When body regex finds nothing, look the sender up in the
        # operator-curated bindings table. Regex wins on conflict — the
        # binding only fills the gap. WhatsApp messages skip this:
        # IncomingMessage.from_number for that source is a synthetic
        # chat_jid, not a real phone.
        is_openphone = getattr(message, "source", None) == "openphone"
        sender_phone_e164 = normalize_phone(message.from_number) if is_openphone else None
        if not company and sender_phone_e164:
            bound = await phone_binding_repo.get_company_by_phone(self.db, sender_phone_e164)
            if bound is not None:
                company, method = bound, "phone_binding"
                logger.info(
                    "CLASSIFY_STAGE batch_id=%s stage=phone_binding_hit company=%s",
                    batch_id,
                    bound.name,
                )

        # Audit-only: regex picked a company AND a binding exists pointing
        # somewhere else. Regex still wins (operator decision); we just log.
        if company is not None and method == "regex" and sender_phone_e164:
            bound = await phone_binding_repo.get_company_by_phone(self.db, sender_phone_e164)
            if bound is not None and bound.id != company.id:
                logger.info(
                    "CLASSIFY_BINDING_CONFLICT batch_id=%s regex_company=%s binding_company=%s",
                    batch_id,
                    company.name,
                    bound.name,
                )

        # 5. AI company fallback is disabled. When phone + regex + binding
        # all fail to identify a company, the message becomes FAILED
        # rather than being guessed — the AI guesser produced too many
        # misclassifications at the 0.5-confidence threshold.

        if not company:
            logger.info(
                "CLASSIFY_STAGE batch_id=%s stage=no_company job_id=%s",
                batch_id,
                job.id,
            )
            return await self._update_status(
                job, ClassificationStatus.FAILED, error="No company matched"
            )

        # 6. Extract fields via AI.
        try:
            extraction = await self._extract_fields(match_content, company)
        except Exception as e:
            logger.exception("Field extraction failed")
            logger.info(
                "CLASSIFY_STAGE batch_id=%s stage=extraction_failed company=%s error=%s",
                batch_id,
                company.name,
                e,
            )
            return await self._update_status(
                job,
                ClassificationStatus.FAILED,
                error=f"Extraction failed: {e}",
                company_id=company.id,
                classification_method=method,
            )
        logger.info(
            "CLASSIFY_STAGE batch_id=%s stage=extracted company=%s job_type=%s total=%s",
            batch_id,
            company.name,
            extraction.job_type,
            extraction.total,
        )

        # 7. Normalize the extracted address + phone and run the dedup
        # lookup. The lookup matches on (address) OR (phone), so either
        # signal alone is enough to flag a cross-company duplicate.
        normalized = normalize_address(extraction.address or "")
        customer_phone_e164 = normalize_phone(extraction.customer_phone)
        since = datetime.now(UTC) - timedelta(days=DEDUP_WINDOW_DAYS)

        candidate, is_cross_company = await job_repo.find_dedup_candidate(
            self.db,
            company_id=company.id,
            street_number=normalized.street_number,
            street_name=normalized.street_name,
            customer_phone_e164=customer_phone_e164,
            since=since,
        )
        if candidate is None:
            match_kind = "none"
        elif is_cross_company:
            match_kind = "cross_company"
        else:
            match_kind = "same_company"
        logger.info(
            "CLASSIFY_STAGE batch_id=%s stage=dedup_lookup match=%s candidate_id=%s",
            batch_id,
            match_kind,
            candidate.id if candidate is not None else None,
        )

        # 8a. Same-company dedup hit — append-only, link to existing Job.
        if candidate is not None and not is_cross_company:
            return await self._save_extraction(
                job=job,
                company=company,
                method="dedup",
                status=ClassificationStatus.LINKED,
                job_id=candidate.id,
                extraction=extraction,
                batch_id=batch_id,
            )

        # 8b/c. New Job — either truly new, or marked as cross-company duplicate.
        # Freeze the inbound contact so the outbound-draft pipeline always
        # reaches the same company. OpenPhone rows use ``from_number``;
        # WhatsApp rows leave it NULL (no verified number) and the draft
        # sender falls back to a generic "we received your job" message.
        if getattr(message, "source", None) == "openphone":
            inbound_channel = "openphone"
            original_inbound_from_number = normalize_phone(message.from_number)
        else:
            inbound_channel = getattr(message, "source", None) or "whatsapp"
            original_inbound_from_number = None

        new_job = await job_repo.create_job(
            self.db,
            company_id=company.id,
            first_message_at=_message_timestamp(message) or datetime.now(UTC),
            address_street_number=normalized.street_number,
            address_street_name=normalized.street_name,
            address_city=normalized.city,
            address_state=normalized.state,
            address_zip=normalized.zip_code,
            customer_phone_e164=customer_phone_e164,
            job_type=extraction.job_type,
            is_duplicate=candidate is not None,
            duplicate_of=candidate.id if candidate is not None else None,
            original_inbound_from_number=original_inbound_from_number,
            original_inbound_channel=inbound_channel,
        )

        # The inbound job request may already state an appointment date/time
        # (same-day or a future day) — reflect that immediately instead of
        # leaving a scheduled job sitting as "pending" as if unscheduled.
        # Same-day vs. future-day is derived downstream from ``appt_at`` vs.
        # ``first_message_at`` (see ``get_company_status_breakdown``).
        appt_dt = parse_iso8601(extraction.scheduled_at)
        if appt_dt is not None:
            await LifecycleService(self.db).transition(
                job=new_job,
                to_status=LifecycleStatus.APPT_SET,
                source=LifecycleEventSource.CLASSIFICATION,
                payload={"appt_iso": extraction.scheduled_at},
                at=_message_timestamp(message) or datetime.now(UTC),
            )

        return await self._save_extraction(
            job=job,
            company=company,
            method=method,
            status=ClassificationStatus.CLASSIFIED,
            job_id=new_job.id,
            extraction=extraction,
            batch_id=batch_id,
        )

    @staticmethod
    def _is_job_message(content: str) -> bool:
        """Check if message contains a phone number AND an address."""
        has_phone = bool(PHONE_PATTERN.search(content))
        if not has_phone:
            return False
        return any(p.search(content) for p in ADDRESS_PATTERNS)

    @staticmethod
    def _has_matching_key(content: str) -> bool:
        """Check if message contains a phone number OR an address.

        Looser than ``_is_job_message`` (which requires both) — used to
        gate the closing-chat branch, where ``find_for_closing`` only
        needs one matching key (address OR phone) to locate the original
        Job, not both.
        """
        if PHONE_PATTERN.search(content):
            return True
        return any(p.search(content) for p in ADDRESS_PATTERNS)

    async def _classify_company_regex(self, content: str) -> Company | None:
        """Try all active companies' regex patterns. Returns first match."""
        companies = await company_repo.get_all_active(self.db)

        for company in companies:
            pattern_groups = company.identification_patterns or []
            for group in pattern_groups:
                patterns = group.get("patterns", [])
                if not patterns:
                    continue
                # ALL patterns in a group must match
                if all(re.search(p, content, re.IGNORECASE | re.MULTILINE) for p in patterns):
                    logger.info(
                        f"Regex match: company={company.name} (matched {len(patterns)} patterns)"
                    )
                    return company

        return None

    async def _classify_company_ai(self, content: str) -> Company | None:
        """Use AI to classify which company sent this message."""
        try:
            companies = await company_repo.get_all_active(self.db)
            company_names = [c.name for c in companies]

            if not company_names:
                return None

            llm_config = await AppSettingsService(self.db).get_llm_config()
            llm = ChatOpenAI(
                model=settings.AI_MODEL,
                temperature=0.1,
                base_url=llm_config.base_url,
                api_key=llm_config.api_key,
            )
            structured_llm = llm.with_structured_output(CompanyClassification)

            prompt = (
                "You are a dispatch message classifier. Given a job dispatch message and a list "
                "of known companies, identify which company sent this message.\n\n"
                f"Known companies: {json.dumps(company_names)}\n\n"
                f"Message:\n{content[:2000]}\n\n"
                "Respond with the company name that best matches, your confidence (0-1), "
                "and reasoning. If no company matches, return null for company_name."
            )

            result = await structured_llm.ainvoke(prompt)

            if result.company_name and result.confidence >= 0.5:
                company = await company_repo.get_by_name(self.db, result.company_name)
                if company:
                    logger.info(
                        f"AI match: company={company.name} (confidence={result.confidence}, "
                        f"reasoning={result.reasoning})"
                    )
                    return company

            logger.info(
                f"AI classification: no match (confidence={result.confidence}, "
                f"suggested={result.company_name})"
            )
            return None

        except Exception:
            logger.exception("AI company classification failed")
            return None

    async def _save_extraction(
        self,
        *,
        job: DispatchJob,
        company: Company,
        method: str,
        status: ClassificationStatus,
        job_id: uuid.UUID,
        extraction: JobExtraction,
        batch_id: str = "-",
    ) -> DispatchJob:
        """Persist the LLM extraction onto the DispatchJob row."""
        await dispatch_job_repo.update_dispatch_job(
            self.db,
            job=job,
            company_id=company.id,
            job_id=job_id,
            classification_status=status.value,
            classification_method=method,
            address=extraction.address,
            job_type=extraction.job_type,
            total=extraction.total,
            parts=extraction.parts,
            payment_method=extraction.payment_method,
            tech_name=extraction.tech_name,
            car_make=extraction.car_make,
            car_model=extraction.car_model,
            car_year=extraction.car_year,
            customer_name=extraction.customer_name,
            customer_phone=extraction.customer_phone,
            scheduled_at=extraction.scheduled_at,
            job_description=extraction.job_description,
            extraction_raw=extraction.model_dump(),
        )

        logger.info(
            "CLASSIFY_STAGE batch_id=%s stage=finalized status=%s company=%s "
            "method=%s job_type=%s job_id=%s dispatch_job_id=%s",
            batch_id,
            status.value,
            company.name,
            method,
            extraction.job_type,
            job_id,
            job.id,
        )
        return job

    async def _extract_fields(self, content: str, company: Company) -> JobExtraction:
        """Use AI to extract 13 fields from the message."""
        llm_config = await AppSettingsService(self.db).get_llm_config()
        llm = ChatOpenAI(
            model=settings.AI_MODEL,
            temperature=0.0,
            base_url=llm_config.base_url,
            api_key=llm_config.api_key,
        )
        structured_llm = llm.with_structured_output(JobExtraction)

        prompt = (
            "You are a dispatch data extractor. Extract structured information from this "
            "job dispatch message.\n\n"
            f"Company: {company.display_name or company.name}\n"
            f"Message:\n{content[:3000]}\n\n"
            "Extract the following fields if present:\n"
            "- address: The service address\n"
            "- job_type: Type of job (e.g., House Lockout, Car Lockout, Rekey, "
            "Ignition replacement, Garage Door Service, etc.)\n"
            "- total: Total price charged (include $ sign if present)\n"
            "- parts: Parts cost or parts mentioned\n"
            "- payment_method: How the customer will pay (cash, card, zelle, cash app, etc.)\n"
            "- tech_name: Name of the technician assigned\n"
            "- car_make: Vehicle make (only if automotive job)\n"
            "- car_model: Vehicle model (only if automotive job)\n"
            "- car_year: Vehicle year (only if automotive job)\n"
            "- customer_name: Name of the customer (not the technician)\n"
            "- customer_phone: Phone number of the customer (not the dispatcher)\n"
            "- scheduled_at: The appointment/arrival date+time, if mentioned. Messages "
            "often state the date and time window as separate fields (e.g. "
            "\"Date: 7/10/2026\" and \"Hours: 12:00 PM to 2:00 PM\") — combine them into "
            "a single ISO-8601 datetime using the START of the time window and the exact "
            "year given in the message (never assume the current year). "
            "Example: \"Date: 7/10/2026\" + \"Hours: 12:00 PM to 2:00 PM\" -> "
            "\"2026-07-10T12:00:00\". If no date/time is mentioned, set to null.\n"
            "- job_description: Free-text description of what the job involves\n\n"
            "Only extract values that are clearly present in the message. "
            "Set to null if not found."
        )

        return await structured_llm.ainvoke(prompt)

    async def _process_closing_message(
        self,
        job: DispatchJob,
        match_content: str,
        batch_id: str,
        *,
        at: datetime | None = None,
    ) -> DispatchJob:
        """Closing-message pipeline.

        ``at`` is the closing message's real send time (WhatsApp only),
        used to stamp ``closed_at``/the lifecycle event instead of
        processing time — see ``_message_timestamp``.

        1. Run the same regex/AI company classifier on the closing body.
           Phone lookup is skipped because the WhatsApp ``from_number``
           is a synthetic chat_jid for the "Dispatch closing" group, not
           the tech's real phone.
        2. AI-extract the closing fields (total/parts/tip/payment_method
           + matching keys address & customer_phone + free-text notes).
        3. Find the original Job within the 14-day window by
           (company_id) AND (address OR phone). Oldest-first ("the
           original first job classified").
        4a. Match → mark the parent Job closed and finalize this
            DispatchJob as ``CLOSED``.
        4b. No match → finalize as ``CLOSING_UNMATCHED``. The extraction
            sits on ``extraction_raw`` so the rematch endpoint can
            replay it once the original Job lands.
        """
        if not match_content.strip():
            return await self._update_status(
                job,
                ClassificationStatus.CLOSING_UNMATCHED,
                error="Closing message body empty",
                classification_method="closing",
            )

        # 1. Company classification (regex first, then AI fallback).
        company = await self._classify_company_regex(match_content)
        company_method = "regex" if company else None
        if not company:
            company = await self._classify_company_ai(match_content)
            company_method = "ai" if company else None
        logger.info(
            "CLASSIFY_STAGE batch_id=%s stage=closing_company hit=%s company=%s method=%s",
            batch_id,
            bool(company),
            company.name if company else None,
            company_method,
        )

        # 2. Closing field extraction. Done before the match attempt so
        # unmatched closings still persist the amounts for later replay.
        try:
            extraction = await self._extract_closing_fields(match_content)
        except Exception as e:
            logger.exception("Closing extraction failed")
            return await self._update_status(
                job,
                ClassificationStatus.CLOSING_UNMATCHED,
                error=f"Closing extraction failed: {e}",
                company_id=company.id if company else None,
                classification_method="closing",
            )
        logger.info(
            "CLASSIFY_STAGE batch_id=%s stage=closing_extracted total=%s tip=%s payment=%s",
            batch_id,
            extraction.total,
            extraction.tip,
            extraction.payment_method,
        )

        # Stash the extraction on the dispatch_job up-front. Standard
        # columns get the closing actuals so the operator can see them
        # in the list view; extraction_raw keeps the full structured
        # output so rematch can replay matching keys + tip + notes.
        await dispatch_job_repo.update_dispatch_job(
            self.db,
            job=job,
            company_id=company.id if company else None,
            classification_method="closing",
            address=extraction.address,
            total=extraction.total,
            parts=extraction.parts,
            payment_method=extraction.payment_method,
            customer_phone=extraction.customer_phone,
            extraction_raw=extraction.model_dump(),
        )

        if company is None:
            return await self._update_status(
                job,
                ClassificationStatus.CLOSING_UNMATCHED,
                error="No company matched for closing",
                classification_method="closing",
            )

        # 3. Find the original Job.
        normalized = normalize_address(extraction.address or "")
        customer_phone_e164 = normalize_phone(extraction.customer_phone)
        since = datetime.now(UTC) - timedelta(days=DEDUP_WINDOW_DAYS)

        original = await job_repo.find_for_closing(
            self.db,
            company_id=company.id,
            street_number=normalized.street_number,
            street_name=normalized.street_name,
            customer_phone_e164=customer_phone_e164,
            since=since,
        )
        logger.info(
            "CLASSIFY_STAGE batch_id=%s stage=closing_match found=%s original_job_id=%s",
            batch_id,
            bool(original),
            original.id if original else None,
        )

        if original is None:
            return await self._update_status(
                job,
                ClassificationStatus.CLOSING_UNMATCHED,
                error="No matching original Job within 14-day window",
                company_id=company.id,
                classification_method="closing",
            )

        # 4. Stamp closing fields on the parent Job + finalize.
        await job_repo.mark_job_closed(
            self.db,
            job=original,
            closed_total=extraction.total,
            closed_parts=extraction.parts,
            closed_tip=extraction.tip,
            closed_payment_method=extraction.payment_method,
            closed_notes=extraction.notes,
            closed_at=at or datetime.now(UTC),
            closed_from_dispatch_job_id=job.id,
        )
        await dispatch_job_repo.update_dispatch_job(
            self.db,
            job=job,
            job_id=original.id,
            classification_status=ClassificationStatus.CLOSED.value,
            classification_method="closing",
        )

        # 5. Phase-3 lifecycle hook: closing → ``closed`` transition + draft.
        # ``LifecycleService.transition`` is the single gate — it appends an
        # audit event, stamps lifecycle_status, auto-resolves stuck alerts,
        # and creates an outbound draft addressed to the Job's original
        # inbound contact. Failures here must not block the closing path;
        # log and continue so the closing itself still lands.
        try:
            from app.services.lifecycle import LifecycleService

            await LifecycleService(self.db).transition(
                job=original,
                to_status="closed",
                source="closing_chat",
                payload={
                    "closed_total": extraction.total,
                    "closed_payment_method": extraction.payment_method,
                    "dispatch_job_id": str(job.id),
                },
                at=at,
            )
        except Exception:
            logger.exception(
                "Closing lifecycle transition failed for job_id=%s",
                original.id,
            )
        logger.info(
            "CLASSIFY_STAGE batch_id=%s stage=closed company=%s original_job_id=%s "
            "dispatch_job_id=%s",
            batch_id,
            company.name,
            original.id,
            job.id,
        )
        return job

    async def _extract_closing_fields(self, content: str) -> ClosingExtraction:
        """AI-extract closing/payment fields from a "Dispatch closing" message.

        The prompt instructs the model to ignore estimates that appear
        earlier in the message — closings often re-paste the original job
        with its estimate, then the actuals follow at the end.
        """
        llm_config = await AppSettingsService(self.db).get_llm_config()
        llm = ChatOpenAI(
            model=settings.AI_MODEL,
            temperature=0.0,
            base_url=llm_config.base_url,
            api_key=llm_config.api_key,
        )
        structured_llm = llm.with_structured_output(ClosingExtraction)

        prompt = (
            "You are a dispatch CLOSING extractor. The message below was sent to "
            "the 'Dispatch closing' WhatsApp group when a job was completed. "
            "Extract the FINAL payment/closing information.\n\n"
            "CRITICAL: The closing/payment amounts are ALWAYS at the END or in "
            "the SECOND HALF of the message. Any amounts that appear earlier are "
            "ESTIMATES from the original job dispatch — DO NOT return those. "
            "If the message contains both an estimate at the top and a final "
            "total at the bottom, return ONLY the final total.\n\n"
            f"Message:\n{content[:3000]}\n\n"
            "Extract these fields (set to null if not clearly present):\n"
            "- address: The service address (used to match back to the original job)\n"
            "- customer_phone: Customer phone (used to match back to the original job)\n"
            "- total: FINAL total amount charged (include $ sign if present)\n"
            "- parts: Parts cost or parts purchased\n"
            "- tip: Tip amount\n"
            "- payment_method: cash, cc, zelle, check, or other\n"
            "- notes: Any additional closing notes (warranty, split payment, etc.)"
        )

        return await structured_llm.ainvoke(prompt)

    async def _update_status(
        self,
        job: DispatchJob,
        status: ClassificationStatus,
        *,
        error: str | None = None,
        company_id: uuid.UUID | None = None,
        classification_method: str | None = None,
    ) -> DispatchJob:
        """Update job classification status (used for NOT_A_JOB / FAILED / etc.)."""
        await dispatch_job_repo.update_dispatch_job(
            self.db,
            job=job,
            classification_status=status.value,
            classification_error=error,
            company_id=company_id,
            classification_method=classification_method,
        )
        return job
