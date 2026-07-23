"""Operator job-rejection detector.

When a job lands in a source chat (WhatsApp company group or Quo), the
operator may decline it by replying — within the next two operator
messages — with a short phrase ("pass", "have it", "i have it",
"<zip> pass", "cant take") or by re-pasting the job body with a small
note at the bottom. When that happens the parent ``Job`` is transitioned
to the terminal ``rejected`` status so the alert engine never flags it as
stuck/unclosed (it will never be dispatched).

This module holds only the *pure* signal detection — matching a reply
body (optionally against the job it follows) to a reject signal. The
orchestration (finding the target job, enforcing the two-operator-message
window, and running the lifecycle transition) lives in
``WhatsappService._maybe_reject_job``.

The phrase list is intentionally a module constant rather than a DB
setting: the vocabulary is small and stable, and keeping it in code keeps
the hot ingest path allocation-free. Promote it to ``app_settings`` only
if operators need to edit it without a deploy.
"""

import re
from difflib import SequenceMatcher

# Exact operator reject phrases (compared after normalization). "have it"
# / "i have it" read as rejections in the shared-group model: they mean
# another dispatcher already claimed the job, so we are NOT taking it.
REJECT_PHRASES: frozenset[str] = frozenset(
    {
        "have it",
        "i have it",
        "we have it",
        "pass",
        "passing",
        "pass on this",
        "cant take",
        "can't take",
        "cannot take",
        "cant take it",
        "can't take it",
        "cant take this",
        "can't take this",
        # "cant do" family — operator declines a specific job
        "cant do",
        "can't do",
        "cannot do",
        "cant do it",
        "can't do it",
        "no can do",
        # "cant help" family
        "cant help",
        "can't help",
        "cannot help",
        "sorry cant help",
        "sorry can't help",
    }
)

# A "<zip> pass" style reply: a 5-digit ZIP plus a pass token, nothing
# else of substance. Kept separate from REJECT_PHRASES because the ZIP is
# variable. ``_ZIP_PASS_MAX_TOKENS`` guards against matching a full job
# message that merely happens to contain the word "pass".
_ZIP_RE = re.compile(r"\b\d{5}\b")
_PASS_TOKEN_RE = re.compile(r"\b(?:pass|passing)\b")
_ZIP_PASS_MAX_TOKENS = 4

# Prefix check: handles "cant do, too old" / "pass, no parts" — operator
# adds a short reason after the reject phrase. After normalization commas
# become spaces, so "cant do, too old" → "cant do too old" which starts
# with "cant do ". Only applied when the message is short enough that it
# can't be a re-pasted job body.
_REJECT_PREFIX_MAX_TOKENS = 12

# Free-form keyword patterns for short messages where the operator writes
# a natural-language decline ("sorry we have no one for now", "no one
# available at the moment"). Only matched when the message is short.
_REJECT_KEYWORD_PATTERNS: list = [
    re.compile(r"\bno\s+one\b", re.IGNORECASE),  # "no one for now", "no one available"
    re.compile(r"\bnobody\s+available\b", re.IGNORECASE),
    re.compile(r"\bno\s+techs?\s+available\b", re.IGNORECASE),
    re.compile(r"\bno\s+one\s+available\b", re.IGNORECASE),
    re.compile(r"\bnot\s+available\b", re.IGNORECASE),
    re.compile(r"\bsorry\b.{0,40}\bno\b", re.IGNORECASE),  # "sorry, we have no..."
]
_REJECT_KEYWORD_MAX_TOKENS = 12

# Re-paste-with-note: the operator copies the job body and appends a short
# note ("...too far", "pass, no parts"). We treat it as a reject when the
# reply contains (or closely matches) the job body plus only a small tail.
_REPASTE_SIMILARITY_THRESHOLD = 0.75
_REPASTE_NOTE_MAX_CHARS = 200
# Don't attempt re-paste matching against a trivially short job body — a
# 10-char "job" would match almost anything and produce false positives.
_REPASTE_MIN_JOB_CHARS = 25

_PUNCT_STRIP_RE = re.compile(r"[.,!?;:¡¿*_\-\"'`]+")
_WS_RE = re.compile(r"\s+")

