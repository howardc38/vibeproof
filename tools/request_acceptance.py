#!/usr/bin/env python3
"""Retain disposable request-evolution CLI proof and optional native observations.

The default scenario launches no model. Native sessions use installed disposable
adopters and ordinary host permissions; a rejected permission is retained.
Review closure proof belongs to the canonical v4 review close/check commands.
"""
import argparse
import json
import os
import re
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import time

FRAMEWORK = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FRAMEWORK))

from kernel import coordination, design, hashing, ledger


class Acceptance:
    def __init__(self, out):
        self.out = Path(out).resolve()
        if self.out.exists() and any(self.out.iterdir()):
            raise ValueError("acceptance output is populated; choose a new output directory")
        self.out.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="request-adopter-", dir=self.out)).resolve()
        self.cases = []
        self.version = 0
        self.serial = 0

    def run(self, label, argv, *, root=None, data=None, expected=(0,), timeout=60):
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout < float("inf"):
            raise ValueError("acceptance timeout must be a positive number of seconds")
        root = root or self.root
        expectation = {"case": label, "expected_exit": list(expected), "argv": argv, "repo": str(root),
                       "timeout_seconds": timeout}
        print(json.dumps({"before_execution": expectation}), flush=True)
        number = len(self.cases) + 1
        log = self.out / f"cli-{number:03d}.log"
        # Reserve evidence before invoking a command, including concurrent
        # output users and interrupted attempts. Existing bytes are immutable.
        with log.open("x") as stream:
            stream.write(json.dumps({"before_execution": expectation}) + "\n")
            stream.flush()
            started = time.monotonic()
            timed_out = False
            try:
                result = subprocess.run(argv, cwd=root, input=json.dumps(data) if data is not None else None,
                                        text=True, capture_output=True, timeout=timeout)
                code, stdout, stderr = result.returncode, result.stdout, result.stderr
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                code = None
                stdout = exc.stdout.decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
                stderr = exc.stderr.decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
            seconds = time.monotonic() - started
            stream.write(stdout + "\nSTDERR:\n" + stderr)
        case = {**expectation, "code": code, "ok": not timed_out and code in expected,
                "timed_out": timed_out, "seconds": round(seconds, 3), "log": str(log)}
        self.cases.append(case)
        self.save()
        if timed_out:
            raise RuntimeError(f"{label}: timed out after {timeout} seconds; partial output retained at {log}")
        if not case["ok"]:
            raise RuntimeError(f"{label}: expected {expected}, got {code}; {log}")
        return result

    def cli(self, action, data, *, group="coordinate", root=None, expected=(0,)):
        root = root or self.root
        return self.run(group + " " + action,
            [str(FRAMEWORK / "bin/v4"), "--repo", str(root), group, action, "--data", "-"],
            root=root, data=data, expected=expected)

    def mutation(self, action, **fields):
        self.serial += 1
        data = {"id": f"acceptance-operation-{self.serial}", "goal_id": "acceptance-goal",
                "expected_version": self.version, **fields}
        result = self.cli(action, data)
        self.version += 1
        return result

    def save(self, **extra):
        path = self.out / "direct-cli.json"
        body = json.loads(path.read_text()) if path.exists() else {}
        body.update({"framework": str(FRAMEWORK), "repo": str(self.root), "cases": self.cases,
                     "native_observations": "separate opt-in stage", **extra})
        path.write_text(json.dumps(body, indent=2) + "\n")

    def git(self, *args, root=None):
        return self.run("git " + " ".join(args[:2]), ["git", "-c", "commit.gpgsign=false", *args], root=root)

    def setup(self):
        self.git("init", "-q")
        self.git("config", "user.name", "Request acceptance")
        self.git("config", "user.email", "request-acceptance@example.invalid")
        (self.root / ".v4").mkdir()
        (self.root / ".v4/config.json").write_text(json.dumps({
            "test_command": "python3 -m unittest discover", "policy": "allow_accepted_risk"}))
        (self.root / ".v4/claim_kinds.json").write_text("{}")
        (self.root / "app.py").write_text("def calculate(value):\n    return value - 2\n")
        (self.root / "DESIGN.md").write_text("The calculator subtracts two from the given value.\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "Disposable request-evolution baseline")

    def task(self, name):
        path = self.out / ("worktree-" + name)
        self.git("worktree", "add", "--detach", "-q", str(path), "HEAD")
        self.run("open disposable task " + name, [str(FRAMEWORK / "bin/v4"), "--repo", str(path),
            "task", "--id", name, "--request", "Inspect the disposable calculator.", "--scope", "app.py,DESIGN.md"], root=path)
        return path

    def assign(self, work, revision, task):
        path = self.task(task)
        aid = "assignment-" + task
        self.mutation("assign", id=aid, work_id=work, revision=revision, task_id=task,
            worktree=str(path), host="codex", session="direct-cli-fixture", agent="fixture-" + task,
            dependency_versions=[])
        return aid, path

    @staticmethod
    def change(op, work, revision, **extra):
        return {"op": op, "work_id": work, "expected_revision": revision,
                "reason": "Exact disposable request selects this disposition.", **extra}

    def scenario(self):
        source = {"host": "codex", "session": "direct-cli-fixture"}
        first = "Inspect alpha and beta calculator work."
        self.mutation("input", id="original-input", text=first, source=source)
        self.mutation("assess", input_id="original-input", clauses=[{"quote": first, "relation": "add",
            "reason": "Both deliverables are explicitly requested."}], changes=[
            self.change("add", w, 0, title=w, request="Inspect " + w, depends_on=[]) for w in ("alpha", "beta")])
        alpha, old_path = self.assign("alpha", 1, "old-alpha")
        beta, beta_path = self.assign("beta", 1, "beta")
        schema = json.loads(self.run("coordinate schema", [str(FRAMEWORK / "bin/v4"), "--repo",
                                     str(self.root), "coordinate", "schema"]).stdout)
        if "begin-dispatch" in schema["commands"]:
            claimed = self.mutation("begin-dispatch", assignment_id=alpha)
            if not json.loads(claimed.stdout).get("launch_authorized"):
                raise RuntimeError("fresh synthetic launch claim was not authorized")
        else:
            # An acceptance fixture may run against its historical parent to
            # prove a regression; this is not a production launch fallback.
            self.cli("check-dispatch", {"goal_id": "acceptance-goal", "assignment_id": alpha,
                     "expected_version": self.version})
        followup = "Replace alpha while preserving beta."
        self.mutation("input", id="followup-input", text=followup, source=source)
        self.cli("check-dispatch", {"goal_id": "acceptance-goal", "assignment_id": beta,
                 "expected_version": self.version}, expected=(1,))
        self.mutation("assess", input_id="followup-input", clauses=[{"quote": followup, "relation": "change",
            "work_id": "alpha", "reason": "Only alpha changes."}], changes=[
            self.change("change", "alpha", 1, title="alpha revised", request="Inspect new alpha", depends_on=[]),
            self.change("unaffected", "beta", 1)])
        self.cli("check-dispatch", {"goal_id": "acceptance-goal", "assignment_id": alpha,
                 "expected_version": self.version}, expected=(1,))
        self.cli("check-dispatch", {"goal_id": "acceptance-goal", "assignment_id": beta,
                 "expected_version": self.version})
        self.mutation("stop", assignment_id=alpha, reason="The original alpha assignment is obsolete.")
        self.mutation("receipt", assignment_id=alpha, outcome="stopped", host_ref="direct-fixture-observation",
                      evidence=["Synthetic CLI protocol observation; not a native stop claim"])
        replacement, replacement_path = self.assign("alpha", 2, "replacement-alpha")
        self.cli("check-dispatch", {"goal_id": "acceptance-goal", "assignment_id": replacement,
                 "expected_version": self.version})
        self.cli("register", {"id": "register-design", "design_id": "calculator", "expected_design_revision": 0,
            "path": "DESIGN.md", "coverage": [{"path": "app.py", "reference": "calculator behavior"}],
            "basis": "observed_baseline", "intent_refs": [{"input_id": "original-input", "quote": first}],
            "author": source, "evidence": ["Read existing disposable app.py and DESIGN.md"]}, group="design")
        self.cli("disposition", {"id": "beta-na", "goal_id": "acceptance-goal", "work_id": "beta", "revision": 1,
            "disposition": "not_applicable", "reason": "Beta inspection changes no source or design statement.",
            "author": source}, group="design", root=beta_path)
        with ledger.connect_readonly(self.root) as conn:
            state = coordination.goal_status(conn, "acceptance-goal")
            observed = {"alpha_revision": state["works"]["alpha"]["revision"],
                "beta_assignment": coordination.task_context(conn, "beta")["assignment_id"],
                "obsolete_holds": coordination.task_holds(conn, old_path, "old-alpha"),
                "beta_design_holds": design.task_holds(conn, beta_path, "beta"),
                "replacement_design_holds": design.task_holds(conn, replacement_path, "replacement-alpha")}
        passed = (observed["alpha_revision"] == 2 and observed["beta_assignment"] == beta
                  and bool(observed["obsolete_holds"]) and not observed["beta_design_holds"]
                  and bool(observed["replacement_design_holds"]))
        self.save(observed=observed, direct_cli_pass=passed,
                  limitations="CLI receipt is labelled synthetic; native dispatch/stop and semantic review need native observations.")
        if not passed:
            raise RuntimeError("Truth-owner read-back disagrees with the declared scenario")
        return observed


def existing_adopter(repo, host, installer_record=None):
    """Require explicit provenance from this tool's disposable installer."""
    repo = Path(repo).resolve()
    if repo == FRAMEWORK or (repo / ".public-release.json").exists():
        raise ValueError("existing native repository must be a marked disposable adopter")
    marker = repo / ".v4/disposable-acceptance.json"
    if marker.exists():
        provenance = json.loads(marker.read_text())
    elif installer_record:
        provenance = json.loads(Path(installer_record).read_text())
        cases = provenance.get("cases", [])
        baseline = [case for case in cases if case.get("kind") == "git" and case.get("code") == 0
                    and case.get("argv", [])[-1:] == ["Disposable application's baseline"]]
        if not baseline or not repo.name.startswith(host + "-adopter-"):
            raise ValueError("installer record does not mark this repository as disposable")
    else:
        raise ValueError("existing native repository lacks explicit disposable provenance")
    if (provenance.get("repo") != str(repo) or provenance.get("framework") != str(FRAMEWORK)
            or provenance.get("host") != host):
        raise ValueError("disposable provenance does not match repository, framework and host")
    return repo


def native_child_environment(environ=None):
    """Drop outer task/bridge attribution while retaining auth and runtime inputs."""
    source = dict(os.environ if environ is None else environ)
    identity = re.compile(r"^(?:CODEX|CLAUDE|CLAUDE_CODE)_(?:SESSION|THREAD|AGENT|PARENT_SESSION|PARENT_AGENT|TASK|TOOL_CALL|APP_TOOLS_PIPE|PERMISSION_PROFILE|INTERNAL_ORIGINATOR)(?:_|$)")
    stripped = [key for key in source if key.startswith("V4_") or identity.match(key)]
    if any(key.startswith("V4_TRACE_") for key in source) and "PYTHONPATH" in source:
        stripped.append("PYTHONPATH")
    return {key: value for key, value in source.items() if key not in stripped}, sorted(stripped)


def _native_command(host, repo, prompt_file, out, timeout, *, codex_bin=None,
                    persist_session=False, reviewed_hooks=False):
    command = [sys.executable, str(FRAMEWORK / "tools/native_host_acceptance.py"), "--host", host,
               "--repo", str(repo), "--prompt-file", str(prompt_file), "--out", str(out),
               "--timeout", str(timeout)]
    if codex_bin:
        command += ["--codex-bin", codex_bin]
    if persist_session:
        command.append("--persist-session")
    if reviewed_hooks:
        command.append("--reviewed-test-hooks")
    return command


def native(host, out, timeout, existing=None, installer_record=None, reviewed_hooks=False,
           *, codex_bin=None, persist_session=False):
    if host != "codex" and (codex_bin or persist_session):
        raise ValueError("codex-bin and persist-session require the Codex native host")
    from kernel import hosts, install
    from tools.host_acceptance import Matrix
    codex_binary = (codex_bin or shutil.which("codex")) if host == "codex" else None
    if host == "codex" and not codex_binary:
        raise FileNotFoundError("Codex executable is unavailable on PATH")
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    attempt = Path(tempfile.mkdtemp(prefix="native-attempt-", dir=out))
    if existing:
        repo = existing_adopter(existing, host, installer_record)
        hook_paths = (".codex/hooks.json", "hooks/session_context.py", "hooks/_framework.py",
                      "hooks/write_block.py", "hooks/bash_guard.py", "hooks/stop_gate.py")
        reviewed = {path: hashing.file_sha(repo / path) for path in hook_paths} if reviewed_hooks else None
        matrix = Matrix(attempt / "installation", host, repo=repo)
        kinds, held = install.installable_kinds(FRAMEWORK, repo)
        print(json.dumps({"before_execution": {"stage": "refresh manifest-owned project assets",
            "expected": "Updated host prompt bytes; this refresh does not re-prove installation fixtures"}}), flush=True)
        copied = install.copy_files(FRAMEWORK, repo, kinds, host_choice=host)
        prefixes = (".agents/skills/", ".codex/agents/") if host == "codex" else (".claude/commands/", ".claude/agents/")
        assets = {str(path.relative_to(FRAMEWORK)): (repo / path.relative_to(FRAMEWORK)).is_file()
                  and (repo / path.relative_to(FRAMEWORK)).read_bytes() == path.read_bytes()
                  for path in FRAMEWORK.rglob("*") if path.is_file()
                  and path.relative_to(FRAMEWORK).as_posix().startswith(prefixes)
                  and path.suffix in (".md", ".toml")}
        (attempt / "asset-refresh.json").write_text(json.dumps({"copies": copied, "held_kinds": held,
            "assets": assets, "meaning": "Asset refresh only; interrupted installer remains unproven"}, indent=2) + "\n")
        if not assets or not all(assets.values()):
            raise RuntimeError("installed host prompt bytes differ from current framework")
        if reviewed_hooks:
            current = {path: hashing.file_sha(repo / path) for path in hook_paths}
            if current != reviewed:
                raise ValueError("installed hook bytes changed after review; review this copy before retrying")
            (attempt / "hook-review.json").write_text(json.dumps({"reviewed_sha256": reviewed,
                "launch_sha256": current, "scope": "explicit invocation only; no global trust change"}, indent=2) + "\n")
            project = repo / ".codex/config.toml"
            if not project.exists():
                with project.open("x") as stream:
                    stream.write((repo / ".codex/config.template.toml").read_text()
                                 + "\n[agents]\nenabled = true\nmax_concurrent_threads_per_session = 2\n")
    else:
        matrix = Matrix(attempt / "installation", host)
        matrix.setup()
    (matrix.repo / ".v4/disposable-acceptance.json").write_text(json.dumps({
        "repo": str(matrix.repo), "framework": str(FRAMEWORK), "host": host,
        "installer_record": str(installer_record) if installer_record else str(matrix.out / (matrix.repo.name + ".json"))}) + "\n")
    (matrix.repo / "DESIGN.md").write_text("Observed baseline: total adds the given discount to the price.\n")
    matrix.finalize_setup()
    task = "native-request-" + attempt.name.rsplit("-", 1)[-1]
    goal = "native-" + host + "-" + attempt.name.rsplit("-", 1)[-1] + "-goal"
    matrix.run("open native disposable task", ["task", "--id", task, "--request",
        "Record the current calculator as observed baseline without changing application source.",
        "--scope", "checkout.py,DESIGN.md"])
    prompt = f"""This is an authorized acceptance run in a disposable adopter: {matrix.repo}.
Use only this repository, task {task}, and its existing local ledger. Change no user-level settings.
Do not bypass a rejected permission or hook-trust decision. Do not sign any risk.
This is a standalone inspection/proof case for this fresh sanitized session and actual child identities, not a feature change or an amendment to the earlier baseline work. First discover every existing goal through coordinate status; retain earlier attempts with their invalid outer-session attribution as historical negative controls. The requester explicitly chooses new goal {goal} for this independent host acceptance case: record that reason in the assessment rather than silently ignoring related history. If you instead need to amend earlier work, reconcile its existing goal and stop this separate-case plan.
First inspect coordinate schema and design schema through ./bin/v4 --repo {matrix.repo}.
Bind task {task} using the actual host/session/agent identity supplied by SessionStart; a parent agent may have no agent ID or message ID. Do not invent them.
The exact synthetic user request is: Record the current calculator as observed baseline without changing application source.
Require the fresh actual injected SessionStart identity or its raw hook_seen row with hook=session_context and hook_event_name=SessionStart before the first protocol mutation. Read the raw ledger rows through a read-only SQLite connection or v4 export if needed. host status summarizes write_block/bash_guard/stop_gate guards and deliberately excludes identity hooks: its observed list cannot establish that SessionStart is absent. If the actual identity is absent or conflicting, report the gap and stop; do not infer identity from inherited environment, old bindings or your own bind response. Use the native spawn_agent tool with agent_type v4-worker and later v4-reviewer. Retain the actual reference returned by each spawn, whether a canonical task_name or an ID; do not invent a UUID or substitute an external app bridge or narrative child name. Use the available native wait API with its actual argument schema: if wait_agent accepts no child ID, call it without one and reconcile observations with the returned child reference. Close finished children only if a close API is exposed by this host.
Capture that literal input under goal {goal} with source using your actual identity. Assess one add deliverable baseline, revision 1. Read checkout.py and DESIGN.md, register the observed baseline, and record required disposition. Assign existing task {task} to a FUTURE child: use actual host/session but OMIT agent until the native host creates the child. Claim begin-dispatch durably before spawning, and proceed only on a fresh launch_authorized=true response; a read-only check-dispatch or same-ID replay never authorizes launching. Spawn one actual worker child to read checkout.py without changing source and return its actual hook identity, HEAD and arithmetic observation. It must bind its own task identity and hand observations back without shipping. Map the returned native child reference to the actual injected or raw-hook host/session/agent tuple before recording receipt.worker, then record the real completed result. The spawn reference need not be a UUID or equal the hook's agent value. A child narrative or spawn reference alone is not proof that its hooks ran: verify the matching injected hook event or raw hook_seen rows for this run, and report any missing identity or hook evidence explicitly. Never substitute parent identity for an unknown child. If a launch receipt is missing, reconcile that stable claim with the host rather than launch again. Use stable IDs and current CAS versions.
Read checkout.py and DESIGN.md. Register existing DESIGN.md as observed_baseline covering checkout.py with reference calculator behavior, then record required disposition for this deliverable.
Then spawn a DIFFERENT actual independent reviewer child using this host's native agent mechanism to read the recorded request, checkout.py and DESIGN.md and submit a content/source/version-bound design review under its own actual child identity. The worker cannot review itself. The reviewer must state whether this observed baseline accurately describes the current code, not approve desired future arithmetic, and must not bind a standing development task merely to read source. If the host cannot spawn a child or permissions refuse, report that gap without fabricating a receipt.
Keep source bytes unchanged. Read back coordinate status, design status and host status with the actual identities. Report transport, direct CLI state, native child and hook observations separately. Do not ship this fixture or claim integration.
"""
    print(json.dumps({"before_execution": {"native_host": host,
        "expected": "Actual native bookkeeping and independent child review, or an explicit retained permission/transport gap"}}), flush=True)
    prompt_file = attempt / "native-prompt.txt"
    prompt_file.write_text(prompt)
    command = _native_command(host, matrix.repo, prompt_file, attempt / "native", timeout,
        codex_bin=codex_binary, persist_session=persist_session, reviewed_hooks=reviewed_hooks)
    clean_environment, stripped = native_child_environment()
    (attempt / "child-environment.json").write_text(json.dumps({"stripped_names": stripped}, indent=2) + "\n")
    launched = subprocess.run(command, cwd=matrix.repo, env=clean_environment, capture_output=True,
                              text=True, timeout=timeout + 30)
    (attempt / "helper-stdout.log").write_text(launched.stdout)
    (attempt / "helper-stderr.log").write_text(launched.stderr)
    result = json.loads((attempt / "native/run.json").read_text())
    with ledger.connect_readonly(matrix.repo) as conn:
        readback = {"coordination": coordination.status(conn), "design": design.snapshot(conn, matrix.repo),
                    "hooks": hosts.evidence(conn, host=host)}
        ledger.export_jsonl(conn, attempt / "native-ledger.jsonl", matrix.repo)
    (attempt / "native-readback.json").write_text(json.dumps(readback, indent=2) + "\n")
    result["readback"] = str(attempt / "native-readback.json")
    return result


def snapshot_installer(repo, out):
    """Read back an interrupted install without completing or launching it."""
    repo, out = Path(repo).resolve(), Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("installer snapshot output is populated; choose a new directory")
    out.mkdir(parents=True, exist_ok=True)
    installed = repo / ".v4/installed.json"
    result = {"repo": str(repo), "source_observation": hashing.source_observation(repo),
              "installed_manifest": json.loads(installed.read_text()) if installed.exists() else None,
              "meaning": "Installer truth-owner snapshot only; no native transport or agent observation is claimed"}
    with ledger.connect_readonly(repo) as conn:
        result["ledger_counts"] = {table: conn.execute("SELECT count(*) FROM " + table).fetchone()[0]
                                    for table in ("task", "claim", "attempt", "event")}
        ledger.export_jsonl(conn, out / "installer-ledger.jsonl", repo)
    (out / "installer-observation.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--native-host", choices=("codex", "claude"))
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--snapshot-installer", type=Path)
    parser.add_argument("--existing-native-repo", type=Path)
    parser.add_argument("--installer-record", type=Path)
    parser.add_argument("--reviewed-test-hooks", action="store_true")
    parser.add_argument("--codex-bin", help="Codex executable for the optional native stage")
    parser.add_argument("--persist-session", action="store_true",
                        help="retain the Codex native session transcript for child hooks")
    args = parser.parse_args()
    if (args.codex_bin or args.persist_session) and args.native_host != "codex":
        parser.error("codex-bin and persist-session require native-host codex")
    if args.reviewed_test_hooks and (args.native_host != "codex" or not args.existing_native_repo):
        parser.error("reviewed-test-hooks requires codex and an already reviewed existing-native-repo")
    if args.snapshot_installer:
        snapshot_installer(args.snapshot_installer, args.out)
        print(json.dumps({"installer_snapshot": str(args.out.resolve() / "installer-observation.json")}))
        return 0
    if args.existing_native_repo:
        if not args.native_host:
            parser.error("existing-native-repo requires native-host")
        try:
            existing_adopter(args.existing_native_repo, args.native_host, args.installer_record)
        except (ValueError, OSError) as exc:
            print("REFUSED: " + str(exc), file=sys.stderr)
            return 2
    try:
        acceptance = Acceptance(args.out / "direct")
        acceptance.setup()
        acceptance.scenario()
    except (ValueError, RuntimeError, FileExistsError) as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 2
    result = {"direct_cli_pass": True, "summary": str(args.out.resolve() / "direct/direct-cli.json")}
    if args.native_host:
        result["native_transport"] = native(args.native_host, args.out / args.native_host, args.timeout,
            args.existing_native_repo, args.installer_record, args.reviewed_test_hooks,
            codex_bin=args.codex_bin, persist_session=args.persist_session)
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
