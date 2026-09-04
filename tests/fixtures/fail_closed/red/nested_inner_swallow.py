"""RED (tricky): the outer ``try`` fails closed, the inner one does not.

Reading only outermost handlers scores this clean.  The outer ``except`` does
re-raise -- but it can only re-raise what reaches it, and the inner handler makes
sure a failed per-order POST never does.  ``sync_orders`` reports success after
silently skipping orders.
"""

import logging

logger = logging.getLogger(__name__)


def sync_orders(session, url: str, orders: list) -> int:
    sent = 0
    try:
        for order in orders:
            try:
                session.post(url, json=order)
                sent += 1
            except Exception as exc:
                logger.warning("order %s not sent: %s", order["id"], exc)
        session.commit()
    except ValueError:
        logger.error("malformed order batch")
        raise
    return sent
