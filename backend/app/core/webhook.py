"""Webhook signature verification for Quo / OpenPhone webhooks.

Supports two signature schemes because Quo ships both in production:

1. **Legacy OpenPhone format** — header ``openphone-signature`` with the
   value ``hmac;1;<unix-ms>;<base64-hmac-sha256>``. The signed string is
   ``"{timestamp_ms}.{raw_body}"``. The key is the raw UTF-8 bytes of
   ``OPENPHONE_WEBHOOK_SECRET``. (See ``docs/guides/2026-03-31_setup_and_next_steps.md``.)

2. **Quo beta (Svix-style) format** — headers ``webhook-signature``,
   ``webhook-timestamp``, ``webhook-id``. The signature value is a
   space-separated list of ``v1,<base64>`` entries. The signed string is
   ``"{webhook-id}.{webhook-timestamp}.{raw_body}"`` with ``webhook-timestamp``
   in **Unix seconds**. The key is the **base64-decoded bytes** of the
   secret (after stripping the ``whsec_`` prefix). Spec:
   https://www.quo.com/docs/mdx/beta/webhooks-signature-validation.md

The route handler calls :func:`verify_webhook_signature`, which tries the
beta scheme first (more specific header set) and falls back to legacy.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from collections.abc import Iterable

logger = logging.getLogger(__name__)

# === Legacy OpenPhone ===

LEGACY_SIGNATURE_HEADER = "openphone-signature"
LEGACY_PREFIX = "hmac"
LEGACY_VERSION = "1"
LEGACY_MAX_AGE_MS = 5 * 60 * 1000  # 5 minutes

# === Quo beta (Svix-style) ===

BETA_SIGNATURE_HEADER = "webhook-signature"
BETA_TIMESTAMP_HEADER = "webhook-timestamp"
BETA_ID_HEADER = "webhook-id"
BETA_VERSION = "v1"
BETA_MAX_AGE_SECONDS = 5 * 60  # 5 minutes
BETA_SECRET_PREFIX = "whsec_"


def _coerce_candidates_legacy(signature: str) -> tuple[int, str] | None:
    """Parse ``"hmac;1;<ts>;<b64>"`` → ``(timestamp_ms, base64_sig)`` or None."""
    parts = signature.split(";")
    if len(parts) != 4 or parts[0] != LEGACY_PREFIX or parts[1] != LEGACY_VERSION:
        return None
    try:
        ts_ms = int(parts[2])
    except ValueError:
        return None
    return ts_ms, parts[3]


def _coerce_candidates_beta(
    signature_header: str,
) -> list[str]:
    """Parse ``"v1,<b64> v1,<b64>"`` → list of base64 sig strings."""
    out: list[str] = []
    for entry in signature_header.split(" "):
        entry = entry.strip()
        if not entry or "," not in entry:
            continue
        version, value = entry.split(",", 1)
        if version == BETA_VERSION and value:
            out.append(value)
    return out


def _decode_beta_key(secret: str) -> bytes:
    """Return the HMAC key bytes for the Quo beta spec.

    Strips the ``whsec_`` prefix if present, then base64-decodes the rest.
    Falls back to UTF-8 bytes if the secret is not valid base64 so we
    don't crash on misconfigured secrets — verification will simply fail.
    """
    raw = secret[len(BETA_SECRET_PREFIX) :] if secret.startswith(BETA_SECRET_PREFIX) else secret
    try:
        return base64.b64decode(raw, validate=True)
    except (ValueError, TypeError):
        # Misconfigured — surface the failure via signature mismatch, not a crash.
        return raw.encode("utf-8")


def _candidate_legacy_keys(secret: str) -> list[bytes]:
    """Return the HMAC key bytes to try for the legacy OpenPhone format.

    OpenPhone's spec for the legacy format is undocumented, but the values
    in the wild look like base64 strings (e.g.
    ``NXg0ZnpoZUZ6ZXVibzJMWFBvckJvMmFBa1Z0TjN6Mmg=``). To stay robust we try
    both interpretations of the configured secret:

    1. The raw UTF-8 bytes of ``secret`` (most providers — Stripe, GitHub,
       Slack, etc. — use raw keys).
    2. The base64-decoded bytes of ``secret`` (matches the new Quo beta
       Svix-style convention; also fits when a value was double-wrapped).

    Returns both candidates (deduped) so callers can try each in turn.
    """
    candidates: list[bytes] = []
    raw = secret.encode("utf-8")
    candidates.append(raw)
    stripped = secret.strip()
    if stripped and len(stripped) % 4 == 0:
        try:
            decoded = base64.b64decode(stripped, validate=True)
            if decoded != raw:
                candidates.append(decoded)
        except (ValueError, TypeError):
            pass
    return candidates


def _verify_legacy_openphone(payload: bytes, signature_header: str | None, secret: str) -> bool:
    """Verify the legacy ``openphone-signature`` header."""
    if not signature_header:
        return False
    parsed = _coerce_candidates_legacy(signature_header)
    if parsed is None:
        logger.warning(
            "Legacy webhook signature rejected: malformed header (got %r)",
            signature_header[:32],
        )
        return False
    ts_ms, received_sig_b64 = parsed

    now_ms = int(time.time() * 1000)
    if abs(now_ms - ts_ms) > LEGACY_MAX_AGE_MS:
        logger.warning(
            "Legacy webhook rejected: timestamp outside tolerance (skew_ms=%d)",
            abs(now_ms - ts_ms),
        )
        return False

    signing = f"{ts_ms}.".encode() + payload
    for key_bytes in _candidate_legacy_keys(secret):
        expected_sig_b64 = base64.b64encode(
            hmac.new(key_bytes, signing, hashlib.sha256).digest()
        ).decode("ascii")
        if hmac.compare_digest(expected_sig_b64, received_sig_b64):
            logger.info(
                "Legacy webhook signature verified OK (payload_bytes=%d key_encoding=%s)",
                len(payload),
                "raw" if key_bytes == secret.encode("utf-8") else "base64-decoded",
            )
            return True

    # Log the first candidate's expected prefix so operators can compare
    # against what the dashboard claims the key is.
    first_expected = base64.b64encode(
        hmac.new(secret.encode("utf-8"), signing, hashlib.sha256).digest()
    ).decode("ascii")
    logger.warning(
        "Legacy webhook signature mismatch: expected=%s... received=%s... payload_bytes=%d",
        first_expected[:12],
        received_sig_b64[:12],
        len(payload),
    )
    return False


def _verify_quo_beta(
    payload: bytes,
    signature_header: str | None,
    timestamp_header: str | None,
    id_header: str | None,
    secret: str,
) -> bool:
    """Verify the Quo beta (Svix-style) headers."""
    if not signature_header or not timestamp_header:
        return False
    try:
        ts_s = int(timestamp_header)
    except ValueError:
        logger.warning("Beta webhook rejected: non-integer webhook-timestamp header")
        return False

    now_s = int(time.time())
    if abs(now_s - ts_s) > BETA_MAX_AGE_SECONDS:
        logger.warning(
            "Beta webhook rejected: timestamp outside tolerance (skew_s=%d)",
            abs(now_s - ts_s),
        )
        return False

    candidates = _coerce_candidates_beta(signature_header)
    if not candidates:
        logger.warning("Beta webhook rejected: no v1,<sig> entries in signature header")
        return False

    wid = id_header or ""
    key_bytes = _decode_beta_key(secret)
    signing = f"{wid}.{ts_s}.".encode() + payload
    expected_b64 = base64.b64encode(hmac.new(key_bytes, signing, hashlib.sha256).digest()).decode(
        "ascii"
    )
    expected_bytes = expected_b64.encode("ascii")

    for candidate in candidates:
        candidate_bytes = candidate.encode("ascii")
        if len(candidate_bytes) != len(expected_bytes):
            continue
        if hmac.compare_digest(expected_bytes, candidate_bytes):
            logger.info("Beta webhook signature verified OK (payload_bytes=%d)", len(payload))
            return True

    logger.warning(
        "Beta webhook signature mismatch: expected=%s... payload_bytes=%d",
        expected_b64[:12],
        len(payload),
    )
    return False


def verify_webhook_signature(
    payload: bytes,
    *,
    openphone_signature: str | None,
    webhook_signature: str | None,
    webhook_timestamp: str | None,
    webhook_id: str | None,
    secret: str,
) -> bool:
    """Verify a Quo / OpenPhone webhook payload against the configured secret.

    Tries the Quo beta (Svix-style) scheme first when its headers are
    present; otherwise falls back to the legacy ``openphone-signature``
    scheme. Returns False (never raises) on any mismatch, missing header,
    or replay-window violation.

    If ``secret`` is empty and the current environment is ``local`` or
    ``development``, verification is skipped (returns True) so local
    smoke tests work without Quo access. In any other environment an
    empty secret always returns False.
    """
    if not secret:
        from app.core.config import settings  # local import to avoid cycles at module load

        if settings.ENVIRONMENT in ("local", "development"):
            logger.warning(
                "Webhook verification skipped: no OPENPHONE_WEBHOOK_SECRET configured (allowed in %s)",
                settings.ENVIRONMENT,
            )
            return True
        logger.error(
            "Webhook rejected: no OPENPHONE_WEBHOOK_SECRET configured in %s",
            settings.ENVIRONMENT,
        )
        return False

    # Prefer the beta spec when its headers are present.
    if webhook_signature or webhook_timestamp:
        return _verify_quo_beta(
            payload,
            webhook_signature,
            webhook_timestamp,
            webhook_id,
            secret,
        )

    # Fall back to the legacy OpenPhone header.
    return _verify_legacy_openphone(payload, openphone_signature, secret)


# === Backwards-compatible alias ===

# Older callers (and the legacy `app/api/routes/v1/openphone.py` import
# path) use the original name. Keep it working with the legacy semantics
# so existing call sites don't need to change yet.
def verify_openphone_signature(
    payload: bytes,
    signature: str | None,
    secret: str,
) -> bool:
    """Backwards-compatible entry point for the legacy OpenPhone header.

    New code should call :func:`verify_webhook_signature` instead.
    """
    return _verify_legacy_openphone(payload, signature, secret)


def _iter_headers(headers) -> Iterable[tuple[str, str | None]]:
    """Normalise header access across Starlette Request and plain dicts."""
    if hasattr(headers, "get"):
        yield LEGACY_SIGNATURE_HEADER, headers.get(LEGACY_SIGNATURE_HEADER)
        yield BETA_SIGNATURE_HEADER, headers.get(BETA_SIGNATURE_HEADER)
        yield BETA_TIMESTAMP_HEADER, headers.get(BETA_TIMESTAMP_HEADER)
        yield BETA_ID_HEADER, headers.get(BETA_ID_HEADER)
    else:  # plain dict
        for k, v in headers.items():
            lk = k.lower()
            if lk == LEGACY_SIGNATURE_HEADER:
                yield LEGACY_SIGNATURE_HEADER, v
            elif lk == BETA_SIGNATURE_HEADER:
                yield BETA_SIGNATURE_HEADER, v
            elif lk == BETA_TIMESTAMP_HEADER:
                yield BETA_TIMESTAMP_HEADER, v
            elif lk == BETA_ID_HEADER:
                yield BETA_ID_HEADER, v


def verify_webhook_from_headers(
    payload: bytes,
    headers,
    secret: str,
) -> bool:
    """Convenience wrapper: extract headers from a Starlette ``Request`` (or dict)
    and call :func:`verify_webhook_signature`.
    """
    h = dict(_iter_headers(headers))
    return verify_webhook_signature(
        payload,
        openphone_signature=h.get(LEGACY_SIGNATURE_HEADER),
        webhook_signature=h.get(BETA_SIGNATURE_HEADER),
        webhook_timestamp=h.get(BETA_TIMESTAMP_HEADER),
        webhook_id=h.get(BETA_ID_HEADER),
        secret=secret,
    )
