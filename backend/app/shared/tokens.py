"""Bearer credentials of the form ``<kind>_<prefix>_<secret>``.

The prefix (12 hex chars) is a public identifier used to find the credential; the
secret is 256 random bits and never leaves the response that created it. Only a
SHA-256 of the full token is stored: with that much entropy a slow password hash
adds nothing. ``kind`` separates credential families (``tlx`` B2B, ``tlxs`` staff),
so a token of one family never matches the pattern of the other.
"""

import hashlib
import re
import secrets


def token_pattern(kind: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(kind)}_([0-9a-f]{{12}})_([A-Za-z0-9_-]{{43}})$")


def new_token(kind: str) -> tuple[str, str]:
    """(prefix, full token). The full token is shown once and never stored."""
    prefix = secrets.token_hex(6)
    return prefix, f"{kind}_{prefix}_{secrets.token_urlsafe(32)}"


def prefix_of(full_token: str, pattern: re.Pattern[str]) -> str | None:
    match = pattern.match(full_token)
    return match.group(1) if match else None


def hash_token(full_token: str) -> str:
    return hashlib.sha256(full_token.encode()).hexdigest()
