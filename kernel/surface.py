"""Validate declared surface execution, independent of the adopter's test runner.

The receipt is an oracle integration contract, not protection against a dishonest
oracle author. The reviewer still owns whether assertions prove the requirement.
"""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import urlsplit
import uuid

from . import ledger


def server_problem(servers, root: Path) -> str:
    """The same local-server context requirement for surface and browser trace."""
    if not isinstance(servers, list) or not servers:
        return "browser proof needs locally managed server context"
    for server in servers:
        if not isinstance(server, dict) or server.get("reuse") is not False:
            return "browser proof cannot reuse an existing server"
        where = server.get("cwd")
        if not isinstance(where, str) or not Path(where).resolve().is_relative_to(root):
            return "browser server cwd is outside the declared repository"
    return ""


def prepare(root: Path, kind: str):
    """Fresh evidence outside the judged tree, shared safely by Git worktrees."""
    try:
        parent = ledger.ledger_path(root).parent / "surface"
        parent.mkdir(parents=True, exist_ok=True)
    except subprocess.CalledProcessError:
        parent = None  # Checker fixtures may not be Git repositories.
    directory = Path(tempfile.mkdtemp(prefix="run-", dir=parent))
    run_id = uuid.uuid4().hex
    env = dict(os.environ, V4_SURFACE_RUN_ID=run_id,
               V4_SURFACE_RESULT=str(directory / "result.json"),
               V4_SURFACE_REPO=str(root), V4_SURFACE_KIND=kind)
    return env


def validate(result: Path, run_id: str, root: Path, cwd: Path, kind: str,
             required: list):
    """Return (checker exit, explanation, receipt); never infer tests from stdout."""
    try:
        receipt = json.loads(result.read_text())
    except (OSError, ValueError) as exc:
        return 1, f"no readable execution receipt at {result}: {exc}", None
    if not isinstance(receipt, dict):
        return 1, "execution receipt must be an object", None

    def unproved(why):
        return 1, why, receipt

    if receipt.get("schema") != 1 or receipt.get("run_id") != run_id:
        return unproved("receipt schema or run identity does not match this invocation")
    if receipt.get("kind") != kind or receipt.get("cwd") != str(cwd):
        return unproved("receipt surface kind or working directory does not match")
    planned, checks, errors = (receipt.get(k) for k in ("planned", "checks", "errors"))
    if not isinstance(errors, list):
        return unproved("receipt errors must be a list")
    if errors:
        return 1, "runner reported errors", receipt
    if (not isinstance(planned, list) or not planned or
            not all(isinstance(x, str) and x.strip() for x in planned) or
            len(set(planned)) != len(planned)):
        return unproved("receipt needs nonempty, unique planned case IDs")
    if not isinstance(checks, list) or not all(isinstance(x, dict) for x in checks):
        return unproved("receipt checks must be a list of case results")
    results = {}
    for check in checks:
        case, status = check.get("id"), check.get("status")
        if (not isinstance(case, str) or case not in planned or case in results or
                status not in ("passed", "failed", "skipped", "not_run")):
            return unproved("receipt has duplicate, unplanned or invalid case results")
        results[case] = check
    if any(x["status"] == "failed" for x in checks):
        return 1, "surface case failed", receipt
    if set(results) != set(planned) or any(x["status"] == "not_run" for x in checks):
        return unproved("planned surface cases did not finish")
    passed = {case for case, check in results.items() if check["status"] == "passed"}
    if not passed:
        return unproved("no surface case executed and passed (all skipped or not run)")
    missing = set(required) - passed
    if missing:
        return unproved("required surface cases absent or not passed: " + ", ".join(sorted(missing)))
    if kind == "browser":
        if not required:
            return unproved("browser proof needs surface_required case IDs declared before execution")
        problem = server_problem(receipt.get("managed_servers"), root)
        if problem:
            return unproved(problem)
        for case in required:
            observation = results[case].get("browser")
            if (not isinstance(observation, dict) or observation.get("run_id") != run_id or
                    not observation.get("name")):
                return unproved(f"required browser case {case!r} has no browser observation")
            urls = observation.get("urls")
            try:
                navigated = isinstance(urls, list) and any(
                    isinstance(url, str) and urlsplit(url).scheme in ("http", "https")
                    and urlsplit(url).hostname for url in urls)
            except ValueError:
                navigated = False
            if not navigated:
                return unproved(f"required browser case {case!r} did not navigate a web page")
    return 0, f"{len(passed)} surface case(s) executed and passed", receipt
