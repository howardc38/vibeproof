"""GREEN -- the fixed half of a real pair.

The shape of a real defect and its repair, with the adopter's domain removed:
its red twin (``red/readback_returns_empty_when_unreadable.py``) carries the
same transport substitution.  The one behavioural change between them is the
last branch: an unreadable body raises instead of returning ``set()``, so a
caller can no longer read "could not tell" as "the account holds nothing".

The pair is what makes the claim answerable rather than permanent: the same
checker that fails the red file passes this one, and the edit between them is
the fix a worker would make.
"""

import requests

RECONCILE_ITEM_LOOKBACK = 25


class FeedAdapter:
    def __init__(self, account_id: str, token: str) -> None:
        self._account_id = account_id
        self._token = token

    def recent_item_ids(self, *, edge: str, limit: int = RECONCILE_ITEM_LOOKBACK):
        """Raises on a payload that does not carry a list, because "an empty
        account" is a persisted answer: a baseline of ``[]`` says the surface
        held nothing, and every recent item id is therefore a candidate.
        Returning ``set()`` for an unreadable body would write that claim on the
        strength of a malformed response."""
        payload = requests.get(
            f"https://feed.example.invalid/v1/{self._account_id}/{edge}",
            params={"fields": "id", "limit": str(limit), "access_token": self._token},
            timeout=30,
        ).json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise RuntimeError(
                f"feed {edge} listing returned no data array; the item surface is unreadable"
            )
        return {str(item.get("id")) for item in data if isinstance(item, dict) and item.get("id")}
