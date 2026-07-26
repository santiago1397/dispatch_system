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
    r"|\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b"
    # Informal time-window scheduling with no am/pm suffix ("CX is booked
    # between 3 to 5", "available 3-5", "free between 2 and 4") — a
    # customer confirming a time window is a schedule update, not a
    # decline. Regression: "Co: Always 24/7 / PDL: FZUHZ" / 801 Forest
    # Ave, Wilmette IL job was marked `rejected` off "CX is booked between
    # 3 to 5" — no am/pm meant the original regex missed it.
    r"|\b(?:booked|available|free)\s+between\s+\d{1,2}\s*(?:to|and|-)\s*\d{1,2}\b",
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

# A re-paste's appended note reporting a customer-contact attempt still in
# progress ("stvm left vm and text", "left voicemail", "sent a text") is NOT
# a decline — the operator is still working the job, just hasn't reached the
# customer yet. Distinguished from ``_CUSTOMER_UNAVAILABLE_NOTE_RE`` (which
# reports a completed no-contact outcome like "no answer") because a bare
# contact attempt doesn't mean the customer is confirmed unreachable — it's
# progress, not a decline or a cancel reason.
# Regression: "Co: Always 24/7 / PDL: Q25ML" / 151 Elizabeth Ct, Wood Dale
# job was marked ``rejected`` off a re-paste whose only added text was "stvm
# left vm and text", followed by a separate message asking to "set appt 2-3".
_CONTACT_ATTEMPT_NOTE_RE = re.compile(
    r"\bstvm\b"
    r"|\bleft\s+(?:a\s+)?(?:vm|voicemail|msg|message)\b"
    r"|\b(?:vm|voicemail)\s+and\s+text\b"
    r"|\btext\s+and\s+(?:vm|voicemail)\b"
    r"|\bsent\s+(?:a\s+)?text\b"
    # Bare shorthand ("vm x2", "cx na") — the same "still trying to reach
    # the customer" signal without "left"/"sent" attached. "na" here is
    # this shop's shorthand for "no answer", not the unrelated English
    # word, so a bare-word match is safe.
    r"|\bvm\b"
    r"|\bna\b",
    re.IGNORECASE,
)

# A re-paste's appended note reporting an estimate/quote is being prepared
# or was already sent ("we are sending estimate, will keep you posted",
# "3 quotes were sent", "quoted 230-290") is NOT a decline — the job is
# actively being worked, just waiting on pricing back-and-forth.
# Regression: "Co: Always 24/7 / PDL: 1DAAR" / 2 Salt Creek Ln, Hinsdale IL
# job was marked ``rejected`` off a re-paste whose only added text was "cx
# has 40 locks, we are sending estimate, will keep you posted" — a later
# message in the same thread confirmed the estimate was in fact sent.
_ESTIMATE_PENDING_NOTE_RE = re.compile(
    r"\bestimate(?:s)?\b|\bquote(?:s|d)?\b|\bkeep\s+you\s+posted\b|\bkeep\s+posted\b"
    # Price negotiation ("wants to know the price", "thought it was too
    # expensive", "wants a better price") is the same "pricing
    # back-and-forth, job still open" bucket as an estimate — not a
    # decline. Regression: "Co: Always 24/7 / PDL: GFU7N" / 4601 W Touhy
    # Ave, Lincolnwood IL job was marked `rejected` off "He just wants to
    # know the price. I told him Rekey was $75, and he thought it was too
    # expensive."
    r"|\btoo\s+expensive\b|\bwants?\s+(?:to\s+know\s+)?(?:the\s+|a\s+)?(?:better\s+)?price\b"
    r"|\bprice\s+(?:is\s+)?too\s+high\b",
    re.IGNORECASE,
)

# A re-paste's appended note reporting the customer will call back (rather
# than us trying to reach them — see :data:`_CONTACT_ATTEMPT_NOTE_RE` for
# that direction) is NOT a decline — it's an open, pending job. Regression:
# "Co: Always 24/7 / PDL: VT6KY" / 619 S Cook St, Barrington IL job was
# marked `rejected` off "Cx said will cb, will ask her parents first".
_CALLBACK_PROMISE_NOTE_RE = re.compile(
    r"\bwill\s+(?:cb|call\s*back|callback)\b|\bsaid\s+(?:she|he|they)?\s*(?:will|'ll)\s+call\b",
    re.IGNORECASE,
)

