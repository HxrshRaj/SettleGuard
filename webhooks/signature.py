"""HMAC-SHA256 signing of the *raw* request body, the scheme Razorpay documents for its webhooks.

Sign and verify the exact bytes that arrived. Parsing the JSON and re-serialising it
changes whitespace and key order and the signature will no longer match.
"""
from __future__ import annotations

import hashlib
import hmac


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify(body: bytes, signature: str | None, secret: str) -> bool:
    if not signature or not secret:
        return False
    return hmac.compare_digest(sign(body, secret), signature.strip())
