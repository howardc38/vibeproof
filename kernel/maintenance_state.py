"""Shared run observations and report rules, below review and maintenance."""
import hashlib
import json
import subprocess
from pathlib import Path

from . import config, hashing

RUN = "maintenance_run"
RESULTS = ("completed", "not_applicable", "not_evaluable", "failed")
EVALUATED_RESULTS = ("completed", "not_applicable")
LENS_RUN_KIND = "lens_run"


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


def validate_run(conn, root, run_id, slug, current):
    r = get_run(conn, run_id)
    if r["status"] != "running" or r["root"] != str(Path(root).resolve()):
        raise ValueError("run is not active in this worktree")
    if slug not in r["expected"]:
        raise ValueError("lens was not selected for this run")
    if not current or lens_stamp(current) != r["expected"][slug]["sha256"]:
        raise ValueError("lens definition changed during review")
    if snapshot(root) != r["snapshot"]:
        raise ValueError("reviewed source/config changed; begin a new run")
    return r


def check_lens_result(result, findings):
    """The vocabulary and the count, for both spellings of a lens report.

    Two writers produce a `lens_reviewed` row: `lens_result` for a run-bound
    report and `review.record_lens_reviewed` for one that names no run. Only
    the first checked either value, which is how a reviewer's
    `--result not_evaluable` came back as `{"lens": X, "findings": 0}` -- a
    report a reader could not tell from "ran and found nothing". One copy, so
    the two writers cannot drift into different vocabularies again.
    """
    if result not in RESULTS or not isinstance(findings, int) or isinstance(findings, bool) or findings < 0:
        raise ValueError("valid result and a nonnegative finding count are required")


def check_lens_reason(result, note, evidence=()):
    """A non-completed result has to say why, for both spellings of the row.

    Two writers produce a `lens_reviewed` row: `lens_result` for a run-bound
    report and `review.record_lens_reviewed` for one that names no run. Only
    the first refused a `not_evaluable` with nothing on it, so
    `v4 review done --lens X --result not_evaluable --findings 0` (no `--run`,
    no `--note`, no `--evidence`) exited 0 and left a row saying the lens was
    not evaluated and not one word about why. That row cannot be argued with
    now and its reason cannot be checked later -- the same silence the
    `--findings` refusal exists to prevent, one field over. The run-less
    spelling is the one a sweep's reviewer actually uses.

    Note or evidence, because they are the same fact: what stopped the lens
    being evaluated. The run-bound writer has always taken the first, and an
    evidence reference is not a weaker floor -- it is the sentence with a
    pointer on it. `review.record_lens_reviewed` answers this ValueError with
    its own `BadCoordinates`, which the CLI prints as REFUSED and exits 2.
    """
    if result == "completed":
        return
    if (note or "").strip():
        return
    if any(isinstance(e, str) and e.strip() for e in (evidence or ())):
        return
    raise ValueError("a non-completed result needs its reason: a non-blank "
                     "note or an evidence reference")


def lens_result(conn, root, run_id, slug, *, current, result, findings, note="", evidence=()):
    check_lens_result(result, findings)
    r = validate_run(conn, root, run_id, slug, current)
    check_lens_reason(result, note, evidence)
    if r["expected"][slug]["requires_context"] and not r["context"] and result in EVALUATED_RESULTS:
        raise ValueError("missing request/change input is not a completed or inapplicable review")
    briefed = any(p.get("run_id") == run_id and p.get("lens") == slug
                  for _, p in events(conn, LENS_RUN_KIND))
    if not briefed:
        raise ValueError("take this run's brief before reporting its result")
    return {"run_id": run_id, "lens": slug, "result": result, "findings": findings,
            "note": note, "evidence": list(evidence), "lens_sha": r["expected"][slug]["sha256"],
            "snapshot": r["snapshot"]}
