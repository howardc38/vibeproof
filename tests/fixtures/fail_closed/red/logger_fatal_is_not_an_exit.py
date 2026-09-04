"""RED: `log.fatal` writes a line and returns, and the handler walks past.

`TERMINATING_TAILS` matched the bare tail of any dotted callee, so this read as
fail-closed: `fatal` was in the set. `logging.Logger.fatal` is a stdlib alias
for `critical()` -- it logs and returns -- so the POST's failure is swallowed
exactly the way `swallowed_http_post.py` swallows it, with one word changed.

A rule that seals a handler on the strength of a method name is a fail-open in
the checker that exists to find fail-opens.
"""

import logging

import requests

log = logging.getLogger(__name__)


def notify(url: str, payload: dict) -> None:
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as exc:
        log.fatal("notify failed: %s", exc)
