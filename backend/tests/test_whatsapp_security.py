"""Pure security tests for the WhatsApp service-account flow.

These tests don't require the FastAPI app or DB — they test the JWT
helpers and the bcrypt-based API key hashing directly.
"""

import secrets
from datetime import timedelta

from app.core.security import (
    create_access_token,
    create_refresh_token,
    get_password_hash,
    hash_api_key,
    verify_api_key,
    verify_password,
    verify_token,
)


def test_hash_and_verify_round_trip():
    plaintext = "sk_live_" + secrets.token_hex(16)
    hashed = hash_api_key(plaintext)
    assert hashed != plaintext
    assert hashed.startswith("$2")
    assert verify_api_key(plaintext, hashed) is True


def test_verify_wrong_key_returns_false():
    plaintext = "sk_live_" + secrets.token_hex(16)
    hashed = hash_api_key(plaintext)
    assert verify_api_key("sk_live_wrong_key_wrong_key_", hashed) is False


def test_access_token_carries_extra_claims():
    token = create_access_token(
        "user-id-here",
        extra_claims={"svc": True, "svc_name": "Test"},
    )
    payload = verify_token(token)
    assert payload is not None
    assert payload["svc"] is True
    assert payload["svc_name"] == "Test"
    assert payload["type"] == "access"
    assert payload["sub"] == "user-id-here"


def test_refresh_token_carries_extra_claims():
    token = create_refresh_token(
        "user-id-here",
        extra_claims={"svc": True},
        expires_delta=timedelta(days=7),
    )
    payload = verify_token(token)
    assert payload is not None
    assert payload["svc"] is True
    assert payload["type"] == "refresh"


def test_no_extra_claims_omits_svc():
    token = create_access_token("user-id-here")
    payload = verify_token(token)
    assert payload is not None
    assert "svc" not in payload


def test_existing_password_helpers_still_work():
    # Sanity check that the new hash_api_key didn't break the existing helpers.
    h = get_password_hash("hunter2hunter2")
    assert verify_password("hunter2hunter2", h) is True
    assert verify_password("wrong", h) is False
