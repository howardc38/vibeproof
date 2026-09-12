"""Bounded maintenance coordination. Claims stay in the existing ledger/state."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import tempfile
import uuid

from . import attention, config, hashing, hosts, ledger, lifecycle, review, state

RUN = "maintenance_run"
HANDOFF = "maintenance_handoff"
DECISIONS = ("needs_fix", "needs_evidence", "needs_checker", "finding_disputed",
             "needs_user_decision", "risk_recommended")
RESULTS = ("completed", "not_applicable", "not_evaluable", "failed")
ROLES = ("monitor", "reviewer", "task-splitter", "worker", "checker-author")
REPORT_DECISIONS = (*DECISIONS, "work_completed", "work_failed", "review_completed", "review_failed", "plan_completed", "plan_failed", "no_findings")
DEFAULT_LIMITS = {"max_repair_attempts": 3}
LIMIT_KEYS = frozenset((*DEFAULT_LIMITS, "max_parallel_agents", "max_run_seconds"))
HANDOFF_FIELDS = {
    "prepare": {"request", "acceptance", "evidence"},
    "dispatched": {"host_ref", "evidence"},
    "check_dispatch": set(),
    "dispatch_unknown": {"reason", "evidence"},
    "result": {"decision", "reason", "evidence", "acceptance", "reviewed_head", "head_after", "findings"},
    "link_repair": {"reason"}, "settle": set(), "cancel": {"reason"},
}


def validate_limits(limits):
    if not isinstance(limits, dict) or set(limits) - LIMIT_KEYS:
        raise ValueError("limits must be an object using: " + ", ".join(sorted(LIMIT_KEYS)))
    if any(not isinstance(v, int) or isinstance(v, bool) or v < 1 for v in limits.values()):
        raise ValueError("limits must be positive integers")
    return {**DEFAULT_LIMITS, **limits}


def schema():
    return {"setup": {"mode": ["review", "repair"], "repair_scope": "array of authorized path globs",
                      "limits": {"supported": sorted(LIMIT_KEYS), "defaults": DEFAULT_LIMITS}},
            "handoff": {"identity": ["id", "run_id", "action", "claim", "task", "role"],
                        "roles": list(ROLES),
                        "actions": {k: sorted(v) for k, v in HANDOFF_FIELDS.items()},
                        "decisions": list(REPORT_DECISIONS)}}


def now():
    return datetime.now(timezone.utc).isoformat()


def private_dir(root):
    return ledger.ledger_path(Path(root).resolve()).parent


def settings(root):
    p = private_dir(root) / "maintenance.json"
    return json.loads(p.read_text()) if p.is_file() else {"mode": "review", "repair_scope": [], "schedules": {}}


@contextmanager
def exclusive(root):
    """Short, local cross-process transaction; a run's ownership is an event."""
    import fcntl
    directory = private_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "maintenance.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _write_settings(root, value):
    directory = private_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=directory, prefix="maintenance-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, directory / "maintenance.json")
    finally:
        if Path(name).exists():
            Path(name).unlink()


def configure(root, *, mode="review", repair_scope=(), limits=None):
    if mode not in ("review", "repair"):
        raise ValueError("mode must be review or repair")
    if not isinstance(repair_scope, (list, tuple)) or any(not isinstance(p, str) or not p.strip() for p in repair_scope):
        raise ValueError("repair_scope must be an array of nonempty path globs")
    if mode == "repair" and not repair_scope:
        raise ValueError("repair needs an explicitly authorized repair_scope")
    if any(Path(p).is_absolute() or ".." in Path(p).parts for p in repair_scope):
        raise ValueError("repair_scope must use paths relative to the adopter")
    limits = validate_limits(limits if limits is not None else {})
    with exclusive(root):
        data = settings(root)
        data.update(mode=mode, repair_scope=list(repair_scope), limits=limits,
                    configured_at=now())
        _write_settings(root, data)
    return data


