"""RED -- replay.  The idempotency key is regenerated on every call.

Lifted from the reference adopter.  The store API's stock-adjust mutation
takes a mandatory ``@idempotent(key: $idempotencyKey)`` directive; this builds
that key with ``uuid.uuid4().hex``, so a retry of the same logical adjustment
arrives under a key the server has never seen and the stock moves twice.  The
fix derives the key from the operation parameters with ``hashlib.sha256``
instead; the repaired half is ``green/real_stable_idempotency_key.py``.

The transport helper is kept in the file because the key and the POST live one
call apart in the original -- which is why the rule looks for a fresh key
anywhere in a file that writes, rather than only in the writing scope.
"""

import json
import uuid

import httpx

_ADJUST_INVENTORY_MUTATION = """
mutation AdjustInventory($input: InventoryAdjustQuantitiesInput!, $idempotencyKey: String!) {
  inventoryAdjustQuantities(input: $input) @idempotent(key: $idempotencyKey) {
    inventoryAdjustmentGroup { createdAt }
    userErrors { field message }
  }
}
"""


class ShopifyAdminClient:
    def __init__(self, endpoint: str, token: str) -> None:
        self.endpoint = endpoint
        self.token = token

    def _execute_query(self, query: str, variables: dict) -> dict:
        response = httpx.post(
            self.endpoint,
            headers={"X-Shopify-Access-Token": self.token},
            json={"query": query, "variables": variables},
            timeout=30.0,
        )
        response.raise_for_status()
        return response.json()

    def adjust_inventory_quantity(
        self,
        *,
        inventory_item_id: str,
        location_id: str,
        delta: int,
        reason: str = "correction",
        change_from_quantity: int = 0,
    ) -> dict:
        variables = {
            "input": {
                "reason": reason,
                "name": "available",
                "changes": [
                    {
                        "delta": int(delta),
                        "inventoryItemId": inventory_item_id,
                        "locationId": location_id,
                        "changeFromQuantity": int(change_from_quantity),
                    }
                ],
            },
            "idempotencyKey": uuid.uuid4().hex,
        }
        result = self._execute_query(_ADJUST_INVENTORY_MUTATION, variables)
        user_errors = (
            (result.get("data") or {})
            .get("inventoryAdjustQuantities", {})
            .get("userErrors")
            or []
        )
        if user_errors:
            raise RuntimeError(f"Shopify userErrors: {json.dumps(user_errors)[:300]}")
        return result
