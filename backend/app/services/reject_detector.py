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
    re.compile(r"\bno\s+one\b", re.IGNORECASE),          # "no one for now", "no one available"
    re.compile(r"\bnobody\s+available\b", re.IGNORECASE),
    re.compile(r"\bno\s+techs?\s+available\b", re.IGNORECASE),
    re.compile(r"\bno\s+one\s+available\b", re.IGNORECASE),
    re.compile(r"\bnot\s+available\b", re.IGNORECASE),
    re.compile(r"\bsorry\b.{0,40}\bno\b", re.IGNORECASE),   # "sorry, we have no..."
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
    """
    job_norm = _normalize(job_body)
    reply_norm = _normalize(body)
    if len(job_norm) < _REPASTE_MIN_JOB_CHARS or not reply_norm:
        return False
    # A bare re-paste with no added note is not a rejection — the operator
    # must have appended *something* (the decline note).
    if reply_norm == job_norm:
        return False

    if job_norm in reply_norm:
        extra = len(reply_norm) - len(job_norm)
        return 0 < extra <= _REPASTE_NOTE_MAX_CHARS

    if len(reply_norm) >= len(job_norm):
        ratio = SequenceMatcher(None, job_norm, reply_norm).ratio()
        return ratio >= _REPASTE_SIMILARITY_THRESHOLD
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
