"""GREEN (tricky): the handler ends the process instead of raising.

``sys.exit`` is not a ``raise`` in the AST, but nothing downstream runs on a
stale assumption either.  A rule that only looks for ``ast.Raise`` reports every
CLI entrypoint written this way.
"""

import logging
import sys

import requests

logger = logging.getLogger(__name__)


def main(url: str) -> None:
    try:
        requests.post(url, json={"ping": True}, timeout=5)
    except requests.exceptions.RequestException as exc:
        logger.error("cannot reach %s: %s", url, exc)
        sys.exit(2)
    print("delivered")
