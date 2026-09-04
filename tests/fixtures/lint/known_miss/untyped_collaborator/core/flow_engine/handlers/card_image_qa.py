from typing import Any


def handle(executor: Any, inputs: dict) -> dict:
    # `executor` really is core.flow_engine.executor at run time, but
    # nothing in this file says so. Guessing is how the previous tool
    # produced 47 findings on receivers it never saw bound.
    return {"planned": executor._as_list(inputs.get("planned"))}
