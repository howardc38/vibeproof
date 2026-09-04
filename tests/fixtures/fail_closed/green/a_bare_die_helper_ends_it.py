"""GREEN: a bare `die()` is the repo's own exit helper, and it is called bare.

The control for `red/logger_fatal_is_not_an_exit.py`. Splitting the table into
bare names and full dotted calls has to keep this one closed -- a rule that
refused every name it cannot resolve would pass that red case by reporting
everything.
"""

import requests


def die(message: str) -> None:
    raise SystemExit(message)


def main(url: str) -> None:
    try:
        requests.post(url, json={"ping": True}, timeout=5)
    except Exception as exc:
        die(f"cannot reach {url}: {exc}")
    print("delivered")
