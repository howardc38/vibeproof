"""GREEN: swallowing handlers around operations that reach nothing.

Every ``try`` here catches and continues, and none of them is a defect: parsing a
string, writing to an in-memory buffer, and normalising a datetime have no
outside world to leave inconsistent.  A rule that fires on all 214 swallowing
handlers in a real repo is a rule people turn off, so this file is the control:
the narrowing has to hold, not just the detection.

Note the names deliberately collide with the risky vocabulary -- ``buf.write``,
``dt.replace``, ``text.replace`` -- because tail-name matching alone would flag
all three.
"""

import datetime as dt
import io
import json
import logging

logger = logging.getLogger(__name__)


def parse_config(raw: str) -> dict:
    try:
        return json.loads(raw)
    except ValueError:
        logger.info("config not JSON, using defaults")
        return {}


def render(rows: list) -> str:
    buf = io.StringIO()
    for row in rows:
        try:
            buf.write(str(row))
        except Exception:
            logger.debug("unrenderable row")
    return buf.getvalue()


def start_of_day(when: dt.datetime) -> dt.datetime:
    try:
        return when.replace(hour=0, minute=0, second=0, microsecond=0)
    except ValueError:
        return when


def slugify(text: str) -> str:
    try:
        return text.replace(" ", "-").lower()
    except AttributeError:
        return ""