# A re-paste's appended note that reads as a question or a data-correction
# flag ("K?", "wrong number, pls check") is NOT a decline — the operator is
# telling the source chat that a field looks wrong, not passing on the job.
# Matched against the *raw* body (before punctuation is stripped) so "?"
# survives; a genuine decline reason ("too far", "no parts") never trips
# this, so it doesn't affect the existing reject path.
_DATA_QUESTION_RE = re.compile(
    r"\?"
    r"|\bwrong\s+(?:number|address|phone|info)\b"
    r"|\bcorrect\s+(?:number|address|phone)\b"
    r"|\b(?:check|confirm|verify)\b",
    re.IGNORECASE,
)

# A re-paste's appended note that reads as an appointment confirmation
# ("Appt 11:30 am", "Appt tomorrow 10:30 am") is NOT a decline — the
# operator is reporting that a time was set, the opposite of passing on the
# job. Regression: "PDL: TUKZD" / Kimberly / 2300 College Green Drive job
# was marked ``rejected`` off a re-paste whose only added text was an
# appointment time with no "?" and no data-quality wording, so the existing
# _DATA_QUESTION_RE veto didn't cover it.
_APPT_NOTE_RE = re.compile(
    r"\bappt\b|\bappointment\b|\btomorrow\b|\btmrw\b|\btomm?orow\b"
    r"|\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b",
    re.IGNORECASE,
)

# A re-paste's appended note that reads as a payment/settlement report
# ("Total: 174$ cc", "Paid $600", "4100$cc SADAN") is NOT a decline — the
# tech is closing the job out, the opposite of passing on it. This mirrors
# the keyword+amount adjacency check in ``closing_signal.py``'s
# ``_SETTLEMENT_RE``; kept as a separate, duplicated pattern here (rather
# than importing ``closing_signal``) to keep this module's pure/allocation
# -free import graph free of that module's DB-session-dependent services.
# Regression: "Total: 174$ cc" / "1908 N Cambridge Ct 3a" job was marked
# ``rejected`` off a closing re-paste because the closing-signal gate
# missed it (separate address-extraction bug) and this note matched
# neither the data-question nor the appointment veto.
_PAYMENT_NOTE_KEYWORD = (
    r"(?:paid|pay|parts?|tip|cash|cc|zelle|venmo|card|charged|collected|total|closed?)"
)
_PAYMENT_NOTE_AMOUNT = r"(?:\$\s?\d+(?:[.,]\d+)?|\d+(?:[.,]\d+)?\s?\$|\d{2,}(?:[.,]\d+)?)"
_PAYMENT_NOTE_RE = re.compile(
    rf"\b{_PAYMENT_NOTE_KEYWORD}\b[\s:]{{0,10}}{_PAYMENT_NOTE_AMOUNT}\b"
    rf"|\b{_PAYMENT_NOTE_AMOUNT}\b[\s:]{{0,10}}\b{_PAYMENT_NOTE_KEYWORD}\b",
    re.IGNORECASE,
)

# A re-paste's appended note that reports the tech heading to the job
# ("On way", "OMW", "en route", "heading over") is NOT a decline — it's
# the opposite, progress on a job being worked. Regression: "17200 Fox
# Grove Ln, Tinley Park" (AMS) was marked ``rejected`` off a re-paste
# whose only added text was "On way".
_EN_ROUTE_NOTE_RE = re.compile(
    r"\bon\s*(?:my|the)?\s*way\b|\bomw\b|\ben\s*route\b|\bheading\s+(?:over|there|out)\b"
    r"|\bon\s+route\b|\botw\b",
    re.IGNORECASE,
)

