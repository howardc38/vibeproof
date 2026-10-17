"""Request evolution uses real Git worktrees, shared SQLite and public CLI JSON."""
import json
import inspect
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from kernel import config, hashing, ledger, lifecycle

SOURCE = Path(__file__).resolve().parents[1]
AUTHOR = {"host": "codex", "session": "fixture-session", "agent": "fixture-author"}
CHECKER = '''import argparse, json, runpy, sys
from pathlib import Path
p = argparse.ArgumentParser()
p.add_argument('--subject'); p.add_argument('--out'); p.add_argument('--facts')
a = p.parse_args()
s = json.loads(Path(a.subject).read_text())
calculate = runpy.run_path(str(Path(s['repo_root']) / 'app.py'))['calculate']
observed = calculate(5)
Path(a.out).write_text(json.dumps({'observed': observed}))
print('actual calculate(5):', observed)
sys.exit(0 if observed == 2 else 1)
'''


class CoordinationFixture(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="v4-coordinate-")
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.root = self.home / "main"
        self.root.mkdir()
        self.git(self.root, "init", "-q")
        self.git(self.root, "config", "user.email", "coordination@example.invalid")
        self.git(self.root, "config", "user.name", "Coordination test")
        self.git(self.root, "config", "commit.gpgsign", "false")
        (self.root / ".v4").mkdir()
        (self.root / ".v4/config.json").write_text(json.dumps({
            "test_command": "python3 -m unittest discover", "policy": "allow_accepted_risk"}))
        (self.root / "checker_probe.py").write_text(CHECKER)
        (self.root / ".v4/claim_kinds.json").write_text(json.dumps({
            "probe": {"checker": "probe", "staleness": "repo", "gate": "ship"}}))
        (self.root / ".v4/checkers.json").write_text(json.dumps({"probe": {
            "path": "checker_probe.py", "sha256": hashing.file_sha(self.root / "checker_probe.py"),
            "program_sha": hashing.program_sha(self.root, "checker_probe.py"),
            "reads": ["app.py"], "kinds": ["probe"]}}))
        (self.root / ".gitignore").write_text("__pycache__/\n")
        (self.root / "app.py").write_text("def calculate(value):\n    return value - 2\n")
        (self.root / "ARCHITECTURE.md").write_text("A small calculator.\n")
        self.git(self.root, "add", "-A")
        self.git(self.root, "commit", "-qm", "Disposable request baseline")
        self.conn = ledger.connect(self.root)
        self.addCleanup(self.conn.close)
        self.version = 0
        self.counter = 0
        self.tasks = {}
        self.env = {k: v for k, v in os.environ.items()
                    if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ")}
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        # Preserve only verifier instrumentation when a proof command adds it;
        # real CLI children must remain visible to the same execution trace.
        if "V4_TRACE_OUT" in os.environ:
            self.env.update({key: value for key, value in os.environ.items() if key.startswith("V4_TRACE_")})
            self.env["PYTHONPATH"] = os.environ.get("PYTHONPATH", "")

    def git(self, root, *args):
        return subprocess.run(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
                              cwd=root, text=True, capture_output=True, check=True).stdout.strip()

    def calculate(self, root):
        result = subprocess.run([sys.executable, "-c",
            "from app import calculate; print(calculate(5))"], cwd=root, env=self.env,
            text=True, capture_output=True, check=True, timeout=10)
        return int(result.stdout.strip())

    def cli(self, group, action, data=None, *, root=None):
        root = root or self.root
        command = [str(SOURCE / "bin/v4"), "--repo", str(root), group, action]
        if data is not None:
            command += ["--data", "-"]
        return subprocess.run(command, cwd=root, env=self.env,
                              input=json.dumps(data) if data is not None else None,
                              text=True, capture_output=True, timeout=30)

    def mutation(self, action, *, advance=True, expected=None, ok=True, **fields):
        self.counter += 1
        data = {"id": f"operation-{self.counter}", "goal_id": "goal", "expected_version": self.version,
                **fields}
        if expected is not None:
            data["expected_version"] = expected
        result = self.cli("coordinate", action, data)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            if advance:
                self.version += 1
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        return result, data

    def incoming(self, text, *, input_id=None):
        name = input_id or f"input-{self.counter + 1}"
        self.mutation("input", id=name, text=text,
                      source={**AUTHOR, "message_id": "message-" + name})
        return name

    def change(self, op, work_id, revision, **extra):
        return {"op": op, "work_id": work_id, "expected_revision": revision,
                "reason": f"The current request explicitly {op}s {work_id}.", **extra}

    def assessment(self, input_id, quote, changes, *, ok=True, expected=None):
        clauses = [{"quote": quote, "relation": changes[0]["op"],
                    "work_id": changes[0]["work_id"], "reason": "Exact request text determines affected work."}]
        return self.mutation("assess", input_id=input_id, clauses=clauses,
                             changes=changes, ok=ok, expected=expected)

    def plan(self, works=("alpha",)):
        quote = "Build " + " and ".join(works) + "."
        iid = self.incoming(quote)
        self.assessment(iid, quote, [self.change("add", w, 0, title=w,
                         request="Implement " + w, depends_on=[]) for w in works])
        return iid

    def task(self, name):
        path = self.home / ("worktree-" + name)
        self.git(self.root, "worktree", "add", "--detach", "-q", str(path), "HEAD")
        conn = ledger.connect(path)
        try:
            lifecycle.open_task(conn, config.RepoConfig(path), task_id=name,
                                request="Implement this deliverable", scope_globs=["app.py", "ARCHITECTURE.md"])
        finally:
            conn.close()
        self.tasks[name] = path
        return path

    def assign(self, work="alpha", revision=1, *, task=None, dependencies=None, ok=True):
        task = task or "task-" + work
        path = self.tasks.get(task) or self.task(task)
        assignment = "assignment-" + task
        self.mutation("assign", id=assignment, work_id=work, revision=revision, task_id=task,
                      worktree=str(path), host="codex", session="native-session", agent="worker-" + task,
                      dependency_versions=dependencies or [], ok=ok)
        return assignment, task

    def receipt(self, assignment, outcome="dispatched", **extra):
        current = self.goal()["assignments"][assignment]
        if outcome in ("dispatched", "unknown") and current["dispatch_state"] == "prepared":
            self.prelaunch(assignment)
        return self.mutation("receipt", assignment_id=assignment, outcome=outcome,
                             host_ref="native-" + assignment,
                             evidence=["Disposable test host observation"], **extra)

    def prelaunch(self, assignment):
        schema = json.loads(self.cli("coordinate", "schema").stdout)
        if "begin-dispatch" in schema["commands"]:
            result, data = self.mutation("begin-dispatch", assignment_id=assignment)
            return result, data, "begin-dispatch"
        # Historical proof runs must reach the old readiness decision rather
        # than fail because their CLI predates the atomic claim action.
        data = {"goal_id": "goal", "assignment_id": assignment, "expected_version": self.version}
        result = self.cli("coordinate", "check-dispatch", data)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result, data, "check-dispatch"

    @staticmethod
    def launch_authorized(result):
        if result.returncode:
            return False
        data = json.loads(result.stdout)
        return data.get("launch_authorized", data.get("ready", False))

    def context(self, task):
        from kernel import coordination
        return coordination.task_context(self.conn, task)

    def holds(self, task):
        from kernel import coordination
        return coordination.task_holds(self.conn, self.tasks[task], task)

    def assert_current_ownership(self, task):
        # An unfinished current worker still needs a dispatched completion result.
        codes = {item["code"] for item in self.holds(task)}
        self.assertLessEqual(codes, {"dispatch_claim_required", "dispatch_required", "completed_result_required"})

    def dispatch_check(self, assignment, *, expected=None):
        return self.cli("coordinate", "check-dispatch", {"goal_id": "goal", "assignment_id": assignment,
                       "expected_version": self.version if expected is None else expected})

    def goal(self):
        from kernel import coordination
        return coordination.goal_status(self.conn, "goal")

    def complete_artifact(self, assignment, task, work, revision=1, *, ship=True):
        """Obtain actual source completion in the assigned disposable worktree."""
        path = self.tasks[task]
        self.receipt(assignment)
        (path / "app.py").write_text("def calculate(value):\n    return value - 3\n")
        self.git(path, "add", "app.py")
        self.git(path, "commit", "-qm", "Implement the requested disposable deduction")
        head = self.git(path, "rev-parse", "HEAD")
        self.mutation("result", assignment_id=assignment, revision=revision,
                      host_ref="native-" + assignment, head_after=head, decision="completed",
                      reason="Disposable worker produced committed source.", evidence=["Real committed app.py artifact"])
        result = self.cli("design", "disposition", {"id": "na-" + task, "goal_id": "goal", "work_id": work,
            "revision": revision, "disposition": "not_applicable",
            "reason": "The disposable arithmetic artifact does not change the architecture contract.", "author": AUTHOR}, root=path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        ledger.insert(self.conn, "claim", id="proof-" + task, task_id=task, kind="probe",
                      question="Does the real calculator apply the new deduction?", checker="probe",
                      subject_refs=[{"kind": "file", "path": "app.py"}], origin="derive", created_at="2026")
        checked = lifecycle.check(self.conn, config.RepoConfig(path), task)
        self.assertEqual(len(checked), 1)
        self.assertEqual(checked[0][2].exit_code, 0, checked[0][2].stderr)
        if ship:
            ok, report = lifecycle.ship(self.conn, config.RepoConfig(path), task)
            self.assertTrue(ok, report)
        return head

    def integrate(self, assignment, task, head, revision=1):
        integration_task = "integrate-" + task
        lifecycle.open_task(self.conn, config.RepoConfig(self.root), task_id=integration_task,
                            request="Integrate the completed disposable artifact", scope_globs=["app.py"])
        self.git(self.root, "merge", "--ff-only", head)
        ledger.insert(self.conn, "claim", id="proof-" + integration_task, task_id=integration_task, kind="probe",
                      question="Does integrated source execute the requested deduction?", checker="probe",
                      subject_refs=[{"kind": "file", "path": "app.py"}], origin="derive", created_at="2026")
        checked = lifecycle.check(self.conn, config.RepoConfig(self.root), integration_task)
        self.assertEqual(checked[0][2].exit_code, 0, checked[0][2].stderr)
        ok, report = lifecycle.ship(self.conn, config.RepoConfig(self.root), integration_task)
        self.assertTrue(ok, report)
        self.mutation("settle", assignment_id=assignment, revision=revision,
                      integration={"task_id": integration_task, "worktree": str(self.root), "head": head},
                      evidence=["The actual integration task source includes the artifact commit"])


class Coordination(CoordinationFixture):
    def test_raw_input_is_idempotent_and_retains_literal_provenance(self):
        text = "First line.\n\nKeep punctuation: 雪 & <literal>."
        iid = self.incoming(text, input_id="original-message")
        before = self.goal()
        self.mutation("input", id=iid, text=text, source={**AUTHOR, "message_id": "message-" + iid},
                      advance=False, expected=0)
        self.assertEqual(self.goal(), before)
        self.assertEqual(before["inputs"][0]["text"], text)
        self.assertEqual(before["inputs"][0]["source"]["message_id"], "message-original-message")
        self.mutation("input", id=iid, text="Different bytes under the same identifier",
                      source={**AUTHOR, "message_id": "message-" + iid}, expected=0, ok=False)
        self.assertEqual(self.goal(), before)

    def test_pending_input_fences_dispatch_until_assessed(self):
        self.plan()
        aid, task = self.assign()
        self.assertEqual(self.dispatch_check(aid).returncode, 0)
        iid = self.incoming("Reconsider alpha.")
        self.assertNotEqual(self.dispatch_check(aid).returncode, 0)
        self.assertTrue(self.holds(task))
        self.assessment(iid, "Reconsider alpha.", [self.change("unaffected", "alpha", 1)])
        self.assertEqual(self.dispatch_check(aid).returncode, 0)
        self.assert_current_ownership(task)

    def test_partial_change_preserves_unaffected_assignment_and_historical_task(self):
        self.plan(("alpha", "beta"))
        alpha, ta = self.assign("alpha")
        beta, tb = self.assign("beta")
        self.receipt(alpha)
        self.receipt(beta)
        old = dict(self.conn.execute("SELECT * FROM task WHERE id=?", (ta,)).fetchone())
        with ledger.writing(self.conn):
            ledger.insert(self.conn, "claim", id="historical-claim", task_id=ta, kind="probe",
                          question="Historical evidence", checker="unregistered", subject_refs=[], origin="derive", created_at="2026")
        old_claim = dict(self.conn.execute("SELECT * FROM claim WHERE id='historical-claim'").fetchone())
        iid = self.incoming("Change alpha and keep beta.")
        self.assessment(iid, "Change alpha and keep beta.", [
            self.change("change", "alpha", 1, title="alpha revised", request="New alpha", depends_on=[]),
            self.change("unaffected", "beta", 1)])
        self.assertTrue(self.holds(ta))
        self.assert_current_ownership(tb)
        self.assertEqual(self.context(tb)["revision"], 1)
        self.assertEqual(self.context(tb)["assignment_id"], beta)
        self.assertNotEqual(self.dispatch_check(alpha).returncode, 0)
        self.assertNotEqual(self.dispatch_check(beta).returncode, 0)
        self.assertIn("already_observed", self.dispatch_check(beta).stdout)
        self.assertEqual(dict(self.conn.execute("SELECT * FROM task WHERE id=?", (ta,)).fetchone()), old)
        self.assertEqual(dict(self.conn.execute("SELECT * FROM claim WHERE id='historical-claim'").fetchone()), old_claim)

    def test_assessment_must_dispose_every_live_work_and_quote_actual_input(self):
        self.plan(("alpha", "beta"))
        iid = self.incoming("Change alpha, keep beta.")
        before = self.goal()
        self.assessment(iid, "Change alpha, keep beta.", [self.change("unaffected", "alpha", 1)], ok=False)
        self.assertEqual(self.goal(), before)
        self.assessment(iid, "A paraphrase absent from the user message", [
            self.change("unaffected", "alpha", 1), self.change("unaffected", "beta", 1)], ok=False)
        self.assertEqual(self.goal(), before)

    def test_queued_obsolete_work_never_acquires_an_assignment(self):
        self.plan()
        iid = self.incoming("Cancel alpha and add beta.")
        self.assessment(iid, "Cancel alpha and add beta.", [self.change("cancel", "alpha", 1),
            self.change("add", "beta", 0, title="beta", request="Implement beta", depends_on=[])])
        self.assign("alpha", 1, ok=False)
        aid, task = self.assign("beta")
        self.assertEqual(self.dispatch_check(aid).returncode, 0)
        self.assertEqual(self.context(task)["work_id"], "beta")

    def test_stop_request_is_not_observation_and_replacement_waits_for_stop(self):
        self.plan()
        aid, task = self.assign()
        self.receipt(aid)
        iid = self.incoming("Replace alpha with revised alpha.")
        self.assessment(iid, "Replace alpha with revised alpha.", [self.change("change", "alpha", 1,
                         title="new alpha", request="Revised alpha", depends_on=[])])
        self.mutation("stop", assignment_id=aid, reason="The request replaced this revision.")
        self.assertTrue(self.holds(task))
        self.assign("alpha", 2, task="replacement", ok=False)
        self.receipt(aid, outcome="stopped", expected=1)
        replacement, current = self.assign("alpha", 2, task="replacement")
        self.assertEqual(self.dispatch_check(replacement).returncode, 0)
        self.assertTrue(self.holds(task))
        self.assert_current_ownership(current)

    def test_unknown_dispatch_reconciles_stable_assignment_instead_of_duplicating(self):
        self.plan()
        aid, task = self.assign()
        self.receipt(aid, outcome="unknown")
        self.assertNotEqual(self.dispatch_check(aid).returncode, 0)
        self.assign(task="second-owner", ok=False)
        self.receipt(aid, outcome="dispatched")
        self.assertEqual(self.context(task)["assignment_id"], aid)
        self.assign(task="second-owner", ok=False)

    def test_dependency_revision_change_invalidates_only_dependent_work(self):
        self.plan(("base", "consumer", "independent"))
        iid = self.incoming("Consumer depends on base revision one.")
        self.assessment(iid, "Consumer depends on base revision one.", [self.change("unaffected", "base", 1),
            self.change("change", "consumer", 1, title="consumer", request="Use base", depends_on=[{"work_id": "base", "revision": 1}]),
            self.change("unaffected", "independent", 1)])
        self.assign("consumer", 2, task="premature-consumer", dependencies=[{"work_id": "base", "revision": 1}], ok=False)
        base, base_task = self.assign("base")
        head = self.complete_artifact(base, base_task, "base")
        self.mutation("settle", assignment_id=base, revision=1, evidence=["Worker artifact only"])
        self.assign("consumer", 2, task="premature-consumer", dependencies=[{"work_id": "base", "revision": 1}], ok=False)
        self.integrate(base, base_task, head)
        dep, td = self.assign("consumer", 2, dependencies=[{"work_id": "base", "revision": 1}])
        independent, ti = self.assign("independent")
        iid = self.incoming("Revise base, leave other deliverables unchanged.")
        self.assessment(iid, "Revise base, leave other deliverables unchanged.", [
            self.change("change", "base", 1, title="base", request="New base", depends_on=[]),
            self.change("unaffected", "consumer", 2), self.change("unaffected", "independent", 1)])
        self.assertTrue(self.holds(td))
        self.assert_current_ownership(ti)
        self.assertNotEqual(self.dispatch_check(dep).returncode, 0)
        self.assertEqual(self.dispatch_check(independent).returncode, 0)

    def test_worker_ship_is_ready_artifact_and_does_not_certify_integration(self):
        self.plan()
        aid, task = self.assign()
        self.assertEqual(self.calculate(self.root), 3)
        head = self.complete_artifact(aid, task, "alpha")
        self.assertEqual(self.calculate(self.root), 3)
        self.assertEqual(self.calculate(self.tasks[task]), 2)
        self.mutation("settle", assignment_id=aid, revision=1,
                      evidence=["An arbitrary sentence cannot certify a merge"])
        work = self.goal()["works"]["alpha"]
        self.assertEqual(work["status"], "waiting_integration")
        self.assertTrue(work["artifact_ready"])
        self.assertEqual(self.calculate(self.root), 3)
        self.mutation("settle", assignment_id=aid, revision=1,
                      integration={"task_id": "nonexistent", "worktree": str(self.root), "head": head},
                      evidence=["An invented integration task cannot certify a merge"], ok=False)
        self.integrate(aid, task, head)
        self.assertEqual(self.goal()["works"]["alpha"]["status"], "settled")
        self.assertEqual(self.calculate(self.root), 2)
        self.assertEqual(self.git(self.root, "rev-parse", "HEAD"), head)

    def test_artifact_task_cannot_certify_its_own_integration(self):
        self.plan()
        aid, task = self.assign()
        head = self.complete_artifact(aid, task, "alpha")
        before = self.goal()
        self.assertEqual(self.calculate(self.tasks[task]), 2)
        self.assertEqual(self.calculate(self.root), 3)
        result, _ = self.mutation("settle", assignment_id=aid, revision=1,
            integration={"task_id": task, "worktree": str(self.tasks[task]), "head": head},
            evidence=["The worker completion alone is not destination integration"], ok=False)
        self.assertIn("integration target must use a different task", result.stderr)
        self.assertEqual(self.goal(), before)
        self.assertEqual(self.calculate(self.root), 3)

    def test_distinct_integration_task_cannot_reuse_artifact_checkout(self):
        self.plan()
        aid, task = self.assign()
        head = self.complete_artifact(aid, task, "alpha")
        path = self.tasks[task]
        target_task = "second-task-in-artifact-checkout"
        cfg = config.RepoConfig(path)
        lifecycle.open_task(self.conn, cfg, task_id=target_task,
                            request="Check the completed artifact again", scope_globs=["app.py"])
        ledger.insert(self.conn, "claim", id="proof-" + target_task, task_id=target_task, kind="probe",
                      question="Does this checkout execute the requested deduction?", checker="probe",
                      subject_refs=[{"kind": "file", "path": "app.py"}], origin="derive", created_at="2026")
        checked = lifecycle.check(self.conn, cfg, target_task)
        self.assertEqual(checked[0][2].exit_code, 0, checked[0][2].stderr)
        ok, report = lifecycle.ship(self.conn, cfg, target_task)
        self.assertTrue(ok, report)
        alias = self.home / "artifact-checkout-alias"
        alias.symlink_to(path, target_is_directory=True)
        self.assertEqual(alias.resolve(strict=True), path)
        before = self.goal()
        for target in (path, alias):
            result, _ = self.mutation("settle", assignment_id=aid, revision=1,
                integration={"task_id": target_task, "worktree": str(target), "head": head},
                evidence=["A second completion in the artifact checkout is not integration"], ok=False)
            self.assertIn("integration target must use a different Git worktree", result.stderr)
            self.assertEqual(self.goal(), before)
            self.assertEqual(self.calculate(path), 2)
            self.assertEqual(self.calculate(self.root), 3)

    def test_cycles_unknown_references_and_stale_dependency_versions_are_rejected(self):
        self.plan(("alpha", "beta"))
        iid = self.incoming("Update dependencies.")
        for dependencies in ([{"work_id": "missing", "revision": 1}], [{"work_id": "beta", "revision": 99}],
                             [{"work_id": "alpha", "revision": 2}]):
            with self.subTest(dependencies=dependencies):
                self.assessment(iid, "Update dependencies.", [self.change("change", "alpha", 1,
                    title="alpha", request="Updated alpha", depends_on=dependencies),
                    self.change("unaffected", "beta", 1)], ok=False)
        self.assessment(iid, "Update dependencies.", [
            self.change("change", "alpha", 1, title="alpha", request="Updated alpha", depends_on=[{"work_id": "beta", "revision": 2}]),
            self.change("change", "beta", 1, title="beta", request="Updated beta", depends_on=[{"work_id": "alpha", "revision": 2}])], ok=False)

    def test_new_input_and_revision_cas_reject_stale_update_and_dispatch(self):
        self.plan()
        aid, _ = self.assign()
        observed = self.version
        iid = self.incoming("Keep alpha unchanged.")
        self.assessment(iid, "Keep alpha unchanged.", [self.change("unaffected", "alpha", 1)], expected=observed, ok=False)
        self.assertNotEqual(self.dispatch_check(aid, expected=observed).returncode, 0)
        self.assessment(iid, "Keep alpha unchanged.", [self.change("unaffected", "alpha", 1)])
        self.assertNotEqual(self.dispatch_check(aid, expected=observed).returncode, 0)

    def test_real_concurrent_input_writers_have_one_cas_winner(self):
        self.plan()
        observed = self.version
        barrier = threading.Barrier(2)
        results = []
        def write(number):
            barrier.wait(timeout=10)
            results.append(self.cli("coordinate", "input", {"id": f"concurrent-{number}", "goal_id": "goal",
                "expected_version": observed, "text": f"Concurrent user input {number}.",
                "source": {**AUTHOR, "message_id": f"concurrent-message-{number}"}}))
        threads = [threading.Thread(target=write, args=(n,)) for n in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(r.returncode == 0 for r in results), 1,
                         [(r.returncode, r.stdout, r.stderr) for r in results])
        self.assertTrue(all("Traceback" not in r.stderr for r in results))

    def test_obsolete_late_result_is_retained_but_cannot_settle(self):
        self.plan()
        aid, task = self.assign()
        self.receipt(aid)
        old = self.version
        iid = self.incoming("Cancel alpha.")
        self.assessment(iid, "Cancel alpha.", [self.change("cancel", "alpha", 1)])
        self.mutation("result", assignment_id=aid, revision=1, expected=old,
            host_ref="native-" + aid, head_after=self.git(self.tasks[task], "rev-parse", "HEAD"),
            evidence=["Late disposable worker result"], decision="completed", reason="Worker completed before cancellation.")
        self.assertTrue(self.holds(task))
        self.mutation("settle", assignment_id=aid, revision=1,
                      evidence=["This obsolete result must not settle"], ok=False)
        self.assertIn("Late disposable worker result", json.dumps(self.goal()))

    def test_managed_task_context_does_not_invent_context_for_historical_task(self):
        self.task("legacy")
        self.assertIsNone(self.context("legacy"))
        self.assertFalse(self.holds("legacy"))
        self.plan()
        aid, task = self.assign()
        context = self.context(task)
        self.assertEqual((context["work_id"], context["revision"], context["assignment_id"]), ("alpha", 1, aid))
        self.assertEqual(context["dependency_versions"], [])

    def test_unfinished_current_worker_cannot_ship_even_with_no_claims(self):
        self.plan()
        _, task = self.assign()
        with self.assertRaisesRegex(hashing.SourceUnreadable, "completed_result_required"):
            lifecycle.ship(self.conn, config.RepoConfig(self.tasks[task]), task)
        self.assertFalse(lifecycle.completion_history(self.conn, task))

    def test_parent_source_without_agent_or_native_message_identifier_is_valid(self):
        source = {"host": "codex", "session": "actual-parent-session"}
        self.mutation("input", id="parent-input", text="Build alpha.", source=source)
        before = self.goal()
        self.assertEqual(before["inputs"][0]["source"]["session"], source["session"])
        self.mutation("input", id="empty-agent-input", text="Clarify alpha.",
                      source={**source, "agent": ""})
        self.assertEqual(len(self.goal()["inputs"]), 2)

    def test_request_redaction_happens_before_persisted_state_and_export(self):
        # Explicitly synthetic fixture pattern, not a credential-shaped value.
        sentinel = "SYNTHETIC_REDACTION_SENTINEL"
        (self.root / ".v4/secret_patterns.json").write_text(json.dumps({"patterns": [{
            "id": "fixture-sentinel", "label": "Synthetic redaction fixture", "kind": "opaque",
            "regex": sentinel, "order": 0}]}))
        self.incoming("Keep the surrounding request but remove " + sentinel + ".")
        text = self.goal()["inputs"][0]["text"]
        self.assertEqual(text, "Keep the surrounding request but remove [redacted].")
        payloads = "\n".join(r[0] for r in self.conn.execute("SELECT payload FROM event"))
        self.assertNotIn(sentinel, payloads)
        exported = self.home / "private-ledger.jsonl"
        ledger.export_jsonl(self.conn, exported, self.root)
        self.assertNotIn(sentinel, exported.read_text())
        self.assertIn("[redacted]", exported.read_text())

    def test_pending_inputs_are_assessed_in_observed_order(self):
        self.plan()
        second = self.incoming("Second message keeps alpha.")
        third = self.incoming("Third message changes alpha.")
        before = self.goal()
        self.assessment(third, "Third message changes alpha.", [self.change("change", "alpha", 1,
                        title="alpha", request="Third message alpha", depends_on=[])], ok=False)
        self.assertEqual(self.goal(), before)
        self.assertEqual(before["pending_inputs"], [second, third])
        self.assessment(second, "Second message keeps alpha.", [self.change("unaffected", "alpha", 1)])
        self.assessment(third, "Third message changes alpha.", [self.change("change", "alpha", 1,
                        title="alpha", request="Third message alpha", depends_on=[])])
        self.assertEqual(self.goal()["pending_inputs"], [])
        self.assertEqual(len(self.goal()["inputs"]), 3)
        self.assertEqual(self.goal()["works"]["alpha"]["revision"], 2)

    def test_lost_launch_receipt_and_same_id_retry_cannot_authorize_second_launch(self):
        self.plan()
        aid, _ = self.assign()
        self.assertEqual(self.dispatch_check(aid).returncode, 0)
        first, data, action = self.prelaunch(aid)
        self.assertTrue(self.launch_authorized(first))
        # The caller may have launched and crashed before recording a receipt.
        retried = self.cli("coordinate", action, data)
        self.assertFalse(self.launch_authorized(retried), retried.stdout + retried.stderr)
        self.assertNotEqual(self.dispatch_check(aid).returncode, 0)
        self.assign(task="duplicate-launch-owner", ok=False)

    def test_two_real_prelaunch_callers_have_only_one_launch_authorization(self):
        self.plan()
        aid, _ = self.assign()
        self.assertEqual(self.dispatch_check(aid).returncode, 0)
        schema = json.loads(self.cli("coordinate", "schema").stdout)
        action = "begin-dispatch" if "begin-dispatch" in schema["commands"] else "check-dispatch"
        observed = self.version
        barrier = threading.Barrier(2)
        results = []
        def launch(number):
            data = {"goal_id": "goal", "assignment_id": aid, "expected_version": observed}
            if action == "begin-dispatch":
                data["id"] = f"launch-claim-{number}"
            barrier.wait(timeout=10)
            results.append(self.cli("coordinate", action, data))
        threads = [threading.Thread(target=launch, args=(number,)) for number in (1, 2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
            self.assertFalse(thread.is_alive())
        self.assertEqual(len(results), 2)
        self.assertEqual(sum(self.launch_authorized(result) for result in results), 1,
                         [(r.returncode, r.stdout, r.stderr) for r in results])

    def test_consumer_assignment_requires_settled_artifact_in_its_actual_worktree(self):
        self.plan(("base", "consumer"))
        iid = self.incoming("Consumer uses base revision one.")
        self.assessment(iid, "Consumer uses base revision one.", [self.change("unaffected", "base", 1),
            self.change("change", "consumer", 1, title="consumer", request="Use base", depends_on=[{"work_id": "base", "revision": 1}])])
        stale_path = self.task("stale-consumer")
        base, base_task = self.assign("base")
        head = self.complete_artifact(base, base_task, "base")
        self.integrate(base, base_task, head)
        self.assertEqual(self.calculate(stale_path), 3)
        self.assertEqual(self.calculate(self.root), 2)
        self.assign("consumer", 2, task="stale-consumer", dependencies=[{"work_id": "base", "revision": 1}], ok=False)
        self.assertIsNone(self.context("stale-consumer"))
        _, current = self.assign("consumer", 2, task="current-consumer", dependencies=[{"work_id": "base", "revision": 1}])
        self.assertEqual(self.calculate(self.tasks[current]), 2)

    def test_owner_executable_mode_0700_is_valid_committed_artifact(self):
        self.plan()
        aid, task = self.assign()
        path = self.tasks[task]
        (path / "app.py").chmod(0o700)
        head = self.complete_artifact(aid, task, "alpha")
        self.assertEqual(self.calculate(path), 2)
        self.assertEqual(self.git(path, "diff", "--", "app.py"), "")
        self.mutation("settle", assignment_id=aid, revision=1,
                      evidence=["Real committed executable source with owner-only mode"])
        self.assertTrue(self.goal()["works"]["alpha"]["artifact_ready"])
        self.integrate(aid, task, head)
        self.assertEqual(self.goal()["works"]["alpha"]["status"], "settled")

    def test_delayed_dispatch_observation_after_stop_is_retained_without_reopening(self):
        self.plan()
        aid, _ = self.assign()
        self.receipt(aid)
        launched_at = self.version
        self.mutation("stop", assignment_id=aid, reason="The actual host is stopping this worker.")
        self.receipt(aid, "stopped")
        before = self.goal()["assignments"][aid]
        self.receipt(aid, "dispatched", expected=launched_at)
        after = self.goal()["assignments"][aid]
        self.assertEqual(after["dispatch_state"], "stopped")
        self.assertGreater(len(after["receipts"]), len(before["receipts"]))
        self.assign(task="replacement-after-observed-stop")

    def test_acceptance_output_reuse_preserves_every_prior_evidence_byte(self):
        out = self.home / "retained-acceptance"
        command = [sys.executable, str(SOURCE / "tools/request_acceptance.py"), "--out", str(out)]
        first = subprocess.run(command, cwd=SOURCE, text=True, capture_output=True, timeout=120)
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}
        second = subprocess.run(command, cwd=SOURCE, text=True, capture_output=True, timeout=120)
        self.assertNotEqual(second.returncode, 0, second.stdout + second.stderr)
        after = {str(p.relative_to(out)): p.read_bytes() for p in out.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        self.assertNotIn("Traceback", second.stderr)

    def test_future_child_identity_is_observed_after_atomic_launch_claim(self):
        self.plan()
        path = self.task("future-child")
        schema = json.loads(self.cli("coordinate", "schema").stdout)
        fields = {"id": "future-child-assignment", "work_id": "alpha", "revision": 1,
                  "task_id": "future-child", "worktree": str(path), "host": "codex",
                  "session": "actual-parent-session", "dependency_versions": []}
        if "agent" in schema["commands"]["assign"]["required"]:
            # The historical protocol forced callers to substitute the known
            # parent for a child that the native host had not created yet.
            fields["agent"] = ""
        self.mutation("assign", **fields)
        context = self.context("future-child")
        old_assignment = self.goal()["assignments"]["future-child-assignment"]
        identity = context["worker_identity"] if "worker_identity" in context else {
            key: old_assignment[key] for key in ("host", "session", "agent")}
        self.assertIsNone(identity)
        self.prelaunch("future-child-assignment")
        self.mutation("receipt", assignment_id="future-child-assignment", outcome="dispatched",
                      host_ref="actual-host-child-reference", evidence=["Native spawn returned the child identity"], ok=False)
        observed = {"host": "codex", "session": "actual-parent-session", "agent": "actual-child-agent"}
        self.mutation("receipt", assignment_id="future-child-assignment", outcome="dispatched", worker=observed,
                      host_ref="actual-host-child-reference", evidence=["Actual post-spawn child identity"])
        self.assertEqual(self.context("future-child")["worker_identity"], observed)
        self.mutation("receipt", assignment_id="future-child-assignment", outcome="dispatched",
                      worker={**observed, "agent": "different-child"}, host_ref="actual-host-child-reference",
                      evidence=["Conflicting later host observation is retained"])
        self.assertEqual(self.context("future-child")["worker_identity"], observed)
        self.assertTrue(self.holds("future-child"))

    def test_existing_native_path_requires_explicit_disposable_provenance(self):
        before = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result = subprocess.run([sys.executable, str(SOURCE / "tools/request_acceptance.py"),
            "--out", str(self.home / "refused-native"), "--native-host", "codex",
            "--existing-native-repo", str(self.root)], cwd=SOURCE, text=True, capture_output=True, timeout=30)
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("disposable", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        after = {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        self.assertEqual(after, before)

    def test_later_artifact_settle_preserves_integrated_status_and_owner(self):
        self.plan()
        aid, task = self.assign()
        head = self.complete_artifact(aid, task, "alpha")
        self.integrate(aid, task, head)
        before = self.goal()["assignments"][aid]
        self.mutation("settle", assignment_id=aid, revision=1,
                      evidence=["Later artifact-only accounting cannot undo the integration"])
        after = self.goal()["assignments"][aid]
        self.assertEqual(self.goal()["works"]["alpha"]["status"], "settled")
        self.assertEqual(after["status"], "settled")
        self.assertGreater(len(after["settlements"]), len(before["settlements"]))
        self.assign(task="duplicate-integrated-revision", ok=False)

    def test_unknown_launch_observation_cannot_bind_future_worker_identity(self):
        self.plan()
        path = self.task("unknown-worker")
        self.mutation("assign", id="unknown-worker-assignment", work_id="alpha", revision=1,
                      task_id="unknown-worker", worktree=str(path), host="codex", session="actual-parent-session",
                      dependency_versions=[])
        self.prelaunch("unknown-worker-assignment")
        purported = {"host": "codex", "session": "actual-parent-session", "agent": "not-yet-observed-child"}
        self.mutation("receipt", assignment_id="unknown-worker-assignment", outcome="unknown", worker=purported,
                      host_ref="uncertain-native-reference", evidence=["Launch outcome is still unknown"])
        self.assertIsNone(self.context("unknown-worker")["worker_identity"])
        result = self.cli("design", "disposition", {"id": "unknown-worker-na", "goal_id": "goal",
            "work_id": "alpha", "revision": 1, "disposition": "not_applicable",
            "reason": "Inspection changes no document meaning.", "author": AUTHOR}, root=path)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        from kernel import design
        self.assertTrue(design.task_holds(self.conn, path, "unknown-worker"))

    def test_real_timeout_retains_partial_streams_and_failed_summary_before_raising(self):
        from tools.request_acceptance import Acceptance
        acceptance = Acceptance(self.home / "timeout-evidence")
        configurable = "timeout" in inspect.signature(acceptance.run).parameters
        budget = 0.5 if configurable else 60
        script = ("import sys,time; print('PARTIAL_STDOUT',flush=True); "
                  "print('PARTIAL_STDERR',file=sys.stderr,flush=True); time.sleep(" + str(budget + 2) + ")")
        kwargs = {"timeout": budget} if configurable else {}
        with self.assertRaises(Exception) as caught:
            acceptance.run("actual timeout control", [sys.executable, "-u", "-c", script], **kwargs)
        summary = acceptance.out / "direct-cli.json"
        self.assertTrue(summary.exists(), "the actual timeout returned without a failed case summary")
        report = json.loads(summary.read_text())
        self.assertEqual(len(report["cases"]), 1)
        case = report["cases"][0]
        self.assertFalse(case["ok"])
        self.assertTrue(case["timed_out"])
        self.assertIsNone(case["code"])
        self.assertEqual(case["timeout_seconds"], budget)
        self.assertGreater(case["seconds"], 0)
        output = Path(case["log"]).read_text()
        self.assertIn("PARTIAL_STDOUT", output)
        self.assertIn("PARTIAL_STDERR", output)
        self.assertIsInstance(caught.exception, RuntimeError)
        self.assertIn("timed out", str(caught.exception))

    def test_native_subprocess_environment_isolates_outer_identity_and_keeps_runtime_auth(self):
        from tools.request_acceptance import native_child_environment
        source = dict(os.environ)
        # Explicitly synthetic environment inputs; no service is contacted.
        source.update({"CODEX_SESSION_ID": "synthetic-outer-session", "CODEX_THREAD_ID": "synthetic-outer-agent",
            "CODEX_APP_TOOLS_PIPE_PATH": "/tmp/synthetic-app-pipe", "CODEX_TOOL_CALL_ID": "synthetic-call",
            "CODEX_TASK_WORKSPACE_VERIFYING_IDENTITY": "synthetic-verifier", "CLAUDE_SESSION_ID": "synthetic-claude-session",
            "CLAUDE_CODE_AGENT_ID": "synthetic-claude-agent", "V4_TASK": "synthetic-parent-task",
            "V4_TRACE_OUT": "/tmp/synthetic-trace", "PYTHONPATH": "/tmp/synthetic-sitecustomize",
            "CODEX_HOME": "/tmp/synthetic-runtime-home", "OPENAI_API_KEY": "synthetic-auth-fixture"})
        clean, stripped = native_child_environment(source)
        observed_names = ["CODEX_SESSION_ID", "CODEX_THREAD_ID", "CODEX_APP_TOOLS_PIPE_PATH", "CODEX_TOOL_CALL_ID",
            "CODEX_TASK_WORKSPACE_VERIFYING_IDENTITY", "CLAUDE_SESSION_ID", "CLAUDE_CODE_AGENT_ID", "V4_TASK",
            "V4_TRACE_OUT", "PYTHONPATH"]
        script = ("import os,json; print(json.dumps({'outer_names':[k for k in " + repr(observed_names)
                  + " if k in os.environ], 'auth_preserved':os.environ.get('OPENAI_API_KEY')=='synthetic-auth-fixture',"
                    "'runtime_home_preserved':os.environ.get('CODEX_HOME')=='/tmp/synthetic-runtime-home'}))")
        result = subprocess.run([sys.executable, "-c", script], cwd=self.root, env=clean,
                                capture_output=True, text=True, check=True, timeout=10)
        observed = json.loads(result.stdout)
        self.assertEqual(observed["outer_names"], [])
        self.assertTrue(observed["auth_preserved"])
        self.assertTrue(observed["runtime_home_preserved"])
        self.assertLessEqual(set(observed_names), set(stripped))
        self.assertEqual(source["CODEX_SESSION_ID"], "synthetic-outer-session")

    def test_obsolete_development_proof_arguments_refuse_without_creating_output(self):
        cases = [["--parent-commit", "0" * 40,
                  "--test-locator", "tests.test_coordination.Coordination.test_lost_launch_receipt_and_same_id_retry_cannot_authorize_second_launch",
                  "--target-file", "kernel/coordination.py", "--target-symbol", "check_dispatch"]]
        cases += [[flag, "unused-synthetic-value"] for flag in
                  ("--parent-proof", "--locator", "--target-file", "--target-symbol", "--parent")]
        for number, arguments in enumerate(cases):
            with self.subTest(arguments=arguments):
                out = self.home / ("obsolete-proof-" + str(number))
                result = subprocess.run([sys.executable, str(SOURCE / "tools/request_acceptance.py"),
                    "--out", str(out), *arguments], cwd=SOURCE, text=True, capture_output=True, timeout=10)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertFalse(out.exists())

    def test_actual_session_identity_row_is_separate_from_guard_coverage_summary(self):
        from kernel import hosts
        session = "synthetic-actual-hook-session"
        payload = {"cwd": str(self.root), "session_id": session, "hook_event_name": "SessionStart"}
        result = subprocess.run([sys.executable, str(SOURCE / "hooks/session_context.py")],
            cwd=self.root, env={**self.env, "V4_HOST": "codex"}, input=json.dumps(payload),
            capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(session, context)
        events = [json.loads(row[0]) for row in self.conn.execute("SELECT payload FROM event WHERE kind='hook_seen'")]
        identities = [event for event in events if event.get("hook") == "session_context"
                      and event.get("hook_event_name") == "SessionStart" and event.get("session") == session]
        self.assertEqual(len(identities), 1)
        self.assertEqual(identities[0]["basis"], "identity")
        self.assertEqual(identities[0]["host"], "codex")
        self.assertEqual(hosts.evidence(self.conn, host="codex", session=session)["observed"], [])

    def test_pending_input_between_entry_check_and_final_append_blocks_ship(self):
        self.plan()
        aid, task = self.assign()
        self.complete_artifact(aid, task, "alpha", ship=False)
        marker = {"ship_round": False, "input_observed": False}

        def interleave(statement):
            # SQLite calls this before executing a statement. The second real
            # CLI connection commits after the ship-round append, before the
            # final BEGIN IMMEDIATE; no production decision is replaced.
            if statement.lstrip().upper().startswith("INSERT INTO EVENT") and "ship_round" in statement:
                marker["ship_round"] = True
            if statement == "BEGIN IMMEDIATE" and marker["ship_round"] and not marker["input_observed"]:
                marker["input_observed"] = True
                self.incoming("New input arrived while completion was running.")

        self.conn.set_trace_callback(interleave)
        try:
            with self.assertRaisesRegex(hashing.SourceUnreadable, "pending_input"):
                lifecycle.ship(self.conn, config.RepoConfig(self.tasks[task]), task)
        finally:
            self.conn.set_trace_callback(None)
        self.assertTrue(marker["input_observed"])
        self.assertFalse(lifecycle.completion_history(self.conn, task))
        self.assertEqual(len(self.goal()["pending_inputs"]), 1)

    def test_conflicting_late_result_preserves_settlement_but_revokes_readiness(self):
        self.plan()
        aid, task = self.assign()
        head = self.complete_artifact(aid, task, "alpha")
        self.integrate(aid, task, head)
        history = lifecycle.completion_history(self.conn, task)
        self.mutation("result", assignment_id=aid, revision=1, host_ref="native-" + aid,
                      head_after=head, decision="failed", reason="A late real observation contradicts readiness.",
                      evidence=["Late failed worker observation"])
        self.assertEqual(self.goal()["works"]["alpha"]["status"], "conflicting_result")
        self.assertEqual(lifecycle.completion_history(self.conn, task), history)
        self.assertTrue(self.holds(task))

    def test_disposable_acceptance_program_executes_its_real_cli_scenario(self):
        out = self.home / "acceptance-output"
        result = subprocess.run([sys.executable, str(SOURCE / "tools/request_acceptance.py"), "--out", str(out)],
                                cwd=SOURCE, text=True, capture_output=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((out / "direct/direct-cli.json").read_text())
        self.assertTrue(report["direct_cli_pass"])
        self.assertGreater(len(report["cases"]), 15)
        self.assertTrue(all(case["ok"] for case in report["cases"]))
        self.assertEqual(report["observed"]["alpha_revision"], 2)
        self.assertTrue(report["observed"]["obsolete_holds"])
        self.assertEqual(report["observed"]["beta_design_holds"], [])

    def test_assume_unchanged_source_cannot_certify_committed_artifact(self):
        self.plan()
        aid, task = self.assign()
        self.complete_artifact(aid, task, "alpha")
        path = self.tasks[task]
        self.git(path, "update-index", "--assume-unchanged", "app.py")
        (path / "app.py").write_text("def calculate(value):\n    return value + 3\n")
        self.assertEqual(self.git(path, "diff", "--", "app.py"), "")
        self.assertEqual(self.calculate(path), 8)
        self.mutation("settle", assignment_id=aid, revision=1,
                      evidence=["Git status hiding content is not a committed artifact"], ok=False)
        self.assertNotEqual(self.goal()["works"]["alpha"]["status"], "settled")

    def test_redaction_cannot_change_stable_input_id_or_host_identity(self):
        # Synthetic patterns deliberately target protocol coordinates, not secrets.
        for field, marker in (("id", "synthetic-operation-id"), ("session", "synthetic-native-session")):
            with self.subTest(field=field):
                (self.root / ".v4/secret_patterns.json").write_text(json.dumps({"patterns": [{
                    "id": "fixture-coordinate", "label": "Synthetic coordinate masking fixture", "kind": "opaque",
                    "regex": marker, "order": 0}]}))
                data = {"id": marker if field == "id" else "stable-input", "goal_id": "goal",
                        "expected_version": 0, "text": "Keep the request as supplied.",
                        "source": {"host": "codex", "session": marker if field == "session" else "native-session"}}
                before = self.goal()
                result = self.cli("coordinate", "input", data)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(self.goal(), before)


if __name__ == "__main__":
    unittest.main()
