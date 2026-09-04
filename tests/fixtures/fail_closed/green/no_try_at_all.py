"""GREEN: outbound calls everywhere and not a single ``try``.

Exit 0 here is a verified answer, not an abstention: an exception from any of
these propagates to the caller, which is exactly what failing closed means.
"""

import requests


def upload(url: str, blob: bytes) -> str:
    response = requests.put(url, data=blob, timeout=30)
    response.raise_for_status()
    return response.headers["ETag"]


def delete(url: str) -> None:
    requests.delete(url, timeout=30).raise_for_status()
