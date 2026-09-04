"""RED: an HTTP POST whose failure is logged and then walked past.

The plainest shape of the rule.  ``notify`` returns normally whether or not the
webhook was delivered, so no caller can distinguish the two.
"""

import logging

import requests

logger = logging.getLogger(__name__)


def notify(url: str, payload: dict) -> None:
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as exc:
        logger.warning("notify failed: %s", exc)
