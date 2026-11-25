from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from datetime import UTC, datetime

PBKDF2_ITERATIONS = 310_000


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def generate_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(12)}"


def generate_token(prefix: str = "tok") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    salt_b64 = base64.urlsafe_b64encode(salt).decode("ascii")
    hash_b64 = base64.urlsafe_b64encode(derived).decode("ascii")
    return f"pbkdf2_sha256${iterations}${salt_b64}${hash_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        scheme, iter_str, salt_b64, expected_b64 = stored_hash.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iter_str)
        salt = base64.urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_b64.encode("ascii"))
    except Exception:
        return False
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(derived, expected)


def _derive_enc_mac_keys(app_secret: str) -> tuple[bytes, bytes]:
    master = app_secret.encode("utf-8")
    enc_key = hmac.new(master, b"invoiceline:enc:v1", hashlib.sha256).digest()
    mac_key = hmac.new(master, b"invoiceline:mac:v1", hashlib.sha256).digest()
    return enc_key, mac_key


def _ctr_keystream(enc_key: bytes, iv: bytes, length: int) -> bytes:
    blocks: list[bytes] = []
    counter = 0
    remaining = length
    while remaining > 0:
        block = hashlib.sha256(enc_key + iv + counter.to_bytes(8, "big")).digest()
        blocks.append(block[:remaining])
        remaining -= len(block)
        counter += 1
    return b"".join(blocks)


def encrypt_secret(plaintext: str, app_secret: str) -> str:
    """Pure-Python authenticated Encrypt-then-MAC (SHA256-CTR + HMAC-SHA256)."""
    enc_key, mac_key = _derive_enc_mac_keys(app_secret)
    iv = secrets.token_bytes(16)
    raw = plaintext.encode("utf-8")
    stream = _ctr_keystream(enc_key, iv, len(raw))
    ciphertext = bytes(a ^ b for a, b in zip(raw, stream, strict=True))
    tag = hmac.new(mac_key, iv + ciphertext, hashlib.sha256).digest()
    return "v1:" + base64.urlsafe_b64encode(iv + ciphertext + tag).decode("ascii")


def decrypt_secret(token: str, app_secret: str) -> str:
    if not token.startswith("v1:"):
        raise ValueError("Unsupported ciphertext format")
    raw = base64.urlsafe_b64decode(token[3:].encode("ascii"))
    if len(raw) < 16 + 32:
        raise ValueError("Ciphertext too short")
    iv, ciphertext, tag = raw[:16], raw[16:-32], raw[-32:]
    enc_key, mac_key = _derive_enc_mac_keys(app_secret)
    expected_tag = hmac.new(mac_key, iv + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        raise ValueError("Ciphertext integrity check failed")
    stream = _ctr_keystream(enc_key, iv, len(ciphertext))
    return bytes(a ^ b for a, b in zip(ciphertext, stream, strict=True)).decode("utf-8")


def sign_download_token(
    invoice_id: str,
    organisation_id: str,
    ttl_seconds: int,
    app_secret: str,
) -> tuple[str, int]:
    expires_at = int(time.time()) + ttl_seconds
    payload = f"{invoice_id}:{organisation_id}:{expires_at}"
    sig = hmac.new(
        app_secret.encode("utf-8"),
        f"download:{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()
    token = base64.urlsafe_b64encode(f"{payload}:{sig}".encode()).rstrip(b"=").decode("ascii")
    return token, expires_at


def verify_download_token(token: str, app_secret: str) -> tuple[str, str]:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        if base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii") != token:
            raise ValueError("Non-canonical token encoding")
        decoded = raw.decode("utf-8")
        invoice_id, organisation_id, expires_str, sig = decoded.split(":", 3)
        expires_at = int(expires_str)
    except Exception as exc:
        raise ValueError("Malformed download token") from exc

    if int(time.time()) > expires_at:
        raise ValueError("Download token expired")

    payload = f"{invoice_id}:{organisation_id}:{expires_at}"
    expected = hmac.new(
        app_secret.encode("utf-8"),
        f"download:{payload}".encode(),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("Invalid download token signature")
    return invoice_id, organisation_id


def sign_webhook_payload(secret: str, timestamp: int, payload_bytes: bytes) -> str:
    signed_content = f"{timestamp}.".encode() + payload_bytes
    sig = hmac.new(secret.encode("utf-8"), signed_content, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={sig}"
