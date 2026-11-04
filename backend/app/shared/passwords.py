"""Password hashing with scrypt (stdlib — no extra dependency).

Uses ``hashlib.scrypt`` with a random 16-byte salt, encoded as
``scrypt$\u003csalt_hex\u003e$\u003chash_hex\u003e``.  Parameters (n=2¹⁴, r=8, p=1) follow the
OWASP recommendation for interactive login latency (~100 ms).
"""

import hashlib
import hmac
import os

_N = 2**14
_R = 8
_P = 1
_DKLEN = 32


def hash_password(plain: str) -> str:
    """Derive a storable hash from a plaintext password."""
    salt = os.urandom(16)
    derived = hashlib.scrypt(
        plain.encode(),
        salt=salt,
        n=_N,
        r=_R,
        p=_P,
        dklen=_DKLEN,
    )
    return f"scrypt${salt.hex()}${derived.hex()}"


def verify_password(plain: str, stored: str) -> bool:
    """Constant-time comparison of a plaintext password against a stored hash."""
    try:
        _, salt_hex, hash_hex = stored.split("$")
    except ValueError:
        return False
    derived = hashlib.scrypt(
        plain.encode(),
        salt=bytes.fromhex(salt_hex),
        n=_N,
        r=_R,
        p=_P,
        dklen=_DKLEN,
    )
    return hmac.compare_digest(derived.hex(), hash_hex)
