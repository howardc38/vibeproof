"""Read-only coordination projections and shared structured-value validation.

This layer reads ledger snapshots. It owns no task lifecycle, document mutation,
worker dispatch or settlement operation.
"""
import copy
import json
from pathlib import Path
import re

EVENT = "coordination_transition"
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}\Z")
_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")


def _validate(value, spec, where):
    kind = spec["type"]
    valid = {"object": isinstance(value, dict), "array": isinstance(value, list),
             "string": isinstance(value, str), "integer": type(value) is int}[kind]
    if not valid:
        raise ValueError(f"{where} must be {kind}")
    if kind == "object":
        unknown = set(value) - set(spec["properties"])
        missing = set(spec["required"]) - set(value)
        if unknown or missing:
            raise ValueError(f"{where}: unknown fields {sorted(unknown)}; missing fields {sorted(missing)}")
        for key, item in value.items():
            _validate(item, spec["properties"][key], f"{where}.{key}")
    elif kind == "array":
        if len(value) < spec.get("minItems", 0):
            raise ValueError(f"{where} needs at least {spec['minItems']} items")
        for index, item in enumerate(value):
            _validate(item, spec["items"], f"{where}[{index}]")
    elif kind == "string":
        if not spec.get("minLength", 0) <= len(value) <= spec.get("maxLength", 100000):
            raise ValueError(f"{where} has invalid length")
        if (value and not value.strip()) or any(ord(c) < 32 and c not in "\n\t\r" for c in value):
            raise ValueError(f"{where} contains empty text or control characters")
        form = spec.get("format")
        if form == "identifier" and not _ID.fullmatch(value):
            raise ValueError(f"{where} must be a stable identifier")
        if form == "absolute-path" and (not Path(value).is_absolute() or "\n" in value):
            raise ValueError(f"{where} must be an absolute worktree path")
        if form == "commit" and not _SHA.fullmatch(value):
            raise ValueError(f"{where} must be a full Git commit ID")
    elif value < spec.get("minimum", 0):
        raise ValueError(f"{where} must be nonnegative")
    if "enum" in spec and value not in spec["enum"]:
        raise ValueError(f"{where} must be one of {spec['enum']}")



def _events(conn):
    for row in conn.execute("SELECT id,payload FROM event WHERE kind=? ORDER BY id", (EVENT,)):
        yield row["id"], json.loads(row["payload"])



def _empty(goal_id):
    return {"goal_id": goal_id, "version": 0, "intent_version": 0, "inputs": [],
            "pending_inputs": [], "assessments": [], "works": {}, "assignments": {}}



def goal_status(conn, goal_id):
    """One durable snapshot, including obsolete assignments and late results."""
    for _, payload in reversed(list(_events(conn))):
        if payload["goal_id"] == goal_id:
            return payload["state"]
    return _empty(goal_id)



def status(conn, data=None):
    data = {} if data is None else data
    if "goal_id" in data:
        return goal_status(conn, data["goal_id"])
    goals = {}
    for _, payload in _events(conn):
        goals[payload["goal_id"]] = payload["state"]
    return {"goals": goals}



def worker_identity(assignment):
    """Only known enrollment or an actual dispatched observation owns identity."""
    if "agent" in assignment:
        return {key: assignment[key] for key in ("host", "session", "agent")}
    for receipt in assignment["receipts"]:
        if (receipt["outcome"] == "dispatched" and receipt.get("worker") is not None and
                receipt["classification"] != "worker_identity_conflict"):
            return copy.deepcopy(receipt["worker"])
    return None



def task_context(conn, task_id):
    """None for historical unmanaged tasks; current enrollment otherwise."""
    found = []
    for state in status(conn)["goals"].values():
        for assignment in state["assignments"].values():
            if assignment["task_id"] == task_id:
                found.append((state, assignment))
    if not found:
        return None
    if sum(1 for _, assignment in found if _owns(assignment)) > 1:
        raise ValueError("task has conflicting coordination owners")
    state, assignment = max(found, key=lambda pair: pair[1]["created_event"])
    return {"goal_id": state["goal_id"], "work_id": assignment["work_id"],
            "revision": assignment["revision"], "assignment_id": assignment["id"],
            "goal_version": assignment["goal_version"],
            "dependency_versions": assignment["dependency_versions"],
            "worker_identity": worker_identity(assignment),
            "status": assignment["status"]}



def _owns(assignment):
    return (assignment["dispatch_state"] not in ("not_started", "stopped") and
            not any(item["status"] == "settled" for item in assignment["settlements"]))



def _coordinates(value):
    """Protocol identity excludes only the schema's free-text fields."""
    if isinstance(value, dict):
        prose = {"text", "quote", "title", "request", "reason", "evidence"}
        return {key: _coordinates(item) for key, item in value.items() if key not in prose}
    if isinstance(value, list):
        return [_coordinates(item) for item in value]
    return value