# A re-paste's appended note reporting the customer wasn't there when the
# tech arrived ("she's not there anymore", "no one home", "cx gone"), or
# that the customer resolved the problem themselves and the job is no
# longer needed ("cx found his key. DNS", "no longer needs us"), is a
# CANCELLATION reason, not a plain decline — the job was accepted (and
# possibly worked), but doesn't need to be completed. Distinguished from
# ``REJECT_PHRASES`` (which mean "we are not taking this job at all") so
# it can route to the ``canceled`` terminal status instead of
# ``rejected``. See :func:`is_cancel_signal`.
# Regression: "428 N Elmwood Ave, Waukegan" (Always 24/7) was marked
# ``rejected`` off a re-paste whose only added note was "Cx found his
# key. DNS" — a self-resolved cancellation, not a decline.
_CUSTOMER_UNAVAILABLE_NOTE_RE = re.compile(
    r"\bnot\s+there\s+anymore\b"
    r"|\b(?:isn'?t|is\s+not|wasn'?t|was\s+not)\s+(?:there|home)\b"
    r"|\bno\s+(?:one|body)\s+(?:home|there|answer(?:ed|ing)?)\b"
    r"|\bnobody\s+(?:home|there)\b"
    r"|\bno\s+answer\b"
    r"|\b(?:customer|cx|client)\s+(?:gone|left|not\s+(?:home|there))\b"
    r"|\balready\s+left\b"
    r"|\bfound\s+(?:his|her|their|a)\s+key\b"
    r"|\bdns\b"
    r"|\bno\s+longer\s+need(?:s|ed)?\b"
    r"|\b(?:cx|customer|client)\s+(?:resolved|handled|fixed)\s+it\b"
    r"|\bcancel(?:l?ed)?\s+on\s+(?:his|her|their)\s+own\b",
    re.IGNORECASE,
)

# A re-paste's appended note reporting a deposit taken and/or a future
# close date ("took 50 deposit will close job Tuesday Wednesday") is NOT
# a decline — it's the tech confirming the job is scheduled/accepted and
# reporting progress toward closing it, the opposite of passing on it.
# Distinct from ``_PAYMENT_NOTE_RE`` (a completed settlement report, which
# is also handled by ``closing_signal.py``'s dedicated gate): here no
# final total has landed yet, just a deposit and a future close date, so
# it reads closest to an appointment/scheduling update.
# Regression: "2885 Foxwood Dr, New Lenox" (Always 24/7) was marked
# ``rejected`` off a re-paste whose only added note was "took 50 deposit
# will close job Tusday Wednesday" — an in-progress scheduling update.
_DEPOSIT_NOTE_RE = re.compile(
    r"\bdeposit\b"
    r"|\bwill\s+close\b"
    r"|\bclose\s+(?:the\s+)?job\b",
    re.IGNORECASE,
)


def _looks_like_en_route_note(body: str) -> bool:
    """True if ``body`` reads as the tech heading to the job, not a decline."""
    return bool(_EN_ROUTE_NOTE_RE.search(body))


def _looks_like_customer_unavailable_note(body: str) -> bool:
    """True if ``body`` reports the customer wasn't there — a cancel reason."""
    return bool(_CUSTOMER_UNAVAILABLE_NOTE_RE.search(body))


def _looks_like_deposit_note(body: str) -> bool:
    """True if ``body`` reports a deposit taken / future close date rather
    than a job decline (see :data:`_DEPOSIT_NOTE_RE`)."""
    return bool(_DEPOSIT_NOTE_RE.search(body))


def _looks_like_data_question(body: str) -> bool:
    """True if ``body`` reads as a question / data-correction request
    rather than a job decline (see :data:`_DATA_QUESTION_RE`)."""
    return bool(_DATA_QUESTION_RE.search(body))


def _looks_like_appt_note(body: str) -> bool:
    """True if ``body`` reads as an appointment confirmation rather than a
    job decline (see :data:`_APPT_NOTE_RE`)."""
    return bool(_APPT_NOTE_RE.search(body))


def _looks_like_payment_note(body: str) -> bool:
    """True if ``body`` reads as a payment/settlement report rather than a
    job decline (see :data:`_PAYMENT_NOTE_RE`)."""
    return bool(_PAYMENT_NOTE_RE.search(body))


def _normalize(text: str) -> str:
    """Lowercase, strip surrounding punctuation, and collapse whitespace."""
    lowered = text.lower().strip()
    lowered = _PUNCT_STRIP_RE.sub(" ", lowered)
    return _WS_RE.sub(" ", lowered).strip()


# Phrases compared after normalization — apostrophes/punctuation in
# ``REJECT_PHRASES`` ("can't take") are stripped the same way the reply is,
# so the human-readable source list and the match set stay in sync.
_NORMALIZED_REJECT_PHRASES: frozenset[str] = frozenset(_normalize(p) for p in REJECT_PHRASES)


