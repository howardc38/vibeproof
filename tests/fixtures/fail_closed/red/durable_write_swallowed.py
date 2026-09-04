"""RED: a durable write logged and stepped over, in two different variants.

``save_checkpoint`` swallows and continues (``swallow``); ``record_receipt``
hands the caller an empty dict, which reads as "nothing to record" rather than
"the write failed" (``falsey``).  Both leave state the process believes it wrote.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def save_checkpoint(path: Path, state: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state))
    except OSError as exc:
        logger.warning("checkpoint not written: %s", exc)


def record_receipt(cursor, receipt: dict) -> dict:
    try:
        cursor.execute(
            "INSERT INTO receipts (id, body) VALUES (%s, %s)",
            (receipt["id"], json.dumps(receipt)),
        )
        return receipt
    except Exception as exc:
        logger.warning("receipt not recorded: %s", exc)
        return {}
