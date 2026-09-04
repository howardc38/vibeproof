"""GREEN (tricky): the outbound call is *defined* inside the try, not made there.

``requests.post`` appears lexically inside the ``try`` body -- but inside a
``def``, so the ``try`` never guards it.  Whatever the callback does later, this
handler is not swallowing it.

This was a real bug in an earlier draft of the analysis: the nested ``def`` was
filtered out of the descendants but not out of the top level of the block, so
``core/flow_engine/handlers/video_director.py`` was reported for a call that
happens in a callback the try only registers.
"""

import logging

import requests

logger = logging.getLogger(__name__)


def register(registry, url: str) -> None:
    try:
        def on_event(payload: dict) -> None:
            requests.post(url, json=payload, timeout=10)

        registry.add(on_event)
    except KeyError as exc:
        logger.warning("registry unavailable: %s", exc)


def build(url: str):
    try:
        sender = lambda payload: requests.post(url, json=payload, timeout=10)  # noqa: E731
    except NameError:
        logger.warning("could not build sender")
        return None
    return sender