def is_reject_phrase(body: str) -> bool:
    """True if ``body`` is a standalone operator reject phrase.

    Matches (in order):
    1. Exact phrase list.
    2. "<zip> pass" pattern.
    3. Prefix match for short messages — "cant do, too old" / "pass, no
       parts" — where a reject phrase leads and the operator appends a
       brief reason. After normalization commas become spaces so
       "cant do, too old" → "cant do too old" which starts with "cant do ".
    4. Free-form keyword patterns for short natural-language declines
       ("sorry we have no one for now").

    Does NOT consider re-pastes — use :func:`is_reject_signal` for that.
    """
    normalized = _normalize(body)
    if not normalized:
        return False
    if normalized in _NORMALIZED_REJECT_PHRASES:
        return True

    tokens = normalized.split()

    # "<zip> pass" / "pass <zip>"
    if (
        len(tokens) <= _ZIP_PASS_MAX_TOKENS
        and _PASS_TOKEN_RE.search(normalized)
        and _ZIP_RE.search(normalized)
    ):
        return True

    # Prefix check: reject phrase + short extra context
    if len(tokens) <= _REJECT_PREFIX_MAX_TOKENS:
        for phrase in _NORMALIZED_REJECT_PHRASES:
            if normalized == phrase or normalized.startswith(phrase + " "):
                return True

    # Free-form keyword patterns (short messages only)
    if len(tokens) <= _REJECT_KEYWORD_MAX_TOKENS:
        for pattern in _REJECT_KEYWORD_PATTERNS:
            if pattern.search(normalized):
                return True

    return False


def is_repaste_with_note(body: str, job_body: str) -> bool:
    """True if ``body`` is a re-paste of ``job_body`` plus a short note.

    Two acceptance paths:
    1. Containment — the normalized job body is a substring of the reply
       and the extra text (the note) is short.
    2. Similarity — the reply is highly similar to the job body and is at
       least as long as it (i.e. it re-pastes then appends).

    Either path is vetoed when the note itself reads as a question or a
    data-correction request (see :func:`_looks_like_data_question`), as an
    appointment confirmation (see :func:`_looks_like_appt_note`), as a
    payment/settlement report (see :func:`_looks_like_payment_note`), as
    the tech heading to the job (see :func:`_looks_like_en_route_note`), or
    as a deposit-taken / future-close-date update (see
    :func:`_looks_like_deposit_note`) rather than an actual decline — an
    operator flagging "wrong number, pls check", reporting "Appt tomorrow
    10:30 am", a tech closing out with "Total: 174$ cc", "On way", or
    "took 50 deposit will close job Tuesday Wednesday" back to the source
    chat is not passing on the job. A note matching
    :func:`_looks_like_customer_unavailable_note` is also excluded here —
    it is not a plain decline either, but is routed to the ``canceled``
    status via :func:`is_cancel_signal` instead of merely being vetoed.
    """
    job_norm = _normalize(job_body)
    reply_norm = _normalize(body)
    if len(job_norm) < _REPASTE_MIN_JOB_CHARS or not reply_norm:
        return False
    # A bare re-paste with no added note is not a rejection — the operator
    # must have appended *something* (the decline note).
    if reply_norm == job_norm:
        return False

    def _not_vetoed() -> bool:
        return not (
            _looks_like_data_question(body)
            or _looks_like_appt_note(body)
            or _looks_like_payment_note(body)
            or _looks_like_en_route_note(body)
            or _looks_like_customer_unavailable_note(body)
            or _looks_like_deposit_note(body)
        )

    if job_norm in reply_norm:
        extra = len(reply_norm) - len(job_norm)
        if not (0 < extra <= _REPASTE_NOTE_MAX_CHARS):
            return False
        return _not_vetoed()

    if len(reply_norm) >= len(job_norm):
        ratio = SequenceMatcher(None, job_norm, reply_norm).ratio()
        if ratio < _REPASTE_SIMILARITY_THRESHOLD:
            return False
        return _not_vetoed()
    return False


