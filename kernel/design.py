"""Versioned, content-bound project-document bookkeeping for managed work.

The host authors documents and performs semantic review. The ledger records
which existing document was selected and fences missing or stale evidence; it
neither writes product documents nor authenticates an author/reviewer identity.
"""
import copy
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from . import coordination_state as coordination
from . import hashing, ledger, scope
from .analysis.subject_files import matches

EVENT_KINDS = ("design_registered", "design_disposition", "design_reviewed")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _object(properties, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else required,
            "additionalProperties": False}


_ID_SPEC = {"type": "string", "format": "identifier", "minLength": 1, "maxLength": 200}
_TEXT = {"type": "string", "minLength": 1, "maxLength": 100000}
_REF = {"type": "string", "minLength": 1, "maxLength": 2000}
_REV = {"type": "integer", "minimum": 0}
_PATH = {"type": "string", "format": "relative-path", "minLength": 1, "maxLength": 2000}
_IDENTITY = _object({"host": {"type": "string", "enum": ["claude", "codex"]},
                     "session": _REF, "agent": {"type": "string", "minLength": 0, "maxLength": 2000}},
                    ["host", "session"])
_INTENT_REF = _object({"input_id": _ID_SPEC, "quote": _TEXT})
_EVIDENCE = {"type": "array", "minItems": 1, "items": _REF}
_SCHEMA = {
    "status": _object({}, []),
    "register": _object({"id": _ID_SPEC, "design_id": _ID_SPEC,
        "expected_design_revision": _REV, "path": _PATH,
        "coverage": {"type": "array", "minItems": 1, "items": _object({"path": _PATH, "reference": _REF})},
        "basis": {"type": "string", "enum": ["approved_intent", "observed_baseline"]},
        "intent_refs": {"type": "array", "minItems": 1, "items": _INTENT_REF},
        "author": _IDENTITY, "evidence": _EVIDENCE}),
    "disposition": _object({"id": _ID_SPEC, "goal_id": _ID_SPEC, "work_id": _ID_SPEC,
        "revision": _REV, "disposition": {"type": "string", "enum": ["required", "not_applicable"]},
        "design_ids": {"type": "array", "minItems": 1, "items": _ID_SPEC},
        "coverage": {"type": "array", "minItems": 1, "items": _object({"design_id": _ID_SPEC, "reference": _REF})},
        "reason": _TEXT, "author": _IDENTITY},
        ["id", "goal_id", "work_id", "revision", "disposition", "reason", "author"]),
    "review": _object({"id": _ID_SPEC, "goal_id": _ID_SPEC, "work_id": _ID_SPEC,
        "revision": _REV, "design_id": _ID_SPEC, "design_revision": _REV,
        "content_sha256": {"type": "string", "format": "sha256", "minLength": 64, "maxLength": 64},
        "reviewer": _IDENTITY,
        "result": {"type": "string", "enum": ["approved", "needs_changes", "not_evaluable"]},
        "coverage": {"type": "array", "minItems": 1, "items": _object({
            "reference": _REF, "intent_ref": _INTENT_REF, "evidence": _EVIDENCE})},
        "reason": _TEXT, "evidence": _EVIDENCE}),
}


def schema():
    """Executable JSON argument schema; prose does not duplicate its fields."""
    return {"schema_version": 1, "commands": copy.deepcopy(_SCHEMA)}


def _validate(value, spec, where):
    coordination._validate(value, spec, where)
    _validate_format(value, spec, where)


def _validate_format(value, spec, where):
    if isinstance(value, dict):
        for key, child in value.items():
            _validate_format(child, spec["properties"][key], where + "." + key)
    elif isinstance(value, list):
        for child in value:
            _validate_format(child, spec["items"], where)
    elif spec.get("format") == "relative-path":
        path = PurePosixPath(value)
        if (path.is_absolute() or "\\" in value or any(p in ("", ".", "..", ".git") for p in value.split("/"))
                or any(ord(c) < 32 for c in value) or str(path) != value):
            raise ValueError(f"{where} must be a normalized repository-relative path")
    elif spec.get("format") == "sha256" and not _SHA.fullmatch(value):
        raise ValueError(f"{where} must be a sha256 digest")


def validate(action, data):
    if action not in _SCHEMA:
        raise ValueError("unknown design action: " + action)
    _validate(data, _SCHEMA[action], action)


