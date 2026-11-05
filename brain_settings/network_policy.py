"""Narrow internal-network opt-in; local and metadata services remain forbidden."""

import ipaddress

INTERNAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10", "fc00::/7",
))
METADATA_ADDRESSES = frozenset(ipaddress.ip_address(value) for value in (
    "100.100.100.200", "fd00:ec2::254", "fd20:ce::254",
))


def address_allowed(address, *, allow_private_network: bool = False) -> bool:
    """Allow public unicast, or explicitly selected internal unicast ranges."""
    if (address in METADATA_ADDRESSES or address.is_loopback or address.is_link_local
            or address.is_multicast or address.is_unspecified or address.is_reserved
            or getattr(address, "is_site_local", False)
            or getattr(address, "ipv4_mapped", None) is not None):
        return False
    if address.is_global:
        return True
    return allow_private_network and any(
        address.version == network.version and address in network for network in INTERNAL_NETWORKS
    )


def hostname_allowed(hostname: str, *, allow_private_network: bool = False) -> bool:
    hostname = hostname.lower().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    return allow_private_network or not hostname.endswith((".local", ".internal", ".lan"))
