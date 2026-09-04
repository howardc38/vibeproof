"""GREEN: the same idiom with the raise in the `else:` of a later check.

`_contains_raise(later.body)` read the `if` body and not its `orelse`, so the
branch that actually raises was the branch nothing looked at.
"""

import requests


def notify(url, payload):
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception:
        pass
    if delivered(url, payload):
        record(url)
    else:
        raise RuntimeError("notify did not land")
