"""RED -- readback.  The read-back cannot fail, so it answers about the wrong thing.

Lifted from the reference adopter, where it was one of the three causes the
fix commit names for publishing a record twice.  The only edit is the transport:
the original reaches the feed through ``self._request_with_retry("GET", ...)``,
whose verb lives in an argument, and this checker's built-in default table has
no pattern for a private helper of that shape.  ``requests.get`` is the same
read at the same place in the control flow.

The defect: an unreadable body and an empty account both return ``set()``.  The
caller persists that as a pre-publish baseline and later diffs against it, so a
publish that *did* land looks like one that never happened, and the retry posts
again.  The fix replaces the ``return set()`` with a ``raise``; the repaired
half is ``green/real_recent_media_ids_fixed.py``.
"""

import requests

RECONCILE_MEDIA_LOOKBACK = 25


class MetaGraphAdapter:
    def __init__(self, ig_user_id: str, token: str) -> None:
        self._ig_user_id = ig_user_id
        self._token = token

    def recent_media_ids(self, *, edge: str, limit: int = RECONCILE_MEDIA_LOOKBACK):
        """The recent media ids to diff a later reconciliation against."""
        payload = requests.get(
            f"https://graph.facebook.com/v21.0/{self._ig_user_id}/{edge}",
            params={"fields": "id", "limit": str(limit), "access_token": self._token},
            timeout=30,
        ).json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return set()
        return {str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")}