# A re-paste's appended note reporting the customer is currently leaning
# toward a competitor ("cx already gave the pictures to another
# technician, he doesnt want to do a job with us", "going with someone
# else", "already talking to another company") is a SOFT decline, not a
# terminal one — customers who've "gone with another tech" for a quote
# frequently come back if that doesn't pan out. Deliberately NOT routed to
# ``is_cancel_signal`` either (unlike :data:`_CUSTOMER_UNAVAILABLE_NOTE_RE`,
# which is a firm "job not needed" outcome) — this note alone shouldn't
# force any terminal state; the job is left as-is (typically ``pending``)
# so it naturally resurfaces via the ``undispatched``/stuck alerts instead
# of vanishing into a status that has to be manually corrected later.
# Regression: "Co: Always 24/7 / PDL: P12BB" / 1970 University Ln, Lisle IL
# job was marked ``rejected`` off "cx already gave the pictures to another
# technician, he doesnt want to do a job with us" — the same PDL resurfaced
# the next day and the operator ended up sending 3 quotes for it.
_COMPETITOR_SOFT_DECLINE_NOTE_RE = re.compile(
    r"\b(?:gave|sent)\s+(?:the\s+)?(?:pictures|photos|info)\s+to\s+another\b"
    r"|\bdoesn'?t\s+want\s+to\s+(?:do\s+a\s+job|work)\s+with\s+us\b"
    r"|\b(?:going|went)\s+with\s+(?:someone|another)\b"
    r"|\balready\s+(?:talking|working)\s+(?:to|with)\s+another\b"
    r"|\bchose\s+(?:someone|another)\s+(?:else|company|technician)\b"
    r"|\bwent\s+with\s+another\s+(?:company|technician|tech)\b",
    re.IGNORECASE,
)

# A re-paste's appended note reporting the customer wasn't there when the
# tech arrived ("she's not there anymore", "no one home", "cx gone"), or
# that the job is no longer needed because someone else already handled it
# ("has someone on site already", "already got someone", "already fixed"),
# is a CANCELLATION reason, not a plain decline — the job was accepted and
# worked (or would have been), but doesn't need to be done by this company.
# Distinguished from ``REJECT_PHRASES`` (which mean "we are not taking this
# job at all") so it can route to the ``canceled`` terminal status instead
# of ``rejected``. See :func:`is_cancel_signal`.
# Regression: "Co: Always 24/7 / PDL: R628Z" / 117 E 163rd St, South
# Holland IL job was marked `rejected` off a re-paste whose only added text
# was "she said has someone on site already" — the customer already had
# another vendor there, not the operator declining the job.
_CUSTOMER_UNAVAILABLE_NOTE_RE = re.compile(
    r"\bnot\s+there\s+anymore\b"
    r"|\b(?:isn'?t|is\s+not|wasn'?t|was\s+not)\s+(?:there|home)\b"
    r"|\bno\s+(?:one|body)\s+(?:home|there|answer(?:ed|ing)?)\b"
    r"|\bnobody\s+(?:home|there)\b"
    r"|\bno\s+answer\b"
    r"|\b(?:customer|cx|client)\s+(?:gone|left|not\s+(?:home|there))\b"
    r"|\balready\s+left\b"
    r"|\b(?:has|have|got|has\s+got)\s+someone(?:\s+(?:on\s*site|there|already))?\b"
    r"|\bsomeone\s+(?:on\s*site|already\s+there|already\s+came|already\s+fixed(?:\s+it)?)\b"
    r"|\balready\s+(?:has|have|got)\s+(?:someone|help|a\s+tech)\b"
    r"|\balready\s+(?:fixed|handled|resolved|taken\s+care\s+of)\b"
    r"|\bno\s+longer\s+need(?:s|ed)?\b"
    r"|\bdon'?t\s+need\b|\bnot\s+needed\b"
    r"|\bdns\b",
    re.IGNORECASE,
)

