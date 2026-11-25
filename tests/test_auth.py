"""密码策略与 token 版本单测。"""
import json
import time

from core.auth import (
    _b64e,
    create_session_token,
    validate_password,
    verify_session_token,
)

SECRET = "test-secret"


def test_strong_password_accepted():
    assert validate_password("Kx7-rain-Forest", "alice") is None


def test_short_password_rejected():
    reason = validate_password("Ab3-x2Yz", "alice")  # 8 位
    assert reason is not None
    assert "10" in reason


def test_common_password_rejected():
    assert validate_password("password123", "alice") is not None
    assert validate_password("1234567890", "alice") is not None


def test_password_equal_to_username_rejected():
    assert validate_password("alicealice", "alice") is not None


def test_password_containing_username_rejected():
    assert validate_password("alice-Kx7-rain", "alice") is not None


def test_project_name_rejected():
    assert validate_password("zhida", "alice") is not None


def test_token_carries_version():
    token = create_session_token("alice", SECRET, 60, 3)

    payload = verify_session_token(token, SECRET)

    assert payload["u"] == "alice"
    assert payload["v"] == 3


def test_token_without_version_is_invalid():
    payload = {"u": "alice", "exp": int(time.time()) + 60}
    body = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    import hashlib
    import hmac as hmac_mod
    sig = _b64e(hmac_mod.new(SECRET.encode(), body.encode(), hashlib.sha256).digest())

    assert verify_session_token(f"{body}.{sig}", SECRET) is None


def test_expired_token_is_invalid():
    token = create_session_token("alice", SECRET, -1, 1)

    assert verify_session_token(token, SECRET) is None


def test_tampered_token_is_invalid():
    token = create_session_token("alice", SECRET, 60, 1)
    body, sig = token.split(".", 1)
    forged = _b64e(json.dumps({"u": "bob", "v": 1, "exp": int(time.time()) + 60}).encode())

    assert verify_session_token(f"{forged}.{sig}", SECRET) is None


def test_wrong_secret_is_invalid():
    token = create_session_token("alice", SECRET, 60, 1)

    assert verify_session_token(token, "other-secret") is None
