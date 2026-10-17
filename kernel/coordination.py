"""Request revisions and host-observed work ownership in the shared ledger.

Hosts interpret requests and execute workers. This module validates their
structured decisions, serializes revisions, and fences obsolete work.
"""
import copy
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import design, hashing, ledger, task_provenance
from .coordination_state import (EVENT, _coordinates, _events, _owns, _validate,
                                 goal_status, task_context, worker_identity)
from .coordination_state import status as _state_status


def _object(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


_ID_SPEC = {"type": "string", "minLength": 1, "maxLength": 200, "format": "identifier"}
_TEXT = {"type": "string", "minLength": 1, "maxLength": 100000}
_VERSION = {"type": "integer", "minimum": 0}
_HOST = {"type": "string", "enum": ["claude", "codex"]}
_REF = {"type": "string", "minLength": 1, "maxLength": 1000}
_AGENT = {"type": "string", "minLength": 0, "maxLength": 1000}
_WORKER = _object({"host": _HOST, "session": _REF, "agent": _AGENT})
_EVIDENCE = {"type": "array", "minItems": 1, "items": _REF}
_DEPS = {"type": "array", "items": _object({"work_id": _ID_SPEC, "revision": _VERSION})}
_COMMON = {"id": _ID_SPEC, "goal_id": _ID_SPEC, "expected_version": _VERSION}
_SCHEMA = {
    "status": _object({"goal_id": _ID_SPEC}, []),
    "input": _object({**_COMMON, "text": _TEXT, "source": _object({
        "host": _HOST, "session": _REF, "agent": _AGENT, "message_id": _REF},
        ["host", "session"])}),
    "assess": _object({**_COMMON, "input_id": _ID_SPEC, "clauses": {"type": "array", "minItems": 1,
        "items": _object({"quote": _TEXT, "relation": _TEXT,
            "work_id": _ID_SPEC, "related_input_ids": {"type": "array", "items": _ID_SPEC},
            "reason": _TEXT}, ["quote", "relation", "reason"])},
        "changes": {"type": "array", "items": _object({"op": {"type": "string", "enum": [
            "add", "change", "cancel", "unaffected"]}, "work_id": _ID_SPEC,
            "expected_revision": _VERSION, "title": _TEXT, "request": _TEXT,
            "depends_on": _DEPS, "reason": _TEXT}, ["op", "work_id", "expected_revision", "reason"])}}),
    "assign": _object({**_COMMON, "work_id": _ID_SPEC, "revision": _VERSION,
        "task_id": _ID_SPEC, "worktree": {"type": "string", "format": "absolute-path"},
        "host": _HOST, "session": _REF, "agent": _AGENT, "dependency_versions": _DEPS},
        [*_COMMON, "work_id", "revision", "task_id", "worktree", "host", "session", "dependency_versions"]),
    "check-dispatch": _object({"goal_id": _ID_SPEC, "assignment_id": _ID_SPEC,
                               "expected_version": _VERSION}),
    "begin-dispatch": _object({**_COMMON, "assignment_id": _ID_SPEC}),
    "receipt": _object({**_COMMON, "assignment_id": _ID_SPEC, "outcome": {
        "type": "string", "enum": ["dispatched", "unknown", "not_started", "stopped"]},
        "host_ref": _REF, "worker": _WORKER, "evidence": _EVIDENCE, "reason": _TEXT},
        [*_COMMON, "assignment_id", "outcome", "evidence"]),
    "stop": _object({**_COMMON, "assignment_id": _ID_SPEC, "reason": _TEXT}),
    "result": _object({**_COMMON, "assignment_id": _ID_SPEC, "revision": _VERSION,
        "host_ref": _REF, "head_after": {"type": "string", "format": "commit"},
        "evidence": _EVIDENCE, "decision": {"type": "string", "enum": ["completed", "failed"]},
        "reason": _TEXT}),
    "settle": _object({**_COMMON, "assignment_id": _ID_SPEC, "revision": _VERSION,
        "evidence": _EVIDENCE, "integration": _object({"task_id": _ID_SPEC,
            "worktree": {"type": "string", "format": "absolute-path"},
            "head": {"type": "string", "format": "commit"}})},
        [*_COMMON, "assignment_id", "revision", "evidence"]),
}


def schema():
    """The executable argument schema used by every transition."""
    return {"schema_version": 1, "commands": copy.deepcopy(_SCHEMA)}


def validate(action, data):
    if action not in _SCHEMA:
        raise ValueError(f"unknown coordination action: {action}")
    _validate(data, _SCHEMA[action], action)


def status(conn, data=None):
    data = {} if data is None else data
    validate("status", data)
    return _state_status(conn, data)


def _work(state, work_id):
    try:
        return state["works"][work_id]
    except KeyError as exc:
        raise ValueError(f"unknown work: {work_id}") from exc


def _assignment(state, assignment_id):
    try:
        return state["assignments"][assignment_id]
    except KeyError as exc:
        raise ValueError(f"unknown assignment: {assignment_id}") from exc


def _hold(code, **details):
    return {"kind": "coordination", "code": code, **details}


def _dependency_holds(state, work):
    holds = []
    for dep in work["depends_on"]:
        target = state["works"].get(dep["work_id"])
        if target is None or target["revision"] != dep["revision"]:
            holds.append(_hold("stale_dependency", dependency=dep))
        elif target["status"] != "settled":
            holds.append(_hold("unsettled_dependency", dependency=dep))
    return holds


def _dependency_source_holds(conn, state, work, root):
    """A settled producer's artifact must be present in the actual consumer."""
    holds = []
    for dep in work["depends_on"]:
        target = state["works"].get(dep["work_id"])
        if target is None or target["revision"] != dep["revision"] or target["status"] != "settled":
            continue  # The logical dependency hold already names this cause.
        producer = state["assignments"].get(target.get("assignment_id"))
        settlements = [item for item in producer["settlements"] if item["status"] == "settled"] if producer else []
        if not settlements:
            holds.append(_hold("dependency_source_unknown", dependency=dep))
            continue
        settled = settlements[-1]
        source = settled["source_observation"]
        head = source["head_commit"]
        if (not settled.get("integration_observation") or head != producer["current_result"]["head_after"] or
                source["actual_worktree"] != producer["worktree"]):
            holds.append(_hold("dependency_source_unknown", dependency=dep))
            continue
        try:
            consumer_head = _git(root, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
            present = subprocess.run(["git", "merge-base", "--is-ancestor", head, consumer_head],
                cwd=root, capture_output=True, text=True, env=hashing.worktree_git_env())
            if present.returncode != 0:
                holds.append(_hold("dependency_artifact_missing", dependency=dep,
                    artifact_head=head, artifact_worktree=producer["worktree"], consumer_head=consumer_head))
        except (OSError, subprocess.CalledProcessError) as exc:
            holds.append(_hold("dependency_source_unreadable", dependency=dep, reason=str(exc)))
    return holds


def _current(state, assignment):
    work = _work(state, assignment["work_id"])
    return work["revision"] == assignment["revision"] and work["status"] != "cancelled" and not assignment.get("invalidated")


def _integrated_revision(state, work_id, revision, *, exclude=None):
    return any(a["id"] != exclude and a["work_id"] == work_id and a["revision"] == revision and
               any(item["status"] == "settled" for item in a["settlements"])
               for a in state["assignments"].values())


def _assignment_holds(state, assignment, *, for_ship=False):
    holds = []
    if state["pending_inputs"]:
        holds.append(_hold("pending_input", input_ids=state["pending_inputs"]))
    if not _current(state, assignment):
        holds.append(_hold("obsolete_assignment", assignment_id=assignment["id"]))
    if _integrated_revision(state, assignment["work_id"], assignment["revision"], exclude=assignment["id"]):
        holds.append(_hold("work_revision_already_integrated", assignment_id=assignment["id"]))
    if assignment.get("stop_requested"):
        holds.append(_hold("stop_requested", assignment_id=assignment["id"]))
    if assignment.get("worker_identity_conflicts"):
        holds.append(_hold("worker_identity_conflict", assignment_id=assignment["id"]))
    if _work(state, assignment["work_id"]).get("result_conflicts"):
        holds.append(_hold("result_conflict", assignment_id=assignment["id"]))
    holds.extend(_dependency_holds(state, _work(state, assignment["work_id"])))
    if assignment["dispatch_state"] in ("unknown", "stopped", "not_started"):
        holds.append(_hold("dispatch_" + assignment["dispatch_state"], assignment_id=assignment["id"]))
    if for_ship:
        if worker_identity(assignment) is None:
            holds.append(_hold("worker_identity_required", assignment_id=assignment["id"]))
        if not assignment.get("dispatch_claim"):
            holds.append(_hold("dispatch_claim_required", assignment_id=assignment["id"]))
        if assignment["dispatch_state"] != "dispatched":
            holds.append(_hold("dispatch_required", assignment_id=assignment["id"]))
        result = assignment.get("current_result")
        if result is None or result["decision"] != "completed":
            holds.append(_hold("completed_result_required", assignment_id=assignment["id"]))
    return holds


def task_holds(conn, root, task_id):
    context = task_context(conn, task_id)
    if context is None:
        return []
    state = goal_status(conn, context["goal_id"])
    assignment = _assignment(state, context["assignment_id"])
    holds = _assignment_holds(state, assignment, for_ship=True)
    holds.extend(_dependency_source_holds(conn, state, _work(state, assignment["work_id"]), root))
    if Path(root).resolve() != Path(assignment["worktree"]).resolve():
        holds.append(_hold("wrong_worktree", worktree=assignment["worktree"]))
    result = assignment.get("current_result")
    if result:
        try:
            head = _git(root, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
            if head != result["head_after"]:
                holds.append(_hold("result_source_changed", expected_head=result["head_after"], actual_head=head))
        except (OSError, subprocess.CalledProcessError) as exc:
            holds.append(_hold("result_source_unreadable", reason=str(exc)))
    return holds


def completion_holds(conn, root, task_id):
    """The combined managed-work completion obligations, also used by ship."""
    return task_holds(conn, root, task_id) + design.task_holds(conn, root, task_id)


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                          env=hashing.worktree_git_env(), check=True)


def _require_committed(root, observed):
    """Compare actual source content to Git objects, even when status hides it."""
    root = Path(root).resolve()
    head = observed["head_commit"]
    if head is None or observed["git_reported_status"]["dirty"]:
        raise ValueError("integration requires clean committed source")
    if _git(root, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip() != head:
        raise ValueError("source head changed after its observation")
    entries = []
    for raw in hashing._source_git(root, "ls-tree", "-rz", "--full-tree", head).split(b"\0"):
        if raw:
            meta, path = raw.split(b"\t", 1)
            mode, kind, oid = meta.split(b" ")
            rel = os.fsdecode(path)
            if not hashing._source_output(rel):
                if kind != b"blob" or mode not in (b"100644", b"100755", b"120000"):
                    raise ValueError("unsupported committed source object: " + rel)
                entries.append((rel, mode, oid))
    digest = hashlib.sha256()
    with subprocess.Popen(["git", "cat-file", "--batch"], cwd=root,
                          env=hashing.worktree_git_env(), stdin=subprocess.PIPE,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE) as process:
        for rel, mode, oid in sorted(entries):
            process.stdin.write(oid + b"\n")
            process.stdin.flush()
            header = process.stdout.readline().rstrip(b"\n").split(b" ")
            if len(header) != 3 or header[:2] != [oid, b"blob"]:
                raise ValueError("cannot read committed source blob: " + rel)
            remaining = int(header[2])
            blob_hash, link = hashlib.sha256(), bytearray()
            while remaining:
                chunk = process.stdout.read(min(remaining, 1 << 16))
                if not chunk:
                    raise ValueError("truncated committed source blob: " + rel)
                remaining -= len(chunk)
                blob_hash.update(chunk)
                if mode == b"120000":
                    link.extend(chunk)
            if process.stdout.read(1) != b"\n":
                raise ValueError("invalid committed source blob boundary")
            entry = ([rel, "symlink", 0o111, os.fsdecode(bytes(link))] if mode == b"120000" else
                     [rel, "file", 0o111 if mode == b"100755" else 0, blob_hash.hexdigest()])
            digest.update(json.dumps(entry, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n")
        process.stdin.close()
        if process.wait() != 0:
            raise ValueError("Git could not read committed source objects")
    actual_digest, observed_digest, actual_count = hashlib.sha256(), hashlib.sha256(), 0
    paths = {os.fsdecode(item) for item in hashing._source_git(root, "ls-files", "-z",
        "--cached", "--others", "--exclude-standard").split(b"\0") if item}
    for rel in sorted(paths):
        if hashing._source_output(rel):
            continue
        entry = hashing._source_entry(root, rel)
        if entry is not None:
            observed_digest.update(json.dumps(entry, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n")
            entry[2] = 0o111 if entry[2] else 0  # Git stores executable/nonexecutable, not each x-bit.
            actual_digest.update(json.dumps(entry, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n")
            actual_count += 1
    if (actual_count != observed["product_content"]["entries"] or
            observed_digest.hexdigest() != observed["product_content"]["sha256"]):
        raise ValueError("actual source changed after its observation")
    if len(entries) != actual_count or digest.hexdigest() != actual_digest.hexdigest():
        raise ValueError("actual source content differs from its committed head")


def _deps_unique(deps):
    ids = [item["work_id"] for item in deps]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate dependency work_id")


def _validate_graph(state, declared_ids):
    for work_id, work in state["works"].items():
        if work["status"] == "cancelled":
            continue
        _deps_unique(work["depends_on"])
        for dep in work["depends_on"]:
            target = _work(state, dep["work_id"])
            if dep["work_id"] == work_id or (work_id in declared_ids and (
                    target["status"] == "cancelled" or target["revision"] != dep["revision"])):
                raise ValueError("self, cancelled or stale dependency revision")
    visited, visiting = set(), set()

    def visit(work_id):
        if work_id in visiting:
            raise ValueError("dependency cycle")
        if work_id in visited:
            return
        visiting.add(work_id)
        for dep in state["works"][work_id]["depends_on"]:
            if state["works"][dep["work_id"]]["status"] != "cancelled":
                visit(dep["work_id"])
        visiting.remove(work_id)
        visited.add(work_id)
    for work_id, work in state["works"].items():
        if work["status"] != "cancelled":
            visit(work_id)


def _input(state, data):
    item = {"id": data["id"], "text": data["text"], "source": data["source"],
            "received_version": state["version"] + 1}
    state["inputs"].append(item)
    state["pending_inputs"].append(item["id"])


def _assess(state, data):
    input_id = data["input_id"]
    if input_id not in state["pending_inputs"]:
        raise ValueError("assessment must resolve a pending input")
    if input_id != state["pending_inputs"][0]:
        raise ValueError("assessment must resolve pending inputs in received order")
    inputs = {item["id"]: item for item in state["inputs"]}
    changes = data["changes"]
    by_work = {item["work_id"]: item for item in changes}
    if len(by_work) != len(changes):
        raise ValueError("each work needs exactly one disposition")
    live = {key for key, item in state["works"].items() if item["status"] != "cancelled"}
    if not live <= set(by_work):
        raise ValueError("assessment omits existing live works: " + ", ".join(sorted(live - set(by_work))))
    for clause in data["clauses"]:
        if clause["quote"] not in inputs[input_id]["text"]:
            raise ValueError("clause quote is not contained in the saved input")
        if clause.get("work_id") not in (None, *by_work, *state["works"]):
            raise ValueError("clause names unknown work")
        related = clause.get("related_input_ids", [])
        if len(related) != len(set(related)) or any(key not in inputs for key in related):
            raise ValueError("clause has duplicate or unknown input links")
    intent_version = state["version"] + 1
    invalidated = []
    for change in changes:
        work_id, op = change["work_id"], change["op"]
        if op == "add":
            if work_id in state["works"] or change["expected_revision"] != 0:
                raise ValueError("add needs a new work ID at revision 0")
            if not change.get("title") or not change.get("request"):
                raise ValueError("added work needs title and request")
            state["works"][work_id] = {
                "id": work_id, "revision": 1, "title": change["title"],
                "request": change["request"], "depends_on": change.get("depends_on", []),
                "status": "queued", "artifact_ready": False,
                "goal_version": intent_version, "input_ids": [input_id]}
            continue
        work = _work(state, work_id)
        if work["status"] == "cancelled" or work["revision"] != change["expected_revision"]:
            raise ValueError("work revision is stale or cancelled")
        if op == "unaffected":
            if any(key in change for key in ("title", "request", "depends_on")):
                raise ValueError("unaffected cannot change work content")
            work["goal_version"] = intent_version
            for assignment in state["assignments"].values():
                if assignment["work_id"] == work_id and _current(state, assignment):
                    assignment["goal_version"] = intent_version
            continue
        for assignment in state["assignments"].values():
            if assignment["work_id"] == work_id and not assignment.get("invalidated"):
                assignment["invalidated"] = {"assessment_id": data["id"], "input_id": input_id}
                assignment["status"] = "obsolete"
                invalidated.append(assignment["id"])
        work["revision"] += 1
        work["goal_version"] = intent_version
        work["input_ids"].append(input_id)
        work.pop("assignment_id", None)
        work["status"] = "cancelled" if op == "cancel" else "queued"
        work["artifact_ready"] = False
        work.pop("result_conflicts", None)
        work.pop("worker_identity_conflicts", None)
        if op == "cancel":
            if any(key in change for key in ("title", "request", "depends_on")):
                raise ValueError("cancel cannot rewrite work content")
        else:
            for key in ("title", "request", "depends_on"):
                if key in change:
                    work[key] = change[key]
    _validate_graph(state, {item["work_id"] for item in changes if item["op"] in ("add", "change")})
    state["intent_version"] = intent_version
    state["pending_inputs"].remove(input_id)
    state["assessments"].append({**data, "effective_version": intent_version,
                                  "invalidated_assignments": invalidated})


def _declared_worktree(conn, root, data):
    task_id = data["task_id"]
    if task_id not in ledger.open_task_ids(conn):
        raise ValueError("assignment requires an existing open task")
    path = Path(data["worktree"]).resolve(strict=True)
    actual = Path(_git(path, "rev-parse", "--show-toplevel").stdout.strip()).resolve()
    if actual != path:
        raise ValueError("assignment requires the actual Git worktree root")
    if ledger.ledger_path(path) != ledger.ledger_path(Path(root)):
        raise ValueError("assignment worktree must use the same shared ledger")
    database = next(row[2] for row in conn.execute("PRAGMA database_list") if row[1] == "main")
    if not database or Path(database).resolve() != ledger.ledger_path(path).resolve():
        raise ValueError("assignment worktree does not own this connection's ledger")
    declared = task_provenance.worktree_of(conn, task_id)
    if declared is None or Path(declared).resolve() != path:
        raise ValueError("task was not opened in the declared worktree")
    return str(path)


def _assign(conn, root, state, data):
    if state["pending_inputs"]:
        raise ValueError("pending input fences assignment")
    work = _work(state, data["work_id"])
    if work["revision"] != data["revision"] or work["status"] in ("cancelled", "settled"):
        raise ValueError("assignment needs current queued work revision")
    if _integrated_revision(state, work["id"], data["revision"]):
        raise ValueError("this work revision was integrated; new work requires a new assessment revision")
    if work.get("result_conflicts"):
        raise ValueError("conflicting completion evidence requires a new assessment/revision")
    if work.get("worker_identity_conflicts"):
        raise ValueError("conflicting worker identity requires a new assessment/revision")
    _deps_unique(data["dependency_versions"])
    if sorted(data["dependency_versions"], key=lambda x: x["work_id"]) != sorted(work["depends_on"], key=lambda x: x["work_id"]):
        raise ValueError("assignment dependency versions must exactly match the work")
    if _dependency_holds(state, work):
        raise ValueError("assignment requires current settled dependencies")
    path = _declared_worktree(conn, root, data)
    source_holds = _dependency_source_holds(conn, state, work, path)
    if source_holds:
        raise ValueError("assignment dependency source held: " + json.dumps(source_holds))
    for other in status(conn)["goals"].values():
        for assignment in other["assignments"].values():
            same_target = (assignment["task_id"] == data["task_id"] or
                           Path(assignment["worktree"]).resolve() == Path(path) or
                           (other["goal_id"] == state["goal_id"] and assignment["work_id"] == work["id"]))
            if same_target and _owns(assignment):
                raise ValueError("target has an unresolved assignment owner: " + assignment["id"])
    assignment = {**data, "worktree": path, "goal_version": work["goal_version"],
                  "worker_identity": ({key: data[key] for key in ("host", "session", "agent")}
                                      if "agent" in data else None),
                  "dispatch_state": "prepared", "status": "prepared", "receipts": [],
                  "results": [], "stop_requested": None, "settlements": [],
                  "created_event": conn.execute("SELECT coalesce(max(id),0)+1 FROM event").fetchone()[0]}
    state["assignments"][data["id"]] = assignment
    work["assignment_id"] = data["id"]
    work["status"] = "assigned"


def _dispatch_holds(conn, root, state, assignment, expected_version):
    holds = _assignment_holds(state, assignment)
    try:
        _declared_worktree(conn, root or assignment["worktree"], assignment)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        holds.append(_hold("assignment_coordinates_changed", reason=str(exc)))
    holds.extend(_dependency_source_holds(conn, state, _work(state, assignment["work_id"]), assignment["worktree"]))
    if expected_version != state["version"]:
        holds.append(_hold("stale_version", expected=expected_version, current=state["version"]))
    if assignment["dispatch_state"] != "prepared":
        holds.append(_hold("already_observed", state=assignment["dispatch_state"]))
    return holds


def check_dispatch(conn, data, root=None):
    validate("check-dispatch", data)
    state = goal_status(conn, data["goal_id"])
    assignment = _assignment(state, data["assignment_id"])
    holds = _dispatch_holds(conn, root, state, assignment, data["expected_version"])
    return {"goal_id": state["goal_id"], "version": state["version"],
            "assignment_id": assignment["id"], "ready": not holds, "holds": holds,
            "launch_authorized": False}


def _begin_dispatch(conn, root, state, data):
    assignment = _assignment(state, data["assignment_id"])
    holds = _dispatch_holds(conn, root, state, assignment, data["expected_version"])
    if holds:
        raise ValueError("dispatch claim held: " + json.dumps(holds))
    assignment["dispatch_claim"] = {"id": data["id"], "claimed_version": state["version"] + 1}
    assignment["dispatch_state"] = "dispatching"
    assignment["status"] = "dispatching"


def _observation(state, assignment, data):
    current = _current(state, assignment)
    return {**data, "observed_version": data["expected_version"],
            "current_version": state["version"],
            "stale": data["expected_version"] != state["version"],
            "classification": ("settled_observation" if assignment["status"] == "settled" else
                "current" if current and not assignment.get("stop_requested") else "obsolete_unresolved")}


def _receipt(state, data):
    assignment = _assignment(state, data["assignment_id"])
    outcome = data["outcome"]
    if outcome in ("dispatched", "stopped") and not data.get("host_ref"):
        raise ValueError("dispatched/stopped receipt requires an actual host_ref")
    if outcome != "not_started" and not assignment.get("dispatch_claim"):
        raise ValueError("actual dispatch observations require a prior begin-dispatch claim")
    previous = assignment["dispatch_state"]
    item = _observation(state, assignment, data)
    known_worker = worker_identity(assignment)
    observed_worker = data.get("worker")
    if (outcome in ("dispatched", "stopped") and known_worker is not None and
            observed_worker is not None and known_worker != observed_worker):
        item.update(applied=False, previous_dispatch_state=previous, classification="worker_identity_conflict")
        assignment["receipts"].append(item)
        assignment.setdefault("worker_identity_conflicts", []).append({"receipt_id": data["id"], "worker": observed_worker})
        if _current(state, assignment):
            work = _work(state, assignment["work_id"])
            work.setdefault("worker_identity_conflicts", []).append(data["id"])
            if work["status"] == "settled":
                work["status"] = "identity_conflict"
        return
    if outcome == "dispatched" and known_worker is None and observed_worker is None:
        raise ValueError("dispatched observation requires the actual unknown worker identity")
    if assignment.get("host_ref") and data.get("host_ref") and data["host_ref"] != assignment["host_ref"]:
        raise ValueError("host_ref differs from the observed worker")
    if outcome == "dispatched" and known_worker is None and observed_worker is not None:
        assignment["worker_identity"] = copy.deepcopy(observed_worker)
    # Receipts are past observations, never requests to launch. A delayed or
    # weaker observation is retained without reversing authoritative closure.
    advances = {"prepared": {"not_started"},
                "dispatching": {"unknown", "dispatched", "stopped", "not_started"},
                "unknown": {"dispatched", "stopped", "not_started"},
                "dispatched": {"stopped"}, "stopped": set(), "not_started": set()}
    applied = outcome in advances[previous]
    item.update(applied=applied, previous_dispatch_state=previous)
    if not applied:
        item["classification"] = "historical_after_" + previous
    assignment["receipts"].append(item)
    if not applied:
        return
    assignment["dispatch_state"] = outcome
    if data.get("host_ref"):
        assignment["host_ref"] = data["host_ref"]
    if (_current(state, assignment) and assignment["status"] != "settled" and
            not _work(state, assignment["work_id"]).get("result_conflicts")):
        assignment["status"] = "stop_requested" if assignment.get("stop_requested") else outcome
    # Observations never clear a stop request or resurrect obsolete work.


def _stop(state, data):
    assignment = _assignment(state, data["assignment_id"])
    if assignment["status"] == "settled":
        raise ValueError("settled assignment cannot be stopped")
    assignment["stop_requested"] = {"id": data["id"], "reason": data["reason"]}
    if _current(state, assignment):
        assignment["status"] = "stop_requested"


def _result(root, state, data):
    assignment = _assignment(state, data["assignment_id"])
    if not assignment.get("dispatch_claim"):
        raise ValueError("worker result requires a prior begin-dispatch claim")
    if worker_identity(assignment) is None or assignment.get("worker_identity_conflicts"):
        raise ValueError("worker result held until actual worker identity is known without conflicts")
    if data["revision"] != assignment["revision"]:
        raise ValueError("result revision must name its original assignment revision")
    if assignment.get("host_ref") and data["host_ref"] != assignment["host_ref"]:
        raise ValueError("result host_ref differs from the observed worker")
    # Resolve an actual object, without using user data as a revision option.
    resolved = _git(root, "rev-parse", "--verify", data["head_after"] + "^{commit}").stdout.strip()
    if resolved != data["head_after"]:
        raise ValueError("result head_after is not an actual full commit ID")
    item = _observation(state, assignment, data)
    if _work(state, assignment["work_id"]).get("result_conflicts"):
        item["classification"] = "conflicting_settled_result"
    if (assignment["status"] == "settled" and _current(state, assignment) and (
            data["decision"] != "completed" or data["head_after"] != assignment["current_result"]["head_after"])):
        item["classification"] = "conflicting_settled_result"
        assignment["status"] = "conflicting_result"
        work = _work(state, assignment["work_id"])
        work["status"] = "conflicting_result"
        work.setdefault("result_conflicts", []).append(data["id"])
    assignment["results"].append(item)
    if item["classification"] == "current" and assignment["dispatch_state"] == "dispatched":
        assignment["current_result"] = item
        assignment["status"] = "completed_candidate" if data["decision"] == "completed" else "failed"


def _completion(conn, assignment):
    history = task_provenance.completion_history(conn, assignment["task_id"])
    if not history:
        raise ValueError("settlement requires actual lifecycle completion")
    source = history[-1]["source_observation"]
    result = assignment.get("current_result")
    if result is None or result["decision"] != "completed":
        raise ValueError("settlement requires a current completed result")
    if (not isinstance(source, dict) or source.get("actual_worktree") != assignment["worktree"] or
            source.get("assigned_worktree") != assignment["worktree"] or
            source.get("head_commit") != result["head_after"]):
        raise ValueError("lifecycle source provenance does not match assignment/result")
    task = ledger.require_task(conn, assignment["task_id"])
    if source.get("original_base_commit") != task["base_commit"]:
        raise ValueError("lifecycle original base provenance differs")
    current = hashing.source_observation(assignment["worktree"])
    if any(source.get(key) != value for key, value in current.items()):
        raise ValueError("completed task source has changed since lifecycle completion")
    return history[-1]


def _integration(conn, root, assignment, target, artifact):
    _require_committed(assignment["worktree"], artifact["source_observation"])
    if target["task_id"] == assignment["task_id"]:
        raise ValueError("integration target must use a different task from the source artifact")
    path = Path(target["worktree"]).resolve(strict=True)
    if path.samefile(assignment["worktree"]):
        raise ValueError("integration target must use a different Git worktree from the source artifact")
    if ledger.ledger_path(path) != ledger.ledger_path(Path(root)):
        raise ValueError("integration target must use the same shared ledger")
    if Path(_git(path, "rev-parse", "--show-toplevel").stdout.strip()).resolve() != path:
        raise ValueError("integration target must be the actual Git worktree root")
    if task_provenance.worktree_of(conn, target["task_id"]) != str(path):
        raise ValueError("integration task belongs to a different worktree")
    observed = hashing.source_observation(path)
    if observed["head_commit"] != target["head"] or observed["git_reported_status"]["dirty"]:
        raise ValueError("integration target must match the declared head and have no unfinished source edits")
    _require_committed(path, observed)
    history = task_provenance.completion_history(conn, target["task_id"])
    if not history:
        raise ValueError("integration target requires actual lifecycle completion")
    holds = completion_holds(conn, path, target["task_id"])
    if holds:
        raise hashing.SourceUnreadable("completion held: " + json.dumps(holds, ensure_ascii=False))
    source = history[-1]["source_observation"]
    task = ledger.require_task(conn, target["task_id"])
    if (not isinstance(source, dict) or source.get("assigned_worktree") != str(path) or
            source.get("original_base_commit") != task["base_commit"] or
            any(source.get(key) != value for key, value in observed.items())):
        raise ValueError("integration completion is stale or has different source provenance")
    ancestor = subprocess.run(["git", "merge-base", "--is-ancestor",
                              assignment["current_result"]["head_after"], target["head"]],
                              cwd=path, capture_output=True, text=True, env=hashing.worktree_git_env())
    if ancestor.returncode != 0:
        raise ValueError("integration target does not contain artifact commit ancestry")
    return {**target, "completion_event_id": history[-1]["event_id"], "source_observation": observed}


def _settle(conn, root, state, data):
    assignment = _assignment(state, data["assignment_id"])
    if data["revision"] != assignment["revision"]:
        raise ValueError("settlement revision differs from assignment")
    holds = _assignment_holds(state, assignment, for_ship=True)
    holds.extend(_dependency_source_holds(conn, state, _work(state, assignment["work_id"]), assignment["worktree"]))
    if holds:
        raise ValueError("settlement held: " + ", ".join(item["code"] for item in holds))
    history = _completion(conn, assignment)
    doc_holds = design.task_holds(conn, Path(assignment["worktree"]), assignment["task_id"])
    if doc_holds:
        raise ValueError("settlement held by design: " + json.dumps(doc_holds))
    integration = _integration(conn, root, assignment, data["integration"], history) if "integration" in data else None
    status_name = "settled" if integration else "waiting_integration"
    already_integrated = any(item["status"] == "settled" for item in assignment["settlements"])
    effective_status = "settled" if already_integrated else status_name
    assignment["settlements"].append({**data, "status": status_name,
        "effective_status": effective_status, "applied": bool(integration) or not already_integrated,
        "artifact_ready": True, "completion_event_id": history["event_id"],
        "source_observation": history["source_observation"], "integration_observation": integration})
    assignment["artifact_ready"] = True
    assignment["status"] = effective_status
    work = _work(state, assignment["work_id"])
    work["status"] = effective_status
    work["artifact_ready"] = True


def transition(conn, root, action, data):
    """Validate/CAS/append exactly one event while holding the shared write lock."""
    if action in ("status", "check-dispatch"):
        return status(conn, data) if action == "status" else check_dispatch(conn, data, root)
    validate(action, data)
    # Mask prose before reference checks, while preserving every coordinate
    # used for replay, source attribution, ownership and host reconciliation.
    saved = json.loads(ledger._redact(json.dumps(data, ensure_ascii=False), Path(root)))
    validate(action, saved)
    if _coordinates(saved) != _coordinates(data):
        raise ValueError("redaction would change stable coordination identity")
    raw = json.dumps({"action": action, "data": data}, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for event_id, payload in _events(conn):
            if payload["id"] == data["id"]:
                if payload["request_sha256"] != digest:
                    raise ValueError("transition id was already used with a different payload")
                conn.rollback()
                return {**payload["state"], "id": data["id"], "action": action,
                        "event_id": event_id, "replayed": True, "launch_authorized": False}
        state = goal_status(conn, data["goal_id"])
        if data["expected_version"] > state["version"]:
            raise ValueError("observation/update refers to a future goal version")
        if data["expected_version"] != state["version"] and action not in ("receipt", "result"):
            raise ValueError(f"stale goal version: expected {data['expected_version']}, current {state['version']}")
        if action == "input":
            _input(state, saved)
        elif action == "assess":
            _assess(state, saved)
        elif action == "assign":
            _assign(conn, root, state, saved)
        elif action == "begin-dispatch":
            _begin_dispatch(conn, root, state, saved)
        elif action == "receipt":
            _receipt(state, saved)
        elif action == "stop":
            _stop(state, saved)
        elif action == "result":
            _result(root, state, saved)
        elif action == "settle":
            _settle(conn, root, state, saved)
        else:
            raise ValueError("not a mutation")
        state["version"] += 1
        payload = {"id": data["id"], "goal_id": data["goal_id"], "action": action,
                   "request_sha256": digest, "data": saved, "state": state}
        task_id = saved.get("task_id")
        if "assignment_id" in saved:
            task_id = _assignment(state, saved["assignment_id"])["task_id"]
        event_id = ledger.insert(conn, "event", task_id=task_id, claim_id=None, kind=EVENT,
                                 actor="orchestrator", payload=payload,
                                 created_at=datetime.now(timezone.utc).isoformat())
        return {**state, "id": data["id"], "action": action, "event_id": event_id,
                "replayed": False, "launch_authorized": action == "begin-dispatch"}
    except BaseException:
        conn.rollback()
        raise


def input_request(conn, root, data):
    return transition(conn, root, "input", data)


def assess(conn, root, data):
    return transition(conn, root, "assess", data)


def assign(conn, root, data):
    return transition(conn, root, "assign", data)


def begin_dispatch(conn, root, data):
    return transition(conn, root, "begin-dispatch", data)


def receipt(conn, root, data):
    return transition(conn, root, "receipt", data)


def stop(conn, root, data):
    return transition(conn, root, "stop", data)


def result(conn, root, data):
    return transition(conn, root, "result", data)


def settle(conn, root, data):
    return transition(conn, root, "settle", data)
