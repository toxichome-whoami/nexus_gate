import hashlib
import hmac


def generate_signature(secret: str, payload: str) -> str:
    """
    Generates a secure HMAC-SHA256 signature for outgoing webhook validation.

    The resulting signature prefix (sha256=...) is a common standard
    (GitHub style) allowing downstream processors to cryptographically verify
    the origin of the request.
    """
    signature = hmac.new(
        secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    return f"sha256={signature}"
