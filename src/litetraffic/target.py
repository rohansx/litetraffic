from __future__ import annotations

import ipaddress
from urllib.parse import urlsplit

# ponytail: literal addresses and well-known names only; hostnames are not resolved,
# so a DNS name that points at a metadata address still passes.
METADATA_HOSTS = {"metadata.google.internal", "metadata.goog", "fd00:ec2::254"}


def _blocked(host: str) -> bool:
    if host.rstrip(".").lower() in METADATA_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        address = address.ipv4_mapped
    return address.is_link_local


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
