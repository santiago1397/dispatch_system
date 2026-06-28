"""Webhook signature verification for Quo (OpenPhone) webhooks."""

import hashlib
import hmac
import logging

logger = logging.getLogger(__name__)

# Header name Quo uses for webhook signatures (common pattern)
SIGNATURE_HEADER = "x-openphone-signature"


def verify_openphone_signature(
    payload: bytes,
    signature: str | None,
    secret: str,
) -> bool:
    """Verify the HMAC-SHA256 signature of an incoming webhook payload.

    Args:
        payload: Raw request body bytes.
        signature: Value of the X-OpenPhone-Signature header.
        secret: The webhook key returned by Quo when creating the webhook.

    Returns:
        True if signature is valid.
        False if the signature is missing (when secret is configured) or doesn't match.
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

    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()

    if not hmac.compare_digest(expected, signature):
        logger.warning(
            "Webhook signature verification failed: expected=%s... received=%s... payload_bytes=%d",
            expected[:12],
            signature[:12],
            len(payload),
        )
        return False

    logger.info("Webhook signature verified OK (payload_bytes=%d)", len(payload))
    return True
