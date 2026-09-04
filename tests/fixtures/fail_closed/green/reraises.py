"""GREEN: the handler logs and re-raises.

Logging is not the problem; logging *instead of* propagating is.  Both shapes
here fail closed -- the bare ``raise`` and the wrapped ``raise ... from exc``.
"""

import logging

import requests

logger = logging.getLogger(__name__)


class PublishFailed(RuntimeError):
    pass


def publish(url: str, payload: dict) -> dict:
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json()
    except requests.exceptions.RequestException:
        logger.warning("publish failed, propagating")
        raise


def publish_wrapped(url: str, payload: dict) -> dict:
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.json()
    except requests.exceptions.RequestException as exc:
        raise PublishFailed(str(exc)) from exc
