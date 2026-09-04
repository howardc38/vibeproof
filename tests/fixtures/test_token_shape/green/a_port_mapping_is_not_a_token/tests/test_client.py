"""GREEN: docker-compose ports, which is what a test writes.

`CREDENTIAL_SHAPE`'s first alternative was `\d{2,}:[A-Za-z0-9_\-]{4,}` with
nothing requiring a non-digit after the colon, so every one of these was
reported as a live credential -- and none is reached by `SAYS_FAKE`, so the fix
a worker reaches for is to write TEST into a port number.
"""


def test_compose_ports():
    ports = ["5432:5432", "8080:8080", "6379:6379", "27017:27017"]
    assert all(":" in p for p in ports)
