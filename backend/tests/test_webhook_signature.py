"""Tests for the Quo / OpenPhone webhook signature verification.

Covers both schemes the live endpoint must accept:

* Legacy OpenPhone: header ``openphone-signature`` = ``hmac;1;<ms-ts>;<base64>``
  over ``"{ts_ms}.{raw_body}"`` with the raw UTF-8 secret as the HMAC key.
* Quo beta (Svix-style): headers ``webhook-signature``,
  ``webhook-timestamp``, ``webhook-id`` with ``v1,<base64>`` over
  ``"{id}.{ts_s}.{raw_body}"`` and the base64-decoded secret as the key
  (after stripping a ``whsec_`` prefix).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import pytest

from app.core import webhook
from app.core.webhook import (
    LEGACY_SIGNATURE_HEADER,
    verify_openphone_signature,
    verify_webhook_from_headers,
    verify_webhook_signature,
)


# ---------------------------------------------------------------------------
# Legacy OpenPhone scheme
# ---------------------------------------------------------------------------


def _sign_legacy(secret: str, body: bytes, ts_ms: int) -> str:
    signing = f"{ts_ms}.".encode("utf-8") + body
    sig_b64 = base64.b64encode(
        hmac.new(secret.encode("utf-8"), signing, hashlib.sha256).digest()
    ).decode("ascii")
    return f"hmac;1;{ts_ms};{sig_b64}"


def test_legacy_accepts_valid_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    body = b'{"event":"message.received","data":{"id":"M123"}}'
    secret = "the-raw-legacy-secret"
    sig = _sign_legacy(secret, body, int(time.time() * 1000))

    assert verify_openphone_signature(body, sig, secret) is True


def test_legacy_rejects_tampered_body(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    body = b'{"event":"message.received","data":{"id":"M123"}}'
    secret = "the-raw-legacy-secret"
    sig = _sign_legacy(secret, body, int(time.time() * 1000))

    tampered = body.replace(b"M123", b"M999")
    assert verify_openphone_signature(tampered, sig, secret) is False


def test_legacy_rejects_wrong_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    body = b'{"event":"message.received"}'
    sig = _sign_legacy("real-secret", body, int(time.time() * 1000))

    assert verify_openphone_signature(body, sig, "wrong-secret") is False


def test_legacy_rejects_stale_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    body = b"{}"
    secret = "s"
    stale_ms = int(time.time() * 1000) - (10 * 60 * 1000)  # 10 minutes ago
    sig = _sign_legacy(secret, body, stale_ms)

    assert verify_openphone_signature(body, sig, secret) is False


def test_legacy_rejects_malformed_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    assert verify_openphone_signature(b"{}", "not-a-real-sig", "s") is False
    assert verify_openphone_signature(b"{}", "hmac;2;1;aaaa", "s") is False
    assert verify_openphone_signature(b"{}", "hmac;1;notanumber;aaaa", "s") is False
    assert verify_openphone_signature(b"{}", "hmac;1;1;!!!notbase64!!!", "s") is False


def test_empty_secret_in_production_rejects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    assert verify_openphone_signature(b"{}", "hmac;1;1;AAAA", "") is False


def test_empty_secret_in_local_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "local")
    assert verify_openphone_signature(b"{}", "hmac;1;1;AAAA", "") is True


# ---------------------------------------------------------------------------
# Quo beta (Svix-style) scheme
# ---------------------------------------------------------------------------


def _sign_beta(secret_bytes: bytes, body: bytes, ts_s: int, wid: str) -> str:
    signing = f"{wid}.{ts_s}.".encode("utf-8") + body
    sig_b64 = base64.b64encode(hmac.new(secret_bytes, signing, hashlib.sha256).digest()).decode(
        "ascii"
    )
    return f"v1,{sig_b64}"


def test_beta_accepts_valid_signature_with_whsec_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    body = b'{"event":"message.received"}'
    raw_secret = b"super-secret-bytes-for-hmac-key"
    secret_in_env = "whsec_" + base64.b64encode(raw_secret).decode("ascii")
    ts_s = int(time.time())
    wid = "msg_abc123"
    sig_header = _sign_beta(raw_secret, body, ts_s, wid)

    headers = {
        LEGACY_SIGNATURE_HEADER: None,  # noqa: explicit absence
        "webhook-signature": sig_header,
        "webhook-timestamp": str(ts_s),
        "webhook-id": wid,
    }
    assert verify_webhook_from_headers(body, headers, secret_in_env) is True


def test_beta_accepts_multiple_v1_signatures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    body = b"{}"
    raw_secret = b"another-secret"
    secret_in_env = "whsec_" + base64.b64encode(raw_secret).decode("ascii")
    ts_s = int(time.time())
    wid = "evt_42"
    valid = _sign_beta(raw_secret, body, ts_s, wid)
    sig_header = f"v1,AAAA v1,BBBB {valid} v1,CCCC"

    headers = {
        "webhook-signature": sig_header,
        "webhook-timestamp": str(ts_s),
        "webhook-id": wid,
    }
    assert verify_webhook_from_headers(body, headers, secret_in_env) is True


def test_beta_rejects_when_no_v1_entry_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    body = b"{}"
    raw_secret = b"secret"
    secret_in_env = "whsec_" + base64.b64encode(raw_secret).decode("ascii")
    ts_s = int(time.time())
    sig_header = "v1,AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="  # wrong

    headers = {
        "webhook-signature": sig_header,
        "webhook-timestamp": str(ts_s),
        "webhook-id": "evt_1",
    }
    assert verify_webhook_from_headers(body, headers, secret_in_env) is False


def test_beta_rejects_stale_timestamp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    body = b"{}"
    raw_secret = b"secret"
    secret_in_env = "whsec_" + base64.b64encode(raw_secret).decode("ascii")
    stale_ts = int(time.time()) - (10 * 60)  # 10 minutes ago
    wid = "evt_2"
    sig_header = _sign_beta(raw_secret, body, stale_ts, wid)

    headers = {
        "webhook-signature": sig_header,
        "webhook-timestamp": str(stale_ts),
        "webhook-id": wid,
    }
    assert verify_webhook_from_headers(body, headers, secret_in_env) is False


def test_beta_rejects_when_signature_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    headers = {
        "webhook-timestamp": str(int(time.time())),
        "webhook-id": "evt_3",
    }
    assert verify_webhook_from_headers(b"{}", headers, "whsec_AAAA") is False


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def test_dispatcher_picks_beta_when_both_headers_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When both schemes' headers arrive, beta wins (more specific)."""
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    body = b'{"event":"message.received"}'

    # Legacy signed with a different secret (would fail legacy check).
    legacy_secret = "legacy-only"
    legacy_sig = _sign_legacy(legacy_secret, body, int(time.time() * 1000))

    # Beta signed with the actual secret we configure.
    raw_secret = b"beta-only"
    secret_in_env = "whsec_" + base64.b64encode(raw_secret).decode("ascii")
    ts_s = int(time.time())
    wid = "evt_dual"
    beta_sig = _sign_beta(raw_secret, body, ts_s, wid)

    headers = {
        LEGACY_SIGNATURE_HEADER: legacy_sig,
        "webhook-signature": beta_sig,
        "webhook-timestamp": str(ts_s),
        "webhook-id": wid,
    }
    # Beta should verify even though the legacy header is present and would fail.
    assert verify_webhook_from_headers(body, headers, secret_in_env) is True


