"""
Print a .env with every secret masked, so it can be pasted into a ticket or a chat.

Reading the file with `cat` is fine on your own terminal and nowhere else. This exists
because the obvious masker — match names containing KEY/SECRET/PASSWORD — misses the
case that actually leaks: a credential embedded in a URL.

    DATABASE_URL=postgresql+asyncpg://edi:<password>@postgres:5432/edi

The name says nothing about a secret, so a name-based filter prints it in full. That
is not hypothetical; it is why this file exists.

Masked values show length, first four characters and a short sha256 — enough to answer
"is the server running the key I set?" by comparing fingerprints, without revealing
anything. Comments and non-secret values are left readable, because the point is to be
able to see the configuration.

    python3 scripts/show_env.py [path]        # default: ./.env
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

#: Names whose whole value is a secret. CLIENT_ID is here because half a
#: credential pair is still a credential -- it printed in full once.
SECRET_NAME = re.compile(
    r"(KEY|SECRET|PASSWORD|PASSWD|TOKEN|DSN|CREDENTIAL|WEBHOOK|CLIENT_ID)", re.I
)

#: A URL carrying user:password@host. Masked in place so the host stays readable —
#: knowing which database you are pointed at is the reason to read the file at all.
URL_WITH_CREDENTIALS = re.compile(r"^[a-z0-9+.\-]+://[^/\s]*:[^/@\s]+@", re.I)


def fingerprint(value: str) -> str:
    sha = hashlib.sha256(value.encode()).hexdigest()[:10]
    return f"<len {len(value)}, starts {value[:4]}, sha {sha}>"


def mask_url(value: str) -> str:
    parts = urlsplit(value)
    if not parts.password:
        return value
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    netloc = f"{parts.username or ''}:{fingerprint(parts.password)}@{host}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def mask_line(line: str) -> str:
    if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
        return line
    name, value = line.split("=", 1)
    if not value:
        return line
    if URL_WITH_CREDENTIALS.match(value):
        return f"{name}={mask_url(value)}"
    if SECRET_NAME.search(name):
        return f"{name}={fingerprint(value)}"
    return line


def main() -> int:
    path = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
    if not path.is_file():
        print(f"no such file: {path}", file=sys.stderr)
        return 1
    for line in path.read_text().splitlines():
        print(mask_line(line.rstrip()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