def _events(conn):
    for row in conn.execute("SELECT kind,payload FROM event WHERE kind IN (?,?,?) ORDER BY id", EVENT_KINDS):
        yield row["kind"], json.loads(row["payload"])


def _records(conn):
    designs, dispositions, reviews = {}, {}, []
    for kind, payload in _events(conn):
        item = payload["record"]
        if kind == "design_registered":
            designs[item["design_id"]] = item
        elif kind == "design_disposition":
            dispositions[(item["goal_id"], item["work_id"], item["revision"])] = item
        else:
            reviews.append(item)
    return designs, dispositions, reviews


def _path(root, rel):
    root = Path(root).resolve()
    path = root / rel
    for part in (path, *path.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError("design path must not traverse a symlink: " + rel)
    if not path.resolve().is_relative_to(root):
        raise ValueError("design path escapes repository: " + rel)
    if path.exists() and not path.is_file():
        raise ValueError("design path must be a regular file: " + rel)
    return path


def _document(root, rel):
    path = _path(root, rel)
    if not path.exists():
        return None, None
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("design changed during read: " + rel)
    try:
        text = raw.decode("utf-8")
    except UnicodeError as exc:
        raise ValueError("design document must be UTF-8 text: " + rel) from exc
    return hashlib.sha256(raw).hexdigest(), text


def snapshot(conn, root):
    """Registered versions and live file status, without a whole-project audit."""
    designs, dispositions, reviews = _records(conn)
    live = {}
    for key, item in designs.items():
        current = copy.deepcopy(item)
        try:
            sha, _ = _document(root, item["path"])
            current["live_content_sha256"] = sha
            current["live_status"] = ("needs_initialization" if sha is None else
                "current" if sha == item["content_sha256"] else "content_changed")
        except (OSError, ValueError) as exc:
            current.update(live_status="unreadable", error=str(exc))
        live[key] = current
    return {"designs": live, "dispositions": list(dispositions.values()), "reviews": reviews,
            "identity_limit": "host-supplied attribution; not authenticated identity or semantic proof"}


def _hash(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _intent_sources(conn, refs, state=None):
    states = [state] if state is not None else list(coordination.status(conn)["goals"].values())
    sources = {}
    for goal in states:
        for item in goal["inputs"]:
            sources.setdefault(item["id"], []).append(item["text"])
    for ref in refs:
        values = sources.get(ref["input_id"], [])
        if len(values) != 1 or ref["quote"] not in values[0]:
            raise ValueError("intent_ref must quote one recorded original input: " + ref["input_id"])


def _work(conn, data):
    state = coordination.goal_status(conn, data["goal_id"])
    work = state["works"].get(data["work_id"])
    if work is None or work["revision"] != data["revision"] or work["status"] == "cancelled":
        raise ValueError("design operation requires a current live work revision")
    if state["pending_inputs"]:
        raise ValueError("pending request input must be assessed before design decisions")
    return state, work


def _work_sources(state, work):
    input_ids = set(work["input_ids"])
    return [{"input_id": item["id"], "quote": item["text"]}
            for item in state["inputs"] if item["id"] in input_ids]


def _work_binding(state, work):
    # Per-work intent/dependency versions, rather than goal's changing event
    # counter: a receipt or an unrelated addition cannot expire this document.
    return {"revision": work["revision"], "input_ids": work["input_ids"],
            "request": work["request"], "depends_on": work["depends_on"]}


def source_snapshot(conn, root, task_id):
    """Read scoped disk content, modes and symlinks, even if Git hides edits."""
    root = Path(root).resolve()
    globs = scope.current_scope(conn, task_id)
    paths = {os.fsdecode(p) for p in hashing._source_git(root, "ls-files", "-z", "--cached",
                 "--others", "--exclude-standard").split(b"\0") if p}
    entries = []
    for rel in sorted(paths):
        if matches(rel, globs) and not hashing._source_output(rel):
            item = hashing._source_entry(root, rel)
            if item is not None:
                entries.append(item)
    return {"scheme": "v4-design-task-source-1", "worktree": str(root), "scope": globs,
            "sha256": _hash(entries), "entries": len(entries)}


def _mutation(conn, root, action, data, build):
    validate(action, data)
    # Mask free prose, but never silently rewrite a source identity, selected
    # path or stable reference and then return it as the requested ownership.
    saved = json.loads(ledger._redact(json.dumps(data, ensure_ascii=False), Path(root)))
    validate(action, saved)
    if coordination._coordinates(saved) != coordination._coordinates(data):
        raise ValueError("redaction would change stable design identity")
    operation_hash = _hash(data)
    conn.execute("BEGIN IMMEDIATE")
    try:
        for _, payload in _events(conn):
            if payload["operation_id"] == data["id"]:
                if payload["operation_sha256"] != operation_hash:
                    raise ValueError("design operation id already used with different data")
                conn.rollback()
                return payload["record"]
        record, task_id = build(saved)
        ledger.insert(conn, "event", kind={"register": "design_registered", "disposition": "design_disposition",
            "review": "design_reviewed"}[action], actor="reviewer" if action == "review" else "orchestrator",
            task_id=task_id, payload={"operation_id": data["id"], "operation_sha256": operation_hash,
                                     "record": record}, created_at=datetime.now(timezone.utc).isoformat())
        return record
    except Exception:
        conn.rollback()
        raise


def register(conn, root, data):
    """Select/revise a document; a missing path records initialization work."""
    def build(data):
        designs, _, _ = _records(conn)
        old = designs.get(data["design_id"])
        expected = old["design_revision"] if old else 0
        if data["expected_design_revision"] != expected:
            raise ValueError("stale expected_design_revision")
        if old and old["path"] != data["path"]:
            raise ValueError("reuse the selected design path; do not silently replace its owner")
        _intent_sources(conn, data["intent_refs"])
        if len({_hash(ref) for ref in data["intent_refs"]}) != len(data["intent_refs"]):
            raise ValueError("duplicate intent_ref")
        for item in data["coverage"]:
            _path(root, item["path"])
        if len({_hash(item) for item in data["coverage"]}) != len(data["coverage"]):
            raise ValueError("duplicate design coverage")
        sha, _ = _document(root, data["path"])
        return {**copy.deepcopy(data), "design_revision": expected + 1,
                "content_sha256": sha, "status": "needs_initialization" if sha is None else "registered"}, None
    return _mutation(conn, root, "register", data, build)


def disposition(conn, root, data):
    """Record required affected sections or explicit source-bound N/A."""
    def build(data):
        state, work = _work(conn, data)
        refs = _work_sources(state, work)
        if not refs:
            raise ValueError("work has no recorded source input")
        if data["disposition"] == "required":
            designs, _, _ = _records(conn)
            ids, coverage = data.get("design_ids", []), data.get("coverage", [])
            if not ids or len(set(ids)) != len(ids) or set(ids) != {c["design_id"] for c in coverage}:
                raise ValueError("required disposition needs unique design_ids and coverage for each")
            if len({_hash(item) for item in coverage}) != len(coverage):
                raise ValueError("duplicate disposition coverage")
            for item in coverage:
                design = designs.get(item["design_id"])
                if design is None or item["reference"] not in {c["reference"] for c in design["coverage"]}:
                    raise ValueError("disposition must reference registered design coverage")
                _intent_sources(conn, design["intent_refs"], state)
                if not any(ref["input_id"] in work["input_ids"] for ref in design["intent_refs"]):
                    raise ValueError("required design must reference this work's original inputs")
        elif data.get("design_ids") or data.get("coverage"):
            raise ValueError("not_applicable must not name required designs")
        return {**copy.deepcopy(data), "intent_refs": refs, "work_binding": _work_binding(state, work)}, None
    return _mutation(conn, root, "disposition", data, build)


def _identity(identity):
    return identity["host"], identity["session"], identity.get("agent", "")


def review(conn, root, data):
    """Record independent questions and evidence at current content/source."""
    def build(data):
        state, work = _work(conn, data)
        designs, dispositions, _ = _records(conn)
        design = designs.get(data["design_id"])
        if design is None or design["design_revision"] != data["design_revision"]:
            raise ValueError("review requires current design revision")
        sha, _ = _document(root, design["path"])
        if sha is None or sha != design["content_sha256"] or sha != data["content_sha256"]:
            raise ValueError("review requires initialized, registered current document content")
        disp = dispositions.get((data["goal_id"], data["work_id"], data["revision"]))
        if disp is None or disp["disposition"] != "required" or data["design_id"] not in disp["design_ids"]:
            raise ValueError("review needs a required current work disposition")
        if disp["work_binding"] != _work_binding(state, work):
            raise ValueError("stale disposition source")
        if _identity(data["reviewer"]) == _identity(design["author"]):
            raise ValueError("document reviewer must differ from document author")
        assignments = [a for a in state["assignments"].values()
                       if a["work_id"] == work["id"] and a["revision"] == work["revision"] and not a.get("invalidated")]
        current = [a for a in assignments if a["id"] == work.get("assignment_id")]
        if len(current) != 1:
            raise ValueError("design review needs one current task assignment")
        assignment = current[0]
        if Path(root).resolve() != Path(assignment["worktree"]).resolve():
            raise ValueError("review must observe the assigned task worktree")
        workers = [coordination.worker_identity(a) for a in assignments]
        worker = coordination.worker_identity(assignment)
        if worker is None:
            raise ValueError("design review requires the actual assigned worker identity")
        if assignment.get("worker_identity_conflicts"):
            raise ValueError("design review held by conflicting worker identity observations")
        if any(w is not None and _identity(data["reviewer"]) == _identity(w) for w in workers):
            raise ValueError("document reviewer must differ from the assigned worker")
        expected = {c["reference"] for c in disp["coverage"] if c["design_id"] == data["design_id"]}
        if {c["reference"] for c in data["coverage"]} != expected:
            raise ValueError("review must account for each affected coverage reference")
        for item in data["coverage"]:
            if (item["intent_ref"] not in design["intent_refs"]
                    or item["intent_ref"]["input_id"] not in work["input_ids"]):
                raise ValueError("review must match registered original intent")
        _intent_sources(conn, [c["intent_ref"] for c in data["coverage"]], state)
        observed = source_snapshot(conn, root, assignment["task_id"])
        return {**copy.deepcopy(data), "basis": design["basis"], "task_id": assignment["task_id"],
                "assignment_id": assignment["id"], "disposition_id": disp["id"],
                "worker_identity": worker, "work_binding": _work_binding(state, work),
                "source_snapshot": observed}, assignment["task_id"]
    return _mutation(conn, root, "review", data, build)


def task_holds(conn, root, task_id):
    """Structured ship holds; unmanaged historical tasks are left untouched."""
    context = coordination.task_context(conn, task_id)
    if context is None:
        return []
    state = coordination.goal_status(conn, context["goal_id"])
    work = state["works"][context["work_id"]]
    designs, dispositions, reviews = _records(conn)
    holds = []
    def hold(code, **details):
        holds.append({"kind": "design", "code": code, **details})
    assignment = state["assignments"][context["assignment_id"]]
    worker = coordination.worker_identity(assignment)
    if worker is None:
        hold("worker_identity_required", assignment_id=assignment["id"])
        return holds
    if assignment.get("worker_identity_conflicts"):
        hold("worker_identity_conflict", assignment_id=assignment["id"])
        return holds
    disp = dispositions.get((context["goal_id"], context["work_id"], work["revision"]))
    if disp is None:
        hold("disposition_required", work_id=work["id"], revision=work["revision"])
        return holds
    if disp["work_binding"] != _work_binding(state, work):
        hold("stale_disposition")
        return holds
    if disp["disposition"] == "not_applicable":
        return holds
    try:
        observed = source_snapshot(conn, root, task_id)
    except (OSError, ValueError, hashing.SourceUnreadable) as exc:
        hold("source_unreadable", reason=str(exc))
        return holds
    for design_id in disp["design_ids"]:
        design = designs.get(design_id)
        if design is None:
            hold("design_missing", design_id=design_id)
            continue
        try:
            sha, _ = _document(root, design["path"])
        except (OSError, ValueError) as exc:
            hold("document_unreadable", design_id=design_id, reason=str(exc))
            continue
        if sha is None or design["content_sha256"] is None:
            hold("needs_initialization", design_id=design_id)
            continue
        if sha != design["content_sha256"]:
            hold("content_changed", design_id=design_id)
            continue
        candidates = [r for r in reviews if r["goal_id"] == context["goal_id"] and r["work_id"] == work["id"]
                      and r["revision"] == work["revision"] and r["design_id"] == design_id]
        if not candidates:
            hold("review_required", design_id=design_id)
            continue
        latest = candidates[-1]
        if (latest["design_revision"] != design["design_revision"] or latest["content_sha256"] != sha
                or latest["disposition_id"] != disp["id"] or latest["assignment_id"] != context["assignment_id"]
                or latest.get("worker_identity") != worker
                or latest["work_binding"] != _work_binding(state, work)):
            hold("stale_review", design_id=design_id)
        elif latest["source_snapshot"] != observed:
            hold("source_changed", design_id=design_id)
        elif latest["result"] != "approved":
            hold("approval_required", design_id=design_id, result=latest["result"])
    return holds