def observe_schedule(root, *, host, job_id, status, evidence, scheduled_prompt=None):
    """Store a host observation, not an assertion that this CLI created a job."""
    if host not in hosts.SUPPORTED or not job_id or not evidence:
        raise ValueError("host, job_id and host observation evidence are required")
    if status not in ("active", "paused", "removed", "unknown"):
        raise ValueError("unknown schedule status")
    with exclusive(root):
        data = settings(root)
        data.setdefault("schedules", {})[host] = {
            "job_id": job_id, "status": status, "evidence": evidence,
            "observed_at": now(), "scheduled_prompt": scheduled_prompt,
            "verification": "recorded host observation; not a live query"}
        _write_settings(root, data)
    return data["schedules"][host]


def event(conn, kind, payload, *, claim=None, task=None, actor="orchestrator"):
    ledger.insert(conn, "event", kind=kind, task_id=task, claim_id=claim,
                  actor=actor, payload=payload, created_at=now())


def events(conn, kind):
    return [(r["id"], json.loads(r["payload"])) for r in conn.execute(
        "SELECT id,payload FROM event WHERE kind=? ORDER BY id", (kind,))]


def runs(conn):
    out = {}
    for _, p in events(conn, RUN):
        rid = p["id"]
        out[rid] = {**out.get(rid, {}), **p}
    return out


def get_run(conn, run_id):
    r = runs(conn).get(run_id)
    if r is None:
        raise ValueError("unknown maintenance run")
    return r


def snapshot(root):
    root = Path(root).resolve()
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    from . import lens_catalogue
    paths = {"catalogue": root / lens_catalogue.CATALOGUE,
             "coordination": root / lens_catalogue.CONTRACT,
             "facts": config.facts_path_for(root)}
    return {"head": head, "tree": hashing.worktree_digest(root, reads=["**"]),
            "config": config.RepoConfig(root).sha, "criteria": criteria_stamp(root),
            **{k: hashing.file_sha(p) if p and p.is_file() else "" for k, p in paths.items()}}


def criteria_stamp(root):
    """The actual ruler and its fixtures, including shared framework imports."""
    root = Path(root).resolve()
    from .runner import V4_HOME
    paths = {root / ".v4/config.json", root / ".v4/claim_kinds.json",
             root / ".v4/acceptance.json", root / ".v4/checkers.json",
             root / ".v4/detectors.json"}
    from .lens_catalogue import CATALOGUE, CONTRACT
    paths.update((root / CATALOGUE, root / CONTRACT))
    paths.update((root / ".v4/lenses").glob("*.json"))
    for pattern in (".github/monitor/*.md", ".claude/agents/*.md", ".claude/commands/*.md",
                    ".agents/skills/*/SKILL.md", ".codex/agents/*.toml"):
        paths.update(root.glob(pattern))
    paths.update(root / p for p in (".claude/settings.json", ".codex/hooks.json", ".codex/config.toml"))
    facts = config.facts_path_for(root)
    if facts:
        paths.add(facts)
    programs = {}
    for name in ("checkers", "detectors"):
        registry = root / ".v4" / (name + ".json")
        if not registry.is_file():
            continue
        for key, entry in json.loads(registry.read_text()).items():
            if entry.get("path"):
                programs[name + ":" + key] = hashing.program_sha(root, root / entry["path"], framework_root=V4_HOME)
            if entry.get("fixtures"):
                fixture_root = root / entry["fixtures"]
                paths.update(p for p in fixture_root.rglob("*")
                             if p.is_file() and "__pycache__" not in p.relative_to(fixture_root).parts
                             and ".git" not in p.relative_to(fixture_root).parts and p.suffix != ".pyc")
    # Shared kernel code is part of the review/repair criterion even when it
    # is outside the adopter's tracked tree.
    programs["maintenance"] = hashing.program_sha(Path(__file__).resolve().parent.parent, "kernel/maintenance.py")
    values = {str(p.relative_to(root)): hashing.file_sha(p) if p.is_file() else "missing" for p in paths}
    return hashlib.sha256(json.dumps({"files": values, "programs": programs}, sort_keys=True).encode()).hexdigest()