# "DNS" ("does not need service") is this dispatch operation's own
# shorthand for "customer no longer needs the job" — used constantly, both
# as a bare standalone reply ("dns", "60108 saying DNS") and embedded in a
# re-paste/note ("Cx informing DNS", "confirm dns", "DNS any more please
# check"). Unlike :data:`_CUSTOMER_UNAVAILABLE_NOTE_RE`'s repaste-note path
# (which requires the reply to contain or closely resemble the job body),
# a bare "dns" is too short to pass that containment/similarity check, so
# it needs its own unconditional signal — see :func:`is_cancel_signal`.
# Regression: "Co: Always 24/7 / PDL: B9YRG" / 12161 S Central Ave, Alsip
# IL job was left `pending` (not even `rejected`) because the "Cx informing
# DNS" reply arrived before the job had been classified into the system,
# so the repaste/reject-candidate matching never saw it at all. The
# customer later confirmed directly ("Do you still need service?" —
# "No I'm good thanks"), so this really is a cancellation.
_DNS_RE = re.compile(r"\bdns\b", re.IGNORECASE)

# A re-paste's appended note reporting that the customer never answered the
# operator OR the dispatched technician ("cx not answering to me or to new
# technician I left vm", "Cx never answered to us or tech to set appt",
# "called and texted no answer") is a CANCELLATION outcome, not a plain
# contact attempt: a tech was already involved and the customer could not be
# reached, so the job will not be done.
#
# This is deliberately narrower than "the operator mentioned a voicemail".
# ``_CONTACT_ATTEMPT_NOTE_RE`` above matches a *bare* attempt ("stvm left vm
# and text") and vetoes the reject path because the operator is still
# working the job. The difference that matters is whether the note reports
# an OUTCOME ("not answering", "never answered", "no pick up") or merely an
# ACTION ("left vm"). Only the outcome wording routes to ``canceled``; a
# bare action still falls through to the ``needs_follow_up`` relay path.
#
# Regression: "Co: Always 24/7 / PDL: PY3YA" / 6946 N Overhill Ave, Chicago
# IL sat at ``pending`` for six days after the operator re-pasted the job
# with "cx not answering to me or to new technician I left vm". The note
# matched ``_CONTACT_ATTEMPT_NOTE_RE`` (on "left vm"), which vetoed the
# reject path, and matched no cancel wording at all — "not answering" was
# absent from ``_CUSTOMER_UNAVAILABLE_NOTE_RE``, which only covered "no
# answer" / "no one answering".
_NO_ANSWER_OUTCOME_RE = re.compile(
    r"\b(?:not|isn'?t|aren'?t|ain'?t)\s+answer(?:ing|ed)?\b"
    r"|\b(?:never|didn'?t|did\s+not|doesn'?t|does\s+not|won'?t|wont)\s+answer(?:ing|ed)?\b"
    r"|\bno\s+answer\b"
    r"|\bunanswered\b"
    r"|\b(?:not|isn'?t|never|didn'?t|did\s+not|doesn'?t)\s+pick(?:ing|ed)?\s*(?:up|the\s+phone)\b"
    r"|\bno\s+pick\s*up\b"
    r"|\b(?:not|isn'?t|never|didn'?t|did\s+not)\s+respond(?:ing|ed)?\b"
    r"|\bno\s+response\b"
    r"|\bunresponsive\b"
    r"|\b(?:cant|can'?t|cannot|unable\s+to)\s+reach\b"
    r"|\bcouldn'?t\s+reach\b",
    re.IGNORECASE,
)

# Tentative markers that turn a no-answer OUTCOME back into an in-progress
# attempt: "no answer yet", "not answering for now", "still trying", "will
# keep trying", "will try again". The operator is explicitly signalling the
# job is still live, so it must stay non-terminal (the ``needs_follow_up``
# relay path handles it) rather than being canceled.
#
# Observed in production alongside the genuine cancels: "not answering for
# now" and "no answer yet" are both still-working updates, whereas "Cx never
# answered to us or tech to set appt" is a settled outcome.
_TENTATIVE_CONTACT_RE = re.compile(
    r"\byet\b"
    r"|\bfor\s+now\b"
    r"|\bso\s+far\b"
    r"|\bstill\s+(?:trying|calling|waiting|working)\b"
    r"|\b(?:will|gonna|going\s+to|ill|i'?ll)\s+(?:keep|try|call|text|reach)\b"
    r"|\bkeep\s+(?:trying|calling|you\s+posted)\b"
    r"|\btry(?:ing)?\s+again\b"
    r"|\bwaiting\s+(?:on|for)\b",
    re.IGNORECASE,
)