def is_reject_signal(body: str, job_body: str | None = None) -> bool:
    """True if the operator reply ``body`` rejects the job it follows.

    ``job_body`` (the body of the job message being replied to) is needed
    only for the re-paste path; pass ``None`` to check phrases alone.
    """
    if not body or not body.strip():
        return False
    if is_reject_phrase(body):
        return True
    return bool(job_body and is_repaste_with_note(body, job_body))


def is_repaste_with_cancel_note(body: str, job_body: str) -> bool:
    """True if ``body`` is a re-paste of ``job_body`` plus a note reporting
    the customer wasn't there (see :data:`_CUSTOMER_UNAVAILABLE_NOTE_RE`).

    Same containment/similarity structure as :func:`is_repaste_with_note`,
    but requires the note to positively match the customer-unavailable
    wording rather than merely fail the decline vetoes — a bare re-paste
    with an unrelated short note is not a cancel signal.
    """
    job_norm = _normalize(job_body)
    reply_norm = _normalize(body)
    if len(job_norm) < _REPASTE_MIN_JOB_CHARS or not reply_norm:
        return False
    if reply_norm == job_norm:
        return False
    if not _looks_like_customer_unavailable_note(body):
        return False

    if job_norm in reply_norm:
        extra = len(reply_norm) - len(job_norm)
        return 0 < extra <= _REPASTE_NOTE_MAX_CHARS

    if len(reply_norm) >= len(job_norm):
        ratio = SequenceMatcher(None, job_norm, reply_norm).ratio()
        return ratio >= _REPASTE_SIMILARITY_THRESHOLD
    return False


def is_cancel_signal(body: str, job_body: str | None = None) -> bool:
    """True if the operator reply ``body`` reports the job needs to be
    canceled (tech got there, but the customer wasn't there) rather than
    declined outright.

    Checked by callers *before* :func:`is_reject_signal` so a message that
    matches both (unlikely, given the disjoint wording) reads as a cancel,
    which carries more information for the operator than a bare reject.
    """
    if not body or not body.strip():
        return False
    return bool(job_body and is_repaste_with_cancel_note(body, job_body))


# =============================================================================
# Technician accept / reject signals
# =============================================================================
#
# After a job is dispatched to a technician's chat, the tech confirms
# ("ok"/"k"/…) or declines ("pass"/"cant"/"no"). These are short, standalone
# replies — matched by exact normalized equality so a long sentence that
# merely contains "no" or "cant" falls through to the LLM intent parser
# instead (where "cant make it, customer not home" reads as ``canceled``,
# not a re-dispatch). ``_normalize`` already strips punctuation and collapses
# repeated whitespace, so "OK!", "k." and "ok 👍" all reduce to the tokens
# below.

TECH_ACCEPT_PHRASES: frozenset[str] = frozenset(
    {
        "ok",
        "okay",
        "k",
        "kk",
        "yes",
        "yep",
        "yeah",
        "ya",
        "yup",
        "sure",
        "got it",
        "gotit",
        "on it",
        "onit",
        "im on it",
        "i got it",
        "copy",
        "copy that",
        "will do",
        "10 4",
    }
)

TECH_REJECT_PHRASES: frozenset[str] = frozenset(
    {
        "pass",
        "passing",
        "no",
        "nope",
        "nah",
        "cant",
        "can't",
        "cannot",
        "cant take",
        "can't take",
        "cannot take",
        "cant take it",
        "cant do it",
        "not me",
        "skip",
    }
)

_NORMALIZED_TECH_ACCEPT: frozenset[str] = frozenset(_normalize(p) for p in TECH_ACCEPT_PHRASES)
_NORMALIZED_TECH_REJECT: frozenset[str] = frozenset(_normalize(p) for p in TECH_REJECT_PHRASES)


def is_tech_reject(body: str) -> bool:
    """True if a tech reply is a standalone decline ("pass"/"cant"/"no")."""
    return _normalize(body or "") in _NORMALIZED_TECH_REJECT


def is_tech_accept(body: str) -> bool:
    """True if a tech reply is a standalone acceptance ("ok"/"k"/…).

    Reject is checked first by callers, so a phrase can't be both.
    """
    return _normalize(body or "") in _NORMALIZED_TECH_ACCEPT
