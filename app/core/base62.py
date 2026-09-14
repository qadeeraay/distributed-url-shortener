import re

BASE62_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
BASE = len(BASE62_ALPHABET)  # 62

# Custom alias regex: alphanumeric, underscores, hyphens, between 4 and 32 characters
ALIAS_REGEX = re.compile(r"^[a-zA-Z0-9_-]{4,32}$")

RESERVED_ROUTES = {
    "api", "r", "healthz", "metrics", "docs", "redoc", "openapi.json",
    "admin", "login", "register", "dashboard", "analytics", "static", "favicon.ico"
}


_CHAR_TO_INDEX = {c: i for i, c in enumerate(BASE62_ALPHABET)}


def encode(num: int) -> str:
    """Bijectively encodes a non-negative integer into a Base62 string."""
    if num < 0:
        raise ValueError("Cannot encode negative integers.")
    if num == 0:
        return BASE62_ALPHABET[0]

    arr = []
    while num > 0:
        num, rem = divmod(num, BASE)
        arr.append(BASE62_ALPHABET[rem])
    return "".join(reversed(arr))


def decode(code: str) -> int:
    """Decodes a Base62 string back into its original integer ID."""
    if not code:
        raise ValueError("Cannot decode an empty string.")

    num = 0
    for char in code:
        idx = _CHAR_TO_INDEX.get(char)
        if idx is None:
            raise ValueError(f"Invalid Base62 character: '{char}'")
        num = num * BASE + idx
    return num


def validate_custom_alias(alias: str) -> bool:
    """
    Validates user-supplied custom short aliases:
    - 4 to 32 characters in length
    - Alphanumeric, underscores, hyphens only
    - Prevents shadowing application root endpoints (e.g., 'api', 'metrics')
    """
    if not alias or not ALIAS_REGEX.match(alias):
        return False

    return alias.lower() not in RESERVED_ROUTES