# The subset of :data:`_CUSTOMER_UNAVAILABLE_NOTE_RE` that reports a settled
# physical fact rather than a failed phone contact — the tech found nobody
# there, another vendor already handled it, or the customer said DNS. A
# tentative marker cannot soften these ("already fixed, will keep you
# posted" is still a cancellation), so they bypass the
# :data:`_TENTATIVE_CONTACT_RE` veto that applies to answer-related wording.
_SETTLED_UNAVAILABLE_RE = re.compile(
    r"\bnot\s+there\s+anymore\b"
    r"|\b(?:isn'?t|is\s+not|wasn'?t|was\s+not)\s+(?:there|home)\b"
    r"|\bno\s+(?:one|body)\s+(?:home|there)\b"
    r"|\bnobody\s+(?:home|there)\b"
    r"|\b(?:customer|cx|client)\s+(?:gone|left|not\s+(?:home|there))\b"
    r"|\balready\s+left\b"
    r"|\b(?:has|have|got|has\s+got)\s+someone(?:\s+(?:on\s*site|there|already))?\b"
    r"|\bsomeone\s+(?:on\s*site|already\s+there|already\s+came|already\s+fixed(?:\s+it)?)\b"
    r"|\balready\s+(?:has|have|got)\s+(?:someone|help|a\s+tech)\b"
    r"|\balready\s+(?:fixed|handled|resolved|taken\s+care\s+of)\b"
    r"|\bno\s+longer\s+need(?:s|ed)?\b"
    r"|\bdon'?t\s+need\b|\bnot\s+needed\b"
    r"|\bdns\b",
    re.IGNORECASE,
)


def _looks_like_no_answer_outcome(body: str) -> bool:
    """True if ``body`` reports a settled "customer never answered" outcome.

    Returns ``False`` when the note carries a tentative marker ("no answer
    yet", "not answering for now", "still trying") — those mean the operator
    is still chasing the customer, which is a ``needs_follow_up`` update, not
    a cancellation. See :data:`_NO_ANSWER_OUTCOME_RE` /
    :data:`_TENTATIVE_CONTACT_RE`.

    Also returns ``False`` when the note is asking the broker something
    ("No answer, check please", "Appt 9am pls check cx not answering try
    confirm appt") — an open question means the operator is still working
    the job, and one of those observed notes is about an appointment that
    exists. Reuses the reject path's :func:`_looks_like_data_question` veto
    rather than a second pattern. Note that "never answered ... to set appt"
    is *not* vetoed: it mentions an appointment but asks nothing, and
    reports that the appointment was never made.
    """
    text = body or ""
    if not _NO_ANSWER_OUTCOME_RE.search(text):
        return False
    if _TENTATIVE_CONTACT_RE.search(text):
        return False
    return not _looks_like_data_question(text)


def _looks_like_cancel_note(body: str) -> bool:
    """True if a re-paste's appended note reports a cancellation outcome.

    Combines the two families:
    - a settled customer-unavailable fact (:data:`_SETTLED_UNAVAILABLE_RE`),
      which a tentative marker cannot soften; and
    - a failed-contact outcome (:func:`_looks_like_no_answer_outcome`),
      which a tentative marker *does* soften back to an in-progress attempt.

    ``_CUSTOMER_UNAVAILABLE_NOTE_RE`` also carries bare answer-related
    alternatives ("no answer", "no one answering"). Those are routed through
    the tentative veto here so "no answer yet" stays a ``needs_follow_up``
    update instead of canceling the job.
    """
    text = body or ""
    if _SETTLED_UNAVAILABLE_RE.search(text):
        return True
    # Everything left in _CUSTOMER_UNAVAILABLE_NOTE_RE is answer-related
    # ("no answer", "no one answering"), so it goes through the same
    # outcome gate — tentative wording and open questions both veto it.
    if _looks_like_customer_unavailable_note(text) and not _NO_ANSWER_OUTCOME_RE.search(text):
        return not (_TENTATIVE_CONTACT_RE.search(text) or _looks_like_data_question(text))
    return _looks_like_no_answer_outcome(text)


def is_dns_signal(body: str) -> bool:
    """True if ``body`` contains this shop's "DNS" (does-not-need-service)
    shorthand, regardless of whether it's a bare reply or a re-paste note
    (see :data:`_DNS_RE`)."""
    return bool(_DNS_RE.search(body or ""))


def _looks_like_en_route_note(body: str) -> bool:
    """True if ``body`` reads as the tech heading to the job, not a decline."""
    return bool(_EN_ROUTE_NOTE_RE.search(body))