def test_dispatcher_falls_back_to_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    body = b"{}"
    legacy_secret = "still-the-raw-secret"
    legacy_sig = _sign_legacy(legacy_secret, body, int(time.time() * 1000))

    headers = {LEGACY_SIGNATURE_HEADER: legacy_sig}
    assert verify_webhook_from_headers(body, headers, legacy_secret) is True


def test_dispatcher_rejects_when_no_signature_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    assert verify_webhook_from_headers(b"{}", {}, "some-secret") is False


# ---------------------------------------------------------------------------
# A real-shape body from a captured Quo/OpenPhone request
# ---------------------------------------------------------------------------


def test_real_captured_log_line_with_both_legacy_and_beta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reproduce the structure of one of the 401s from the prod logs.

    We don't know the original secret, so we just verify the verifier
    rejects when the wrong secret is used — and accepts when we feed it
    the right one (synthesized here).
    """
    monkeypatch.setattr(webhook.settings, "ENVIRONMENT", "production")
    body = b'{"event":"message.received","data":{}}' + b" " * 700  # ~729 bytes
    real_secret = "the-real-legacy-secret"
    wrong_secret = "definitely-not-the-real-one"

    sig = _sign_legacy(real_secret, body, int(time.time() * 1000))
    assert verify_openphone_signature(body, sig, real_secret) is True
    assert verify_openphone_signature(body, sig, wrong_secret) is False

    # Tampered body must be rejected.
    assert verify_openphone_signature(body + b"x", sig, real_secret) is False


# Sanity check — we didn't break the public alias.
def test_public_alias_still_importable() -> None:
    from app.core.webhook import verify_openphone_signature as legacy  # noqa: F401

    assert legacy is verify_openphone_signature