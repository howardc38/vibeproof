"""GREEN: the handler reports, and the two ways it reports were both missed.

Both shapes here came back from an adopter running this checker on real code,
and both were exemptions the rule already grants -- it just could not see them.

**The name of the helper decided the verdict.** ``ERROR_RESULT_WORDS`` was a
list of whole words, so it had to carry every inflection of every concept, and
it carried four of seven: ``failed`` but not ``fail``, ``denied`` but not
``deny``, ``refuse`` but not ``refused``. The adopter renamed a helper and two
findings went from FAIL to PASS with no logic changed, which is the measurement.
Stems and ``startswith`` now, so the forms nobody typed are covered.

**The signature already said what ``False`` means.** ``return {"ok": False}``
was read as an explicit failure and a bare ``return False`` was not -- but a
function whose whole return type is ``bool`` has nowhere for a caller to read
``False`` as success. Five handlers of this shape were baselined by that adopter
rather than fixed, which is what a baseline is for and also what it means when
five entries arrive wearing one shape.

What stays reported, and is why this file is not the whole rule: ``-> str``
returning ``False`` (the signature does not make it the answer), ``-> bool``
returning ``None`` (a third value the signature did not promise), and any
handler that returns nothing at all.
"""

import logging

import requests

logger = logging.getLogger(__name__)


def _fail(message: str) -> dict:
    return {"ok": False, "error": message}


def _deny(reason: str) -> dict:
    return {"ok": False, "denied": reason}


def _refused(reason: str) -> dict:
    return {"ok": False, "refused": reason}


def publish(url: str, slug: str) -> dict:
    try:
        requests.post(f"{url}/publish", json={"slug": slug}, timeout=10)
    except requests.exceptions.RequestException as exc:
        logger.warning("publish failed: %s", exc)
        return _fail(str(exc))
    return {"ok": True, "slug": slug}


def authorise(client, actor: str) -> dict:
    try:
        client.check(actor)
    except PermissionError as exc:
        return _deny(str(exc))
    return {"ok": True}


def enqueue(client, job: str) -> dict:
    try:
        client.submit(job)
    except RuntimeError as exc:
        return _refused(str(exc))
    return {"ok": True}


def is_reachable(url: str) -> bool:
    """`False` is the answer, not a value that leaked out of a handler."""
    try:
        requests.head(url, timeout=5)
    except requests.exceptions.RequestException as exc:
        logger.warning("unreachable: %s", exc)
        return False
    return True


def has_capacity(client, queue: str) -> bool:
    try:
        return client.depth(queue) < 100
    except ConnectionError:
        return False
