import hashlib
import hmac
import os
import secrets

def demo_enabled():
    return os.getenv("DEMO_MODE", "true").lower() == "true"

def hash_password(password):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000)
    return salt + "$" + digest.hex()

def verify_password(password, encoded):
    try:
        salt, expected = encoded.split("$")
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000)
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, AttributeError):
        return False
