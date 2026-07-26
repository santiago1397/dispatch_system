"""Extract the identity of the job a re-pasted operator message refers to.

When an operator updates a company/broker about an existing job, they almost
always re-paste the original job block and append a note::

    Co: Always 24/7
    PDL: PY3YA
    Ph: 7739992940
    Addr: 6946 N Overhill Ave , Chicago, IL, 60631
    Desc: Bedroom Lockout
    Occu: Locksmith

    Notes:

    cx not answering to me or to new technician I left vm

That re-paste names the job precisely — but the reject/follow-up candidate
lookups in ``repositories/job.py`` historically ignored it and simply took
the *most recent open job from the same counterparty*. For a high-volume
broker that is close to a coin flip: AMS alone carries ~176 concurrent open
jobs, so an update meant for one job routinely landed on another.

This module pulls the three usable identity keys out of a message body, in
descending order of strength:

1. **PDL code** — the broker's own per-job reference (``PDL: PY3YA``). Unique
   per job and present on roughly a third of ingested messages. Matched
   against the raw text of the job's own messages, since the code is not
   modelled as a column.
2. **Customer phone** — normalized to the same E.164-ish form
   ``Job.customer_phone_e164`` stores.
3. **Street address** — normalized street number + name, matching
   ``Job.address_street_number`` / ``Job.address_street_name``.

Pure functions only: no DB access, no LLM. The lookup that consumes these
lives in ``repositories/job.py::find_job_by_reference_openphone``.
"""

import re
from dataclasses import dataclass

from app.services.address_normalizer import normalize_address, normalize_phone

# ``PDL: PY3YA`` / ``PDL PY3YA`` / ``pdl#PY3YA``. The code is alphanumeric
# and short; the length bound keeps it from swallowing a following word.
_PDL_RE = re.compile(r"\bPDL\s*[:#]?\s*([A-Z0-9]{4,8})\b", re.IGNORECASE)

# ``Ph: 7739992940`` / ``Phone: (773) 999-2940``. Anchored on the label so a
# technician's or dispatcher's number elsewhere in the body isn't picked up.
_PHONE_LABEL_RE = re.compile(
    r"\b(?:ph|phone|tel|cell)\s*[:#]?\s*(\+?[\d][\d\s().\-]{6,}\d)",
    re.IGNORECASE,
)

# ``Addr: 6946 N Overhill Ave , Chicago, IL, 60631``. Captures to end of line.
_ADDR_LABEL_RE = re.compile(
    r"\b(?:addr|address)\s*[:#]?\s*(.+)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class JobReference:
    """Identity keys extracted from a (possibly re-pasted) message body.

    Every field is optional — a bare "dns" reply yields an empty reference,
    which callers must treat as "no identity information", not as a match.
    """

    pdl: str | None = None
    customer_phone_e164: str | None = None
    street_number: str | None = None
    street_name: str | None = None

    @property
    def has_address(self) -> bool:
        """True when both address components are present (either alone is
        too weak to identify a job)."""
        return bool(self.street_number and self.street_name)

    def __bool__(self) -> bool:
        """True when at least one usable key was found."""
        return bool(self.pdl or self.customer_phone_e164 or self.has_address)


def extract_job_reference(body: str) -> JobReference:
    """Pull PDL / customer phone / street address out of ``body``.

    Tolerant of missing fields and of the WhatsApp/Quo formatting noise the
    bodies carry — anything not confidently found is left ``None``.
    """
    text = body or ""
    if not text.strip():
        return JobReference()

    pdl = None
    pdl_match = _PDL_RE.search(text)
    if pdl_match:
        pdl = pdl_match.group(1).upper()

    customer_phone_e164 = None
    phone_match = _PHONE_LABEL_RE.search(text)
    if phone_match:
        customer_phone_e164 = normalize_phone(phone_match.group(1))

    street_number = street_name = None
    addr_match = _ADDR_LABEL_RE.search(text)
    if addr_match:
        normalized = normalize_address(addr_match.group(1).strip())
        street_number = normalized.street_number
        street_name = normalized.street_name

    return JobReference(
        pdl=pdl,
        customer_phone_e164=customer_phone_e164,
        street_number=street_number,
        street_name=street_name,
    )
