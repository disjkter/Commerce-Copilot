import pytest

from commerce_copilot.security import (
    InvalidTokenError,
    create_access_token,
    decode_access_token,
    hash_password,
    verify_password,
)


def test_password_hash_is_salted_and_verifiable():
    first = hash_password("correct horse battery staple")
    second = hash_password("correct horse battery staple")

    assert first != second
    assert verify_password("correct horse battery staple", first) is True
    assert verify_password("wrong password", first) is False


def test_access_token_rejects_tampering_and_expiration():
    token = create_access_token("user-1", "test-secret", expires_minutes=2, now=100)

    assert decode_access_token(token, "test-secret", now=150) == "user-1"
    with pytest.raises(InvalidTokenError):
        decode_access_token(token + "broken", "test-secret", now=150)
    with pytest.raises(InvalidTokenError):
        decode_access_token(token, "test-secret", now=220)
