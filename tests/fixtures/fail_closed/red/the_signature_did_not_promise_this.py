"""RED: near-misses of the two exemptions this rule just widened.

A rule that stops reporting something has to say where it still reports, or the
widening is unbounded and nobody can tell. Each handler below is one edit away
from a shape that is now exempt, and each is still a fail-open.

* ``-> str`` returning ``False`` -- the signature does not make ``False`` the
  answer, so a caller reading a string gets a bool it never asked for.
* ``-> bool`` returning ``None`` -- a third value the signature did not promise,
  and the one every ``if not result:`` reads as the same thing as ``False``.
* ``-> bool`` with no return at all -- falls off the end, which is ``None``
  again, arrived at by saying nothing.
* ``-> bool`` returning ``False`` under ``except Exception`` -- the exemption
  needs the handler to have caught something it named. A catch-all catches "the
  dependency is down" too, so ``False`` answers a question that never ran, and
  the caller cannot tell that from a real no. Three ``bypass/`` cases in this
  directory are that shape; the first draft of the exemption unreported all
  three at once, which is how the line was found.
* a helper whose name only begins like a report -- ``_failsafe`` is the price of
  stem matching, named in the table's own comment, and it is a price on the
  exempting side; ``_finalise`` is not near any stem and stays reported.
"""

import logging

import requests

logger = logging.getLogger(__name__)


def _finalise(payload: dict) -> dict:
    return payload


def describe(url: str) -> str:
    try:
        return requests.get(url, timeout=5).text
    except requests.exceptions.RequestException as exc:
        logger.warning("describe failed: %s", exc)
        return False


def is_current(client, key: str) -> bool:
    try:
        return client.head(key)
    except ConnectionError:
        return None


def is_healthy(client) -> bool:
    try:
        return client.ping()
    except ConnectionError as exc:
        logger.warning("ping failed: %s", exc)


def signature_matches(request, secret_store) -> bool:
    """`-> bool` and `return False`, and still a fail-open.

    `except Exception` covers the secret store being unreachable, so `False`
    reports "did not match" about a comparison that never happened.
    """
    try:
        return request.signature == secret_store.read(request.tenant_id)
    except Exception:
        logger.warning("signature check failed")
        return False


def settle(client, order: str) -> dict:
    try:
        client.settle(order)
    except RuntimeError as exc:
        logger.warning("settle failed: %s", exc)
        return _finalise({"order": order})
    return {"ok": True}
