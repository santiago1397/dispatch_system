"""Webhook signature verification for Quo (OpenPhone) webhooks."""

import base64
import hashlib
import hmac
import logging
import time

logger = logging.getLogger(__name__)

# Header name Quo (OpenPhone) uses for webhook signatures.
SIGNATURE_HEADER = "openphone-signature"

# Maximum clock skew (in milliseconds) tolerated between us and Quo when checking
# the signature timestamp. Webhooks with a timestamp older or newer than this
# window are rejected as replays. Quo already enforces this server-side; this is
# defense in depth.
SIGNATURE_TOLERANCE_MS = 5 * 60 * 1000


def verify_openphone_signature(
    payload: bytes,
    signature: str | None,
    secret: str,
) -> bool:
    """Verify the HMAC-SHA256 signature of an incoming Quo (OpenPhone) webhook payload.

    Quo's signature header format: "hmac;1;<unix-ms-timestamp>;<base64-hmac-sha256>".
    The signed string is "{timestamp}.{raw_body}" using the webhook secret as the key.

    Args:
        payload: Raw request body bytes.
        signature: Value of the openphone-signature header.
        secret: The webhook key returned by Quo when creating the webhook.

    Returns:
        True if the signature is valid and within the replay window.
        False if the signature is missing, malformed, expired, or doesn't match.
        In local/development environments, skips verification if no secret is configured.
    """
    if not secret:
        from app.core.config import settings

        if settings.ENVIRONMENT in ("local", "development"):
            logger.warning(
                "Webhook verification skipped: no OPENPHONE_WEBHOOK_SECRET configured (allowed in %s)",
                settings.ENVIRONMENT,
            )
            return True
        logger.error(
            "Webhook rejected: no OPENPHONE_WEBHOOK_SECRET configured in %s", settings.ENVIRONMENT
        )
        return False

    if not signature:
        logger.warning("Webhook rejected: no signature header in request")
        return False

    # Parse "hmac;1;<ts>;<b64-sig>"
    parts = signature.split(";")
    if len(parts) != 4 or parts[0] != "hmac" or parts[1] != "1":
        logger.warning(
            "Webhook rejected: malformed signature header (got %d parts)", len(parts)
        )
        return False

    try:
        timestamp_ms = int(parts[2])
    except ValueError:
        logger.warning("Webhook rejected: non-integer timestamp in signature header")
        return False

    received_sig_b64 = parts[3]

    # Replay protection
    now_ms = int(time.time() * 1000)
    skew_ms = abs(now_ms - timestamp_ms)
    if skew_ms > SIGNATURE_TOLERANCE_MS:
        logger.warning(
            "Webhook rejected: signature timestamp outside tolerance window (skew_ms=%d)",
            skew_ms,
        )
        return False

    # Recompute expected signature: HMAC-SHA256(secret, "{ts}.{body}") → base64
    signing_string = f"{timestamp_ms}.".encode("utf-8") + payload
    expected_sig_b64 = base64.b64encode(
        hmac.new(secret.encode("utf-8"), signing_string, hashlib.sha256).digest()
    ).decode("ascii")

    if not hmac.compare_digest(expected_sig_b64, received_sig_b64):
        logger.warning(
            "Webhook signature verification failed: expected=%s... received=%s... payload_bytes=%d",
            expected_sig_b64[:12],
            received_sig_b64[:12],
            len(payload),
        )
        return False

    logger.info("Webhook signature verified OK (payload_bytes=%d)", len(payload))
    return True