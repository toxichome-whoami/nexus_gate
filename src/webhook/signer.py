import hmac
import hashlib

def generate_signature(secret: str, payload: str) -> str:
    """Generate HMAC-SHA256 signature for webhook payload."""
    sig = hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()
    return f"sha256={sig}"
