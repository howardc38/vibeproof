"""GREEN -- the fixed half of a real pair.

The shape of a real defect and its repair, with the adopter's domain removed:
its red twin (``red/per_call_idempotency_key.py``) carries the same transport,
so the two files differ in exactly one thing -- where the idempotency key comes
from.

``hashlib.sha256`` over the operation parameters means a retry of the identical
logical adjustment produces the identical key, so the API's ``@idempotent``
directive dedupes it and the stock moves once.  Two *intentional* adjustments
still differ, because their ``change_from_quantity`` differs.
"""

import hashlib

import httpx

_ADJUST_STOCK_MUTATION = """
mutation AdjustStock($input: StockAdjustInput!, $idempotencyKey: String!) {
  stockAdjust(input: $input) @idempotent(key: $idempotencyKey) {
    adjustmentGroup { createdAt }
    userErrors { field message }
  }
}
"""


def _execute_query(endpoint: str, token: str, query: str, variables: dict) -> dict:
    response = httpx.post(
        endpoint,
        headers={"X-Store-Access-Token": token},
        json={"query": query, "variables": variables},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()


def adjust_stock_quantity(
    *,
    endpoint: str,
    token: str,
    stock_item_id: str,
    location_id: str,
    delta: int,
    change_from_quantity: int,
    reason: str = "correction",
) -> dict:
    idempotency_key = hashlib.sha256(
        "|".join(
            [
                "stock_adjust_v1",
                stock_item_id,
                location_id,
                str(int(delta)),
                str(int(change_from_quantity)),
                reason,
            ]
        ).encode("utf-8")
    ).hexdigest()
    variables = {
        "input": {
            "reason": reason,
            "name": "available",
            "changes": [
                {
                    "delta": int(delta),
                    "stockItemId": stock_item_id,
                    "locationId": location_id,
                    "changeFromQuantity": int(change_from_quantity),
                }
            ],
        },
        "idempotencyKey": idempotency_key,
    }
    return _execute_query(endpoint, token, _ADJUST_STOCK_MUTATION, variables)
