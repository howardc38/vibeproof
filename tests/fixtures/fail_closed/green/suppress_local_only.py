"""GREEN: ``contextlib.suppress`` around things that reach nothing.

The ``with suppress(...)`` shape is only a defect when what it wraps has an
outside effect.  Suppressing a ``KeyError`` off a dict or a ``ValueError`` off a
parse is ordinary Python, and a rule that reports the construct rather than what
it guards would fire on all of it.
"""

import contextlib
import json


def read_flag(config: dict, name: str) -> bool:
    with contextlib.suppress(KeyError, TypeError):
        return bool(config["flags"][name])
    return False


def parse_optional(raw: str) -> dict:
    payload: dict = {}
    with contextlib.suppress(ValueError):
        payload = json.loads(raw)
    return payload
