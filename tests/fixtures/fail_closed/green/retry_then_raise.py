"""GREEN (tricky): a bounded retry loop.

Each attempt's handler swallows, on purpose -- that is what "retry" means.  The
loop's block raises once the attempts are spent, so no caller ever sees a silent
failure.  Flagging this reports every retry loop in every codebase, which is the
fastest way to make people stop reading the gate's output.
"""

import logging
import time

import requests

logger = logging.getLogger(__name__)


def upload_with_retry(url: str, blob: bytes) -> dict:
    last_error = None
    for attempt in range(1, 4):
        try:
            return requests.put(url, data=blob, timeout=30).json()
        except requests.exceptions.RequestException as exc:
            last_error = exc
            logger.warning("attempt %s failed: %s", attempt, exc)
            time.sleep(attempt)
    raise RuntimeError("upload failed after 3 attempts") from last_error
