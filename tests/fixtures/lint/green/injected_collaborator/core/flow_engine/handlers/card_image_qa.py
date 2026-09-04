from typing import Any


def handle(executor: Any, inputs: dict) -> dict:
    planned = executor._as_list(inputs.get("planned_cards"))
    candidates = executor._extract_candidates(
        {"items": executor._as_list(inputs.get("candidates"))}
    )
    return {"planned": planned, "candidates": candidates}