def _looks_like_contact_attempt_note(body: str) -> bool:
    """True if ``body`` reads as an in-progress customer-contact attempt
    (voicemail/text) rather than a decline (see
    :data:`_CONTACT_ATTEMPT_NOTE_RE`)."""
    return bool(_CONTACT_ATTEMPT_NOTE_RE.search(body))


def _looks_like_estimate_pending_note(body: str) -> bool:
    """True if ``body`` reads as an estimate/quote in progress rather than
    a decline (see :data:`_ESTIMATE_PENDING_NOTE_RE`)."""
    return bool(_ESTIMATE_PENDING_NOTE_RE.search(body))


def _looks_like_callback_promise_note(body: str) -> bool:
    """True if ``body`` reads as the customer promising to call back
    rather than a decline (see :data:`_CALLBACK_PROMISE_NOTE_RE`)."""
    return bool(_CALLBACK_PROMISE_NOTE_RE.search(body))


def _looks_like_competitor_soft_decline_note(body: str) -> bool:
    """True if ``body`` reads as the customer currently leaning toward a
    competitor — a soft, reversible decline rather than a terminal one
    (see :data:`_COMPETITOR_SOFT_DECLINE_NOTE_RE`)."""
    return bool(_COMPETITOR_SOFT_DECLINE_NOTE_RE.search(body))


def _looks_like_customer_unavailable_note(body: str) -> bool:
    """True if ``body`` reports the customer wasn't there — a cancel reason."""
    return bool(_CUSTOMER_UNAVAILABLE_NOTE_RE.search(body))


# Loose keyword gate for "operator is reporting a customer-contact attempt"
# — much broader than ``_CUSTOMER_UNAVAILABLE_NOTE_RE`` (which is scoped to
# the re-paste-with-note reject/cancel path). Used only to decide whether a
# standalone operator reply to a company/broker number is worth the LLM
# call in ``services/company_relay_parser.py``; the LLM makes the actual
# no_answer_follow_up/none decision, so over-matching here just costs an
# extra (cheap) model call, never a wrong lifecycle transition.
_CONTACT_ATTEMPT_KEYWORD_RE = re.compile(
    r"\bvm\b|\bvoicemail\b|\banswer(?:ed|ing)?\b|\bpick(?:s|ed|ing)?\s*up\b"
    r"|\bcall\s*back\b|\bcallback\b|\breach(?:ed|ing)?\b|\bunresponsive\b"
    r"|\bno\s+response\b|\bnot\s+responding\b|\bstill\s+trying\b|\bna\b",
    re.IGNORECASE,
)


def mentions_customer_contact_attempt(body: str) -> bool:
    """True if ``body`` reads like a customer-contact-attempt update.

    Cheap pre-filter before the LLM-based company-relay parser — see
    :data:`_CONTACT_ATTEMPT_KEYWORD_RE`.
    """
    return bool(_CONTACT_ATTEMPT_KEYWORD_RE.search(body))


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
    as an in-progress customer-contact attempt (see
    :func:`_looks_like_contact_attempt_note`) rather than an actual decline
    — an operator flagging "wrong number, pls check", reporting "Appt
    tomorrow 10:30 am", a tech closing out with "Total: 174$ cc", "On way"
    back to the source chat, or "stvm left vm and text" is not passing on
    the job.
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
            or _looks_like_no_answer_outcome(body)
            or _looks_like_contact_attempt_note(body)
            or _looks_like_estimate_pending_note(body)
            or _looks_like_competitor_soft_decline_note(body)
            or _looks_like_callback_promise_note(body)
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
    the customer wasn't there (see :data:`_CUSTOMER_UNAVAILABLE_NOTE_RE`) or
    never answered (see :func:`_looks_like_no_answer_outcome`).

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
    if not _looks_like_cancel_note(body):
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
    canceled (tech got there, but the customer wasn't there; or the
    customer confirmed "DNS", does not need service) rather than declined
    outright.

    Checked by callers *before* :func:`is_reject_signal` so a message that
    matches both (unlikely, given the disjoint wording) reads as a cancel,
    which carries more information for the operator than a bare reject.

    "DNS" is checked unconditionally (unlike the repaste-note path, which
    needs ``job_body`` and a length/similarity match) because it's used
    both as a short standalone reply ("dns") too brief to pass that
    containment check, and embedded in a re-paste note.
    """
    if not body or not body.strip():
        return False
    if is_dns_signal(body):
        return True
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
