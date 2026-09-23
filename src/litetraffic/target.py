from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

# ponytail: literal addresses and well-known names only; hostnames are not resolved,
# so a DNS name that points at a metadata address still passes.
METADATA_HOSTS = {"metadata.google.internal", "metadata.goog"}
METADATA_ADDRESSES = {ipaddress.ip_address("fd00:ec2::254")}
_INET_PART = re.compile(r"0[xX][0-9a-fA-F]*|0[0-7]*|[1-9][0-9]*")


def _inet_aton(host: str) -> ipaddress.IPv4Address | None:
    """Parse the legacy IPv4 forms resolvers accept: 1-4 dot parts, each decimal, 0-octal or 0x-hex."""
    parts = host.split(".")
    if len(parts) > 4 or not all(_INET_PART.fullmatch(part) for part in parts):
        return None
    numbers = [int(p[2:] or "0", 16) if p[:2].lower() == "0x" else int(p, 8 if p[0] == "0" else 10) for p in parts]
    *leading, last = numbers
    if any(n > 255 for n in leading) or last >= 256 ** (5 - len(parts)):
        return None
    return ipaddress.IPv4Address(sum(n << (8 * (3 - i)) for i, n in enumerate(leading)) + last)


def _address(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return _inet_aton(host)


def _blocked(host: str) -> bool:
    host = host.rstrip(".").lower()
    if host in METADATA_HOSTS:
        return True
    address = _address(host)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address is not None and (address in METADATA_ADDRESSES or address.is_link_local)


def validate_target(value: str, label: str = "target") -> str:
    """Return the target without a trailing slash, or raise ValueError if it is not allowed."""
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{label} must be an absolute http or https URL")
    if parsed.username or parsed.password:
        raise ValueError(f"{label} URL must not contain credentials")
    if _blocked(parsed.hostname or ""):
        raise ValueError(f"{label} {parsed.hostname}: link-local/metadata address not allowed")
    return value.rstrip("/")
