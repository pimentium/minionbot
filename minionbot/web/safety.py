"""SSRF protection: /url makes the bot fetch addresses chosen by users, so only public hosts are allowed."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from minionbot.web.errors import UnsafeUrlError

ALLOWED_PORTS = {80, 443, 8080, 8443}
Resolver = Callable[[str, int], Awaitable[list[str]]]


async def ResolveHost(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return sorted({info[4][0] for info in infos})


def IsPublicAddress(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%")[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    # is_global is False for private, loopback, link-local, carrier-grade NAT, reserved and unspecified ranges
    return ip.is_global and not ip.is_multicast


async def AssertPublicUrl(url: str, resolver: Resolver = ResolveHost) -> None:
    """Raise UnsafeUrlError unless url is http(s) on an allowed port and every address of its host is public."""
    try:
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port
    except ValueError as error:
        raise UnsafeUrlError("That doesn't look like a valid address.") from error
    if parts.scheme not in ("http", "https") or not host:
        raise UnsafeUrlError("Only http and https addresses are supported.")
    if parts.username or parts.password:
        raise UnsafeUrlError("Addresses with a user name or password are not supported.")
    port = port or (443 if parts.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise UnsafeUrlError("That port is not allowed.")
    try:
        addresses = [host] if IsPublicLiteral(host) is not None else await resolver(host, port)
    except OSError as error:
        raise UnsafeUrlError("I couldn't find that host.") from error
    if not addresses or not all(IsPublicAddress(address) for address in addresses):
        raise UnsafeUrlError("I can't open addresses on private or local networks.")


def IsPublicLiteral(host: str) -> bool | None:
    """True/False if host is an IP literal (public or not), None if it is a name that needs resolving."""
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None
    return IsPublicAddress(host.strip("[]"))
