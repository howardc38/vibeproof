"""GREEN (tricky): the ``except`` does not re-raise, but the ``finally`` does.

Looking only at handler bodies flags this file.  A ``raise`` in ``finally`` runs
on every path out of the statement, including the handled one, so the caller
still gets an exception -- the try fails closed as a whole.
"""

import logging

import requests

logger = logging.getLogger(__name__)


def charge(url: str, amount: int) -> None:
    failure = None
    try:
        requests.post(url, json={"amount": amount}, timeout=10)
    except requests.exceptions.RequestException as exc:
        failure = exc
        logger.warning("charge failed: %s", exc)
    finally:
        if failure is not None:
            raise RuntimeError("charge did not complete") from failure
