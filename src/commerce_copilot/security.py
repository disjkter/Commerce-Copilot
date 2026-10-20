import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time


PASSWORD_ITERATIONS = 310_000
TOKEN_ALGORITHM = "HS256"
TOKEN_ISSUER = "commerce-copilot"


class InvalidTokenError(ValueError):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS,
    )
    return "$".join(
        (
            "pbkdf2_sha256",
            str(PASSWORD_ITERATIONS),
            _base64url_encode(salt),
            _base64url_encode(digest),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_text, digest_text = encoded.split("$", maxsplit=3)
        if scheme != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            _base64url_decode(salt_text),
            int(iterations),
        )
        expected = _base64url_decode(digest_text)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def create_access_token(
    user_id: str,
    secret: str,
    *,
    expires_minutes: int,
    now: int | None = None,
) -> str:
    issued_at = int(time.time()) if now is None else now
    header = {"alg": TOKEN_ALGORITHM, "typ": "JWT"}
    payload = {
        "sub": user_id,
        "iss": TOKEN_ISSUER,
        "iat": issued_at,
        "exp": issued_at + expires_minutes * 60,
    }
    encoded_header = _encode_json(header)
    encoded_payload = _encode_json(payload)
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_base64url_encode(signature)}"


def decode_access_token(
    token: str,
    secret: str,
    *,
    now: int | None = None,
) -> str:
    try:
        encoded_header, encoded_payload, encoded_signature = token.split(".")
        header = json.loads(_base64url_decode(encoded_header))
        payload = json.loads(_base64url_decode(encoded_payload))
        signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
        expected_signature = hmac.new(
            secret.encode("utf-8"),
            signing_input,
            hashlib.sha256,
        ).digest()
        signature = _base64url_decode(encoded_signature)
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError, binascii.Error) as exc:
        raise InvalidTokenError("malformed token") from exc

    if header != {"alg": TOKEN_ALGORITHM, "typ": "JWT"}:
        raise InvalidTokenError("unsupported token header")
    if not hmac.compare_digest(signature, expected_signature):
        raise InvalidTokenError("invalid token signature")
    current_time = int(time.time()) if now is None else now
    expires_at = payload.get("exp")
    if (
        payload.get("iss") != TOKEN_ISSUER
        or not isinstance(expires_at, int)
        or current_time >= expires_at
    ):
        raise InvalidTokenError("expired or invalid token")
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise InvalidTokenError("token subject is missing")
    return user_id


def _encode_json(value: dict[str, object]) -> str:
    raw = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _base64url_encode(raw)


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)
