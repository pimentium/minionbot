import pytest

from minionbot.web.errors import UnsafeUrlError
from minionbot.web.safety import AssertPublicUrl, IsPublicAddress


@pytest.mark.parametrize("address", ["8.8.8.8", "93.184.216.34", "2606:4700:4700::1111"])
def test_public_addresses(address):
    assert IsPublicAddress(address)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "127.1.2.3", "10.0.0.1", "172.16.5.4", "192.168.1.1", "169.254.169.254",  # incl. cloud metadata
        "100.64.0.1", "0.0.0.0", "224.0.0.1", "::1", "fe80::1", "fc00::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "not-an-ip",
    ],
)
def test_non_public_addresses(address):
    assert not IsPublicAddress(address)


def Resolver(table):
    async def Resolve(host, port):
        if host not in table:
            raise OSError("unknown host")
        return table[host]

    return Resolve


PUBLIC = Resolver({"example.com": ["93.184.216.34"], "dual.example": ["93.184.216.34", "2606:4700:4700::1111"]})


async def test_public_host_is_allowed():
    await AssertPublicUrl("https://example.com/page", PUBLIC)
    await AssertPublicUrl("http://example.com:8080/x", PUBLIC)
    await AssertPublicUrl("https://dual.example/", PUBLIC)
    await AssertPublicUrl("https://8.8.8.8/", PUBLIC)  # public IP literal


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/", "http://localhost/", "http://[::1]/", "http://10.1.2.3/admin", "http://192.168.0.1:8080/",
        "http://169.254.169.254/latest/meta-data/", "http://0.0.0.0/", "http://[::ffff:127.0.0.1]/",
    ],
)
async def test_private_literals_and_names_are_blocked(url):
    table = {"localhost": ["127.0.0.1", "::1"]}
    with pytest.raises(UnsafeUrlError):
        await AssertPublicUrl(url, Resolver(table))


async def test_name_resolving_to_private_address_is_blocked_dns_rebinding_style():
    with pytest.raises(UnsafeUrlError, match="private"):
        await AssertPublicUrl("https://evil.example/", Resolver({"evil.example": ["10.0.0.5"]}))


async def test_one_private_address_among_public_ones_blocks():
    with pytest.raises(UnsafeUrlError):
        await AssertPublicUrl("https://mixed.example/", Resolver({"mixed.example": ["93.184.216.34", "192.168.1.9"]}))


@pytest.mark.parametrize(
    "url",
    ["ftp://example.com/", "file:///etc/passwd", "gopher://example.com/", "https://user:pw@example.com/", "https://example.com:22/", "https://example.com:6379/", "https:///nohost"],
)
async def test_scheme_credentials_and_port_restrictions(url):
    with pytest.raises(UnsafeUrlError):
        await AssertPublicUrl(url, PUBLIC)


async def test_unresolvable_host():
    with pytest.raises(UnsafeUrlError, match="find that host"):
        await AssertPublicUrl("https://nope.example/", PUBLIC)