def lens_stamp(lens):
    return hashlib.sha256(json.dumps(lens, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def context_for(conn, task):
    if not task:
        return {}
    row = conn.execute("SELECT request,base_commit FROM task WHERE id=?", (task,)).fetchone()
    if row is None:
        raise ValueError("unknown context task")
    return {"task": task, "request": row["request"], "base": row["base_commit"],
            "source": "ledger task request; original-chat provenance is not authenticated"}


def attention_summary(root, conn):
    result = attention.scan(root, conn=conn)
    return {"as_of": now(), "root": str(Path(root).resolve()),
            "pending": len(result["items"]), "errors": len(result["errors"]),
            "available": result["available"]}


def session_summary(root, *, max_age_seconds=86400):
    """Bounded cached observation for a hook; never run checkers or full scans."""
    prefix = "Vibeproof maintenance: "
    try:
        if not ledger.ledger_path(root).exists():
            return prefix + "no attention observation yet; use /maintain or $vibeproof-maintain."
        with closing(ledger.connect_readonly(root)) as conn:
            row = conn.execute("SELECT payload FROM event WHERE kind=? ORDER BY id DESC LIMIT 1", (RUN,)).fetchone()
        data = json.loads(row[0]).get("attention") if row else None
        if not data or data.get("root") != str(Path(root).resolve()):
            return prefix + "no attention observation for this worktree; use /maintain or $vibeproof-maintain."
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(data["as_of"])).total_seconds()
        if age < 0 or age > max_age_seconds:
            return prefix + "attention observation expired; refresh through /maintain or $vibeproof-maintain."
        return (prefix + f"last observed {data['pending']} pending item(s), {data['errors']} read error(s) at {data['as_of']}. "
                "This is a cached summary; use /maintain or $vibeproof-maintain for current state.")
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        return prefix + "attention observation unavailable (" + type(exc).__name__ + "); inspect maintain status."


def start(root, *, run_id=None, host="claude", session="manual", task=None,
          context_task=None, selected=None, trigger="manual", job_id=None):
    root = Path(root).resolve()
    if host not in hosts.SUPPORTED or not session:
        raise ValueError("supported host and session are required")
    if trigger not in ("manual", "scheduled"):
        raise ValueError("unknown maintenance trigger")
    if trigger == "scheduled":
        observed = settings(root).get("schedules", {}).get(host, {})
        if not job_id or observed.get("job_id") != job_id or observed.get("status") != "active":
            raise ValueError("scheduled runs need the configured active host job ID")
    run_id = run_id or uuid.uuid4().hex
    with exclusive(root), closing(ledger.connect(root)) as conn:
        old = runs(conn)
        if run_id in old:
            if (old[run_id]["root"], old[run_id]["host"], old[run_id]["session"]) != (str(root), host, session):
                raise ValueError("run identity already belongs to different coordinates")
            if old[run_id].get("task") != task or old[run_id].get("context", {}).get("task") != (context_task or task):
                raise ValueError("run identity cannot be reused for another task/context")
            if old[run_id].get("trigger", "manual") != trigger or old[run_id].get("job_id") != job_id:
                raise ValueError("run identity cannot be reused for another trigger")
            if selected is not None and set(selected) != set(old[run_id]["expected"]) | set(old[run_id]["excluded"]):
                raise ValueError("run identity cannot be reused for another lens set")
            return old[run_id]
        active = [r["id"] for r in old.values() if r["status"] == "running"]
        if active:
            raise ValueError("maintenance already running: " + ", ".join(active))
        if task and lifecycle.worktree_of(conn, task) != str(root):
            raise ValueError("task review must run in the task's recorded worktree")
        context = context_for(conn, context_task or task)
        lenses, invalid = review.lens_files(root)
        if invalid:
            raise ValueError("unreadable lens definitions: " + str(invalid))
        active_lenses = {k: v for k, v in lenses.items() if not v.get("legacy_view")}
        cadence_lenses = sorted(k for k, v in active_lenses.items() if context or not v.get("requires_context"))
        requested = list(selected) if selected else list(active_lenses)
        missing = set(requested) - set(active_lenses)
        if missing:
            raise ValueError("unknown active lenses: " + ", ".join(sorted(missing)))
        expected, excluded = {}, {}
        for slug in requested:
            lens = active_lenses[slug]
            if lens.get("requires_context") and not context and selected is None:
                excluded[slug] = "request/change context required; not selected for a tree-only review"
                continue
            expected[slug] = {"sha256": lens_stamp(lens),
                              "checks": [c.get("id", str(i)) if isinstance(c, dict) else str(i)
                                         for i, c in enumerate(lens["checks"])],
                              "requires_context": bool(lens.get("requires_context"))}
        if not expected:
            raise ValueError("no evaluable lenses selected")
        from .lens_catalogue import coordination
        result = {"coordination_checks": coordination(root), "id": run_id, "status": "running", "host": host, "session": session,
                  "limits": validate_limits(settings(root).get("limits", {})),
                  "trigger": trigger, "job_id": job_id,
                  "root": str(root), "task": task, "context": context,
                  "snapshot": snapshot(root), "expected": expected, "excluded": excluded,
                  "cadence_lenses": cadence_lenses,
                  "attention": attention_summary(root, conn),
                  "started_at": now()}
        event(conn, RUN, result)
        return result


def resume_run(root, run_id):
    with exclusive(root), closing(ledger.connect(root)) as conn:
        r = get_run(conn, run_id)
        if r["root"] != str(Path(root).resolve()) or r["status"] != "partial":
            raise ValueError("only a partial run in this worktree can resume")
        if any(x["status"] == "running" for x in runs(conn).values()):
            raise ValueError("another maintenance run owns the repo")
        if snapshot(root) != r["snapshot"]:
            raise ValueError("source changed; create a new review instead of reusing old coverage")
        r = {**r, "status": "running", "resumed_at": now()}
        event(conn, RUN, r)
        return r


def validate_run(conn, root, run_id, slug):
    r = get_run(conn, run_id)
    if r["status"] != "running" or r["root"] != str(Path(root).resolve()):
        raise ValueError("run is not active in this worktree")
    if slug not in r["expected"]:
        raise ValueError("lens was not selected for this run")
    current = review.lenses(root).get(slug)
    if not current or lens_stamp(current) != r["expected"][slug]["sha256"]:
        raise ValueError("lens definition changed during review")
    if snapshot(root) != r["snapshot"]:
        raise ValueError("reviewed source/config changed; begin a new run")
    return r


def lens_result(conn, root, run_id, slug, *, result, findings, note="", evidence=()):
    if result not in RESULTS or not isinstance(findings, int) or isinstance(findings, bool) or findings < 0:
        raise ValueError("valid result and a nonnegative finding count are required")
    r = validate_run(conn, root, run_id, slug)
    if result != "completed" and not note.strip():
        raise ValueError("a non-completed result needs its reason")
    if r["expected"][slug]["requires_context"] and not r["context"] and result in ("completed", "not_applicable"):
        raise ValueError("missing request/change input is not a completed or inapplicable review")
    briefed = any(p.get("run_id") == run_id and p.get("lens") == slug
                  for _, p in events(conn, review.LENS_RUN_KIND))
    if not briefed:
        raise ValueError("take this run's brief before reporting its result")
    return {"run_id": run_id, "lens": slug, "result": result, "findings": findings,
            "note": note, "evidence": list(evidence), "lens_sha": r["expected"][slug]["sha256"],
            "snapshot": r["snapshot"]}


def observe_finding(conn, root, run_id, slug, claim_id, check_id=None):
    r = validate_run(conn, root, run_id, slug)
    claim = conn.execute("SELECT task_id FROM claim WHERE id=?", (claim_id,)).fetchone()
    if claim is None or claim["task_id"] != (r["task"] or ledger.REVIEW_TASK):
        raise ValueError("finding does not belong to this review's filing task")
    if check_id and check_id not in r["expected"][slug]["checks"]:
        raise ValueError("check was not assigned to this lens")
    event(conn, "review_observed", {"run_id": run_id, "lens": slug,
                                   "claim": claim_id, "check_id": check_id},
          claim=claim_id, task=r["task"], actor="reviewer")


def finish(root, run_id, *, abandon_reason=None, coordination=None):
    from . import sweep
    with exclusive(root), closing(ledger.connect(root)) as conn:
        r = get_run(conn, run_id)
        if r["status"] != "running":
            return r
        if str(Path(root).resolve()) != r["root"]:
            raise ValueError("finish from the run's recorded worktree")
        results = {}
        for _, p in events(conn, review.LENS_REVIEWED_KIND):
            if p.get("run_id") == run_id:
                results[p["lens"]] = p
        current = review.lenses(root)
        valid = snapshot(root) == r["snapshot"] and all(
            name in current and lens_stamp(current[name]) == spec["sha256"] for name, spec in r["expected"].items())
        complete = valid and set(results) == set(r["expected"]) and all(
            p.get("result") in ("completed", "not_applicable") and p.get("snapshot") == r["snapshot"]
            for p in results.values())
        seen = {name: set() for name in r["expected"]}
        for _, observation in events(conn, "review_observed"):
            if observation.get("run_id") == run_id and observation.get("lens") in seen:
                seen[observation["lens"]].add(observation["claim"])
        mismatches = {name: {"reported": p["findings"], "recorded": len(seen[name])}
                      for name, p in results.items() if name in seen and p["findings"] != len(seen[name])}
        complete = complete and not mismatches
        claim_ids = set().union(*seen.values()) if seen else set()
        required = {c["id"] for c in r.get("coordination_checks", [])}
        assessments = coordination or {}
        if not isinstance(assessments, dict):
            raise ValueError("coordination must be an object keyed by check ID")
        if len(claim_ids) < 2:
            assessments = {cid: {"result": "not_applicable", "note": "No pair of distinct findings in this run", "claims": sorted(claim_ids)} for cid in required}
        missing_assessments = [cid for cid in required if not isinstance(assessments.get(cid), dict)
                               or not assessments[cid].get("note")
                               or assessments[cid].get("result") not in ("completed", "not_applicable")
                               or set(assessments[cid].get("claims", [])) != claim_ids]
        complete = complete and not missing_assessments
        pending = [h["id"] for h in handoffs(conn, run_id).values()
                   if h["role"] in ("monitor", "reviewer", "task-splitter") and
                   (h["status"] not in ("result", "settled", "cancelled") or h.get("decision") in ("review_failed", "plan_failed"))]
        complete = complete and not pending
        status = "abandoned" if abandon_reason else "complete" if complete else "stale" if not valid else "partial"
        result = {**r, "status": status, "results": results, "finished_at": now(),
                  "attention": attention_summary(root, conn),
                  "reason": abandon_reason or "", "missing": sorted(set(r["expected"]) - set(results)),
                  "pending_handoffs": pending, "finding_count_mismatches": mismatches,
                  "coordination": assessments, "missing_coordination": missing_assessments}
        result["advances_cadence"] = bool(complete and not abandon_reason and r.get("cadence_lenses") and
                                          set(r["cadence_lenses"]) <= set(r["expected"]))
        event(conn, RUN, result)
        if result["advances_cadence"]:
            event(conn, sweep.KIND, {"run_id": run_id, "complete": True, "cadence_complete": True,
                                    "lenses": list(r["expected"]), "reviewed": {k: v["findings"] for k, v in results.items()},
                                    "ran": list(results), "excluded": r["excluded"],
                                    "snapshot": r["snapshot"], "findings": sum(p["findings"] for p in results.values())})
        return result


def handoffs(conn, run_id=None):
    out = {}
    for _, p in events(conn, HANDOFF):
        if run_id and p.get("run_id") != run_id:
            continue
        out[p["id"]] = {**out.get(p["id"], {}), **p}
    return out


def repair_target(root, conn, run, data, *, read_snapshot=True):
    """Recheck the operation's actual worktree and grant immediately before dispatch."""
    if snapshot(root) != run["snapshot"]:
        raise ValueError("source changed since review; revalidate before dispatching repair")
    policy = settings(root)
    if policy["mode"] != "repair" or not policy.get("repair_scope"):
        raise ValueError("repair has not been authorized in maintenance settings")
    tid = data.get("task")
    if not data.get("claim") or tid not in ledger.open_task_ids(conn):
        raise ValueError("repair needs an original claim and an open repair task")
    target = lifecycle.worktree_of(conn, tid)
    if not target or not Path(target).is_dir() or ledger.ledger_path(target) != ledger.ledger_path(root):
        raise ValueError("repair task worktree is unavailable or belongs to another repository")
    target = Path(target).resolve()
    if str(target) == run["root"]:
        raise ValueError("maintenance repairs need a separate worktree from the reviewed source")
    for other in ledger.open_task_ids(conn):
        if other != tid and lifecycle.worktree_of(conn, other) == str(target):
            raise ValueError("repair worktree is occupied by another task: " + other)
    from .analysis.subject_files import matches
    paths = json.loads(conn.execute("SELECT scope_globs FROM task WHERE id=?", (tid,)).fetchone()[0])
    if not paths or any(any(c in p for c in "*?[") or not matches(p, policy["repair_scope"]) for p in paths):
        raise ValueError("unattended repair tasks must declare concrete paths inside authorized repair_scope")
    return {"root": str(target), **({"snapshot": snapshot(target)} if read_snapshot else {})}


def wiring(root):
    """Separate installed entrypoints, host observations and recorded executions."""
    from . import sweep
    root = Path(root).resolve()
    entries = {"claude": ".claude/commands/maintain.md",
               "codex": ".agents/skills/vibeproof-maintain/SKILL.md"}
    result = {"entrypoints": {h: (root / p).is_file() for h, p in entries.items()},
              "schedules": settings(root).get("schedules", {}),
              "executions": {}, "last_complete": None,
              "verification": "Local host observations and ledger records; query the host to confirm current job state."}
    if not ledger.ledger_path(root).exists():
        result["ledger"] = "not initialized"
        return result
    with closing(ledger.connect_readonly(root)) as conn:
        result["last_complete"] = sweep.last(conn)
        for r in runs(conn).values():
            if r.get("trigger") == "scheduled":
                result["executions"].setdefault(r["host"], {})[r["job_id"]] = {
                    k: r.get(k) for k in ("id", "started_at", "finished_at", "status")}
    return result


def handoff(root, data):
    """Persist correlation and evidence. A reported result is never a claim PASS."""
    required = ("id", "run_id", "action")
    if any(not data.get(k) for k in required):
        raise ValueError("handoff needs id, run_id and action")
    action = data["action"]
    if action not in HANDOFF_FIELDS:
        raise ValueError("unknown handoff action")
    extra = set(data) - {"id", "run_id", "action", "claim", "task", "role"} - HANDOFF_FIELDS[action]
    if extra:
        raise ValueError("fields not read by this handoff action: " + ", ".join(sorted(extra)))
    with exclusive(root), closing(ledger.connect(root)) as conn:
        run = get_run(conn, data["run_id"])
        previous = handoffs(conn).get(data["id"])
        if previous and previous["run_id"] != data["run_id"]:
            raise ValueError("handoff ID belongs to another run")
        action = data["action"]
        if previous:
            for key in ("claim", "role", "snapshot"):
                if key in data and data[key] != previous.get(key):
                    raise ValueError("handoff " + key + " is immutable")
            if action != "link_repair" and "task" in data and data["task"] != previous.get("task"):
                raise ValueError("use link_repair to associate a different repair task")
        if action == "prepare":
            if previous:
                if any(previous.get(k) != data.get(k) for k in ("claim", "task", "role")):
                    raise ValueError("handoff coordinates changed")
                return previous
            if run["status"] not in ("running", "complete") or data.get("role") not in ROLES:
                raise ValueError("active run and known role required")
            limits = run.get("limits", DEFAULT_LIMITS)
            if limits.get("max_run_seconds") and (datetime.now(timezone.utc) - datetime.fromisoformat(run["started_at"])).total_seconds() >= limits["max_run_seconds"]:
                raise ValueError("run dispatch budget expired; results can still be recorded")
            pending = [h for h in handoffs(conn).values() if h["status"] in ("prepared", "dispatched", "dispatch_unknown", "result_pending_receipt")]
            def slot(h):
                return (h["role"], h.get("task")) if h["role"] in ("worker", "checker-author") else (h["role"], h["id"])
            slots = {slot(h) for h in pending}
            if limits.get("max_parallel_agents") and slot(data) not in slots and len(slots) >= limits["max_parallel_agents"]:
                raise ValueError("agent capacity reached; wait for existing handoffs")
            if data.get("claim") and not conn.execute("SELECT 1 FROM claim WHERE id=?", (data["claim"],)).fetchone():
                raise ValueError("unknown claim")
            if data["role"] in ("worker", "checker-author"):
                for prior in handoffs(conn).values():
                    if prior.get("claim") == data.get("claim") and prior.get("role") in ("worker", "checker-author") and prior["status"] not in ("settled", "cancelled"):
                        raise ValueError("this claim already has a repair handoff: " + prior["id"])
                attempts = set()
                for _, old in events(conn, HANDOFF):
                    if old.get("claim") != data.get("claim") or old.get("role") not in ("worker", "checker-author"):
                        continue
                    if old["action"] == "settle":
                        attempts.clear()
                    elif old["action"] in ("dispatched", "dispatch_unknown"):
                        attempts.add(old["id"])
                if len(attempts) >= limits["max_repair_attempts"]:
                    raise ValueError("repair attempt budget exhausted; investigate rather than replaying")
                data = {**data, "repair_target": repair_target(root, conn, run, data)}
            data = {**data, "status": "prepared", "snapshot": run["snapshot"]}
        else:
            if previous is None:
                raise ValueError("prepare the handoff first")
            if action == "check_dispatch":
                if previous["status"] != "prepared":
                    raise ValueError("only a prepared handoff may be dispatched")
                if run.get("limits", {}).get("max_run_seconds") and (datetime.now(timezone.utc) - datetime.fromisoformat(run["started_at"])).total_seconds() >= run["limits"]["max_run_seconds"]:
                    raise ValueError("run dispatch budget expired")
                if previous["role"] in ("worker", "checker-author") and repair_target(root, conn, run, previous) != previous["repair_target"]:
                    raise ValueError("repair target changed after preparation")
                return {"id": previous["id"], "ready": True, "basis": "current observation; recheck host receipt"}
            elif action in ("dispatched", "dispatch_unknown"):
                if previous["status"] not in ("prepared", "dispatch_unknown", "result_pending_receipt"):
                    raise ValueError("handoff already dispatched; reconcile with the host instead of redispatching")
                if action == "dispatched" and not data.get("host_ref"):
                    raise ValueError("dispatched needs the real host task/agent reference")
                # Dispatch happens between prepare and this receipt. A changed
                # target is now uncertain, not permission to send a second worker.
                if previous["role"] in ("worker", "checker-author"):
                    try:
                        current = repair_target(root, conn, run, previous, read_snapshot=False)
                        if current["root"] != previous["repair_target"]["root"]:
                            raise ValueError("repair target changed after preparation")
                    except ValueError as exc:
                        data = {**previous, **data, "status": "dispatch_unknown", "needs_attention": str(exc)}
                        event(conn, HANDOFF, data, claim=data.get("claim"), task=data.get("task"))
                        return data
                received_result = previous["status"] == "result_pending_receipt" and action == "dispatched"
                data = {**previous, **data, "status": "result" if received_result else action}
            elif action == "result":
                if previous["status"] not in ("prepared", "result_pending_receipt", "dispatched", "dispatch_unknown", "result", "settled"):
                    raise ValueError("handoff cannot receive a result in this state")
                if data.get("decision") not in REPORT_DECISIONS:
                    raise ValueError("unknown handoff decision")
                if not data.get("reason") or not data.get("evidence") or data.get("reviewed_head") != run["snapshot"]["head"]:
                    raise ValueError("result needs reason, evidence and the assigned reviewed_head")
                if data["decision"] in ("needs_fix", "needs_evidence", "needs_checker") and not data.get("acceptance"):
                    raise ValueError("a repair/evidence request needs acceptance conditions")
                late = previous["status"] == "settled"
                waiting = previous["status"] in ("prepared", "result_pending_receipt") or (previous["status"] == "dispatch_unknown" and not previous.get("host_ref"))
                data = {**previous, **data, "status": "settled" if late else "result_pending_receipt" if waiting else "result", "late_result": late}
                if late and data["decision"] in ("work_failed", "review_failed"):
                    data["needs_attention"] = "late failure conflicts with the earlier settlement; inspect both records"
            elif action == "link_repair":
                if not data.get("task") or not previous.get("claim"):
                    raise ValueError("link_repair needs original claim and repair task")
                if not lifecycle.worktree_of(conn, data["task"]):
                    raise ValueError("unknown repair task worktree")
                if previous.get("task") != data["task"] and (previous["status"] == "settled" or previous["role"] in ("worker", "checker-author")):
                    raise ValueError("cannot retarget a worker or a settled handoff")
                data = {**previous, **data, "status": previous["status"]}
            elif action == "settle":
                claim = conn.execute("SELECT * FROM claim WHERE id=?", (previous.get("claim"),)).fetchone()
                worktree = lifecycle.worktree_of(conn, previous.get("task"))
                if claim is None or not worktree or ledger.ledger_path(worktree) != ledger.ledger_path(root):
                    raise ValueError("settlement needs original claim and its linked repair worktree")
                cfg = config.RepoConfig(worktree)
                lifecycle.check(conn, cfg, claim["task_id"], only=[claim["id"]])
                current = dict((row["id"], st) for row, st in lifecycle.report(conn, cfg, claim["task_id"])[0])[claim["id"]]
                if current not in state.TERMINAL:
                    raise ValueError("original claim is not settled: " + current)
                attempt = ledger.latest_attempt(conn, claim["id"])
                data = {**previous, **data, "status": "settled", "claim_state": current,
                        "attempt": attempt["id"] if attempt else None}
            elif action == "cancel":
                if not data.get("reason") or previous.get("task") in ledger.open_task_ids(conn):
                    raise ValueError("cancel needs a reason and an ended repair task")
                data = {**previous, **data, "status": "cancelled"}
            else:
                raise ValueError("unknown handoff action")
        event(conn, HANDOFF, data, claim=data.get("claim"), task=data.get("task"))
        return data


def status(root):
    result = {"settings": settings(root), "attention": attention.scan(root), "runs": {}, "handoffs": {},
              "monitor": {"due": None, "basis": "not yet read"}}
    try:
        with closing(ledger.connect_readonly(root)) as conn:
            from . import sweep
            previous = next((p for _, p in sweep.history(conn, limit=-1) if sweep.complete_payload(p)), None)
            changed_criteria = previous is None or previous.get("snapshot", {}).get("criteria") != criteria_stamp(root)
            due, why = sweep.elapsed(config.RepoConfig(root), None if changed_criteria else sweep.last(conn), datetime.now(timezone.utc))
            if changed_criteria:
                why = ("review criteria/facts/fixtures changed or have no complete recorded review" if due else
                       why + "; changed criteria await this configured time window")
            result.update(runs=runs(conn), handoffs=handoffs(conn),
                          monitor={"due": changed_criteria, "basis": "criteria/facts/fixtures compared with the last complete review"},
                          cadence={"due": due, "why": why, "basis": "clock and criterion version; worktree isolation is checked separately"})
    except (OSError, ValueError, sqlite3.Error, config.ConfigError) as exc:
        # Status never creates a missing ledger. Surface the failure instead.
        result["ledger_error"] = str(exc)
        missing = not ledger.ledger_path(root).exists()
        result["monitor"] = {"due": True if missing else None, "basis": "first review" if missing else "state unreadable"}
        result["cadence"] = {"due": True if missing else None,
                             "why": "first review; ledger not initialized" if missing else "ledger/config unreadable"}
    return result
