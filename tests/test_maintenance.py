"""Maintenance asks the real state owner and cannot manufacture completed review."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from kernel import attention, config, doctor, hashing, ledger, lifecycle, maintenance, review, sweep

SOURCE = Path(__file__).resolve().parent.parent
APP = "def calculate(value):\n    return value - 2  # fixed deduction\n"


class Maintenance(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory(prefix="v4-maintenance-")
        self.addCleanup(td.cleanup)
        self.root = Path(td.name) / "main"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Maintenance test")
        self.git("config", "user.email", "maintenance@example.invalid")
        (self.root / ".v4").mkdir()
        (self.root / "checkers").mkdir()
        (self.root / ".gitignore").write_text("__pycache__/\n")
        (self.root / "app.py").write_text(APP)
        (self.root / "test_app.py").write_text("import unittest\nfrom app import calculate\nclass Check(unittest.TestCase):\n    def test_difference(self):\n        self.assertEqual(calculate(5), 3)\n")
        shutil.copyfile(SOURCE / "checkers/review_finding.py", self.root / "checkers/review_finding.py")
        (self.root / ".v4/config.json").write_text(json.dumps({"test_command": "python3 -m unittest test_app", "policy": "allow_accepted_risk"}))
        (self.root / ".v4/claim_kinds.json").write_text(json.dumps({"review-finding": {"checker": "review-finding", "question_template": "q", "staleness": "subject", "gate": "ship"}}))
        (self.root / ".v4/checkers.json").write_text(json.dumps({"review-finding": {"path": "checkers/review_finding.py", "sha256": hashing.file_sha(self.root / "checkers/review_finding.py"), "reads": ["**"]}}))
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "fixture baseline")
        self.cfg = config.RepoConfig(self.root)

    def git(self, *args):
        return subprocess.check_output(["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args], cwd=self.root, text=True, stderr=subprocess.PIPE).strip()

    def connect(self, root=None):
        c = ledger.connect(root or self.root)
        self.addCleanup(c.close)
        return c

    def finding(self, c, cfg, task=None):
        cid, _, _ = review.raise_finding(c, cfg, task_id=task, file="app.py", symbol="calculate", note="Subtraction must use the fixed deduction.", lens="probe")
        review.bind_closing_test(c, claim_id=cid, root=cfg.root, test_path="test_app.py",
                                command=[sys.executable, "-m", "unittest", "test_app"],
                                mutation=("app.py", "    return value - 2  # fixed deduction", "    return value + 2  # deliberately wrong"))
        return cid

    def lenses(self, names=("probe", "other")):
        d = self.root / ".v4/lenses"
        d.mkdir(exist_ok=True)
        for name in names:
            (d / (name + ".json")).write_text(json.dumps({"name": name, "source": "test", "checks": ["Inspect the calculation"], "anti_patterns": []}))

    def report_lens(self, c, run, slug):
        lens = review.lenses(self.root)[slug]
        review.record_lens_run(c, slug=slug, lens=lens, root=self.root, run_id=run)
        review.record_lens_reviewed(c, self.root, slug=slug, findings=0, run_id=run)

    def test_status_is_read_only_when_no_ledger_exists(self):
        result = maintenance.status(self.root)
        self.assertFalse(result["attention"]["available"])
        self.assertFalse(ledger.ledger_path(self.root).exists())
        self.assertFalse((maintenance.private_dir(self.root) / "maintenance.json").exists())

    def test_schedule_cli_rejects_bad_arguments_without_replacing_observation(self):
        def call(data):
            return subprocess.run(
                [sys.executable, "-m", "kernel.cli", "--repo", str(self.root),
                 "maintain", "schedule", "--data", "-"], cwd=SOURCE,
                input=json.dumps(data), text=True, capture_output=True, timeout=20)

        valid = {"host": "codex", "job_id": "fixture-job", "status": "paused",
                 "evidence": "Synthetic protocol fixture, not an actual native job",
                 "scheduled_prompt": "Read the disposable fixture only"}
        result = call(valid)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        before = maintenance.settings(self.root)
        for data in (dict(valid, observed_at="output-only field"),
                     dict(valid, unexpected="value"),
                     dict(valid, root=str(self.root.parent)),
                     {k: v for k, v in valid.items() if k != "host"}):
            with self.subTest(data=data):
                result = call(data)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("REFUSED:", result.stderr)
                self.assertNotIn("Traceback", result.stderr)
                self.assertEqual(maintenance.settings(self.root), before)

    def test_attention_uses_worker_worktree_not_the_callers_tree(self):
        worker = self.root.parent / "worker"
        self.git("worktree", "add", "--detach", str(worker), "HEAD")
        c = self.connect(worker)
        cfg = config.RepoConfig(worker)
        lifecycle.open_task(c, cfg, task_id="t-worker", request="Repair app.py", scope_globs=["app.py", "test_app.py"])
        cid = self.finding(c, cfg, "t-worker")
        lifecycle.check(c, cfg, "t-worker", only=[cid])
        self.assertFalse(attention.scan(self.root)["items"])
        (worker / "app.py").write_text(APP.replace("value - 2", "value + 2"))
        result = attention.scan(self.root)
        self.assertEqual(result["errors"], [])
        self.assertEqual([(r["claim"], r["state"]) for r in result["items"]], [(cid, "STALE")])
        self.assertEqual(result["items"][0]["worktree"], str(worker.resolve()))
        self.assertEqual(result["items"][0]["action"], "check")

    def test_doctor_realerts_after_a_previously_passing_claim_regresses(self):
        c = self.connect()
        cid = self.finding(c, self.cfg)
        lifecycle.check(c, self.cfg, ledger.REVIEW_TASK, only=[cid])
        self.assertEqual(attention.scan(self.root)["items"], [])
        (self.root / "app.py").write_text(APP.replace("value - 2", "value + 2"))
        out = []
        try:
            doctor._check_findings_a_review_raised_and_nobody_closed(self.root, out)
        finally:
            doctor._close_reader()
        self.assertTrue(any(r["what"] == "review findings" and r["status"] == "warn" for r in out), out)

    def test_empty_legacy_sweep_does_not_reset_cadence(self):
        c = self.connect()
        result = sweep.record(c, lenses=["probe"], findings=0)
        self.assertFalse(result["complete"])
        self.assertIsNone(sweep.last(c))
        self.assertTrue(sweep.due(c, self.cfg)[0])

    def test_partial_and_complete_are_distinct(self):
        self.lenses()
        c = self.connect()
        maintenance.start(self.root, run_id="first")
        self.report_lens(c, "first", "probe")
        result = maintenance.finish(self.root, "first")
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["missing"], ["other"])
        self.assertIsNone(sweep.last(c))
        maintenance.start(self.root, run_id="second")
        for name in ("probe", "other"):
            self.report_lens(c, "second", name)
        self.assertEqual(maintenance.finish(self.root, "second")["status"], "complete")
        self.assertIsNotNone(sweep.last(c))

    def test_selected_lens_completion_does_not_postpone_the_full_review(self):
        self.lenses();c=self.connect()
        maintenance.start(self.root,run_id="selected",selected=["probe"])
        self.report_lens(c,"selected","probe")
        self.assertEqual(maintenance.finish(self.root,"selected")["status"],"complete")
        self.assertIsNone(sweep.last(c),"one selected lens is not the full applicable review")
        self.assertTrue(maintenance.status(self.root)["cadence"]["due"])

    def test_changed_fixture_makes_monitor_review_due_without_waiting_four_days(self):
        self.lenses(("probe",));c=self.connect()
        fixture=self.root/'.v4/fixtures/review/case.py';fixture.parent.mkdir(parents=True)
        fixture.write_text('valid = True\n')
        registry=self.root/'.v4/checkers.json';data=json.loads(registry.read_text())
        data['review-finding']['fixtures']='.v4/fixtures/review';registry.write_text(json.dumps(data))
        maintenance.start(self.root,run_id="full")
        self.report_lens(c,"full","probe");maintenance.finish(self.root,"full")
        self.assertFalse(maintenance.status(self.root)["cadence"]["due"])
        fixture.write_text('valid = False\n')
        current=maintenance.status(self.root)
        self.assertTrue(current["cadence"]["due"])
        self.assertTrue(current["monitor"]["due"])

    def test_changed_criteria_do_not_override_a_configured_weekday(self):
        from datetime import datetime, timezone
        p=self.root/'.v4/config.json';data=json.loads(p.read_text())
        data['lens_sweep']={'weekday':(datetime.now(timezone.utc).weekday()+1)%7}
        p.write_text(json.dumps(data));self.connect()
        current=maintenance.status(self.root)
        self.assertTrue(current["monitor"]["due"])
        self.assertFalse(current["cadence"]["due"])

    def test_source_changes_invalidate_review_completion(self):
        self.lenses(("probe",))
        c = self.connect()
        maintenance.start(self.root, run_id="changed")
        self.report_lens(c, "changed", "probe")
        (self.root / "app.py").write_text(APP.replace("value - 2", "value + 2"))
        self.assertEqual(maintenance.finish(self.root, "changed")["status"], "stale")
        self.assertIsNone(sweep.last(c))

    def test_missing_context_cannot_be_disguised_as_inapplicable(self):
        self.lenses(("probe",))
        path = self.root / ".v4/lenses/probe.json"
        data = json.loads(path.read_text()); data["requires_context"] = True
        path.write_text(json.dumps(data))
        c = self.connect()
        maintenance.start(self.root, run_id="context", selected=["probe"])
        review.record_lens_run(c, slug="probe", lens=review.lenses(self.root)["probe"], root=self.root, run_id="context")
        with self.assertRaises(ValueError):
            review.record_lens_reviewed(c, self.root, slug="probe", findings=0, run_id="context", result="not_applicable", note="No request")
        review.record_lens_reviewed(c, self.root, slug="probe", findings=0, run_id="context", result="not_evaluable", note="The requested input is missing")
        self.assertEqual(maintenance.finish(self.root, "context")["status"], "partial")

    def test_two_hosts_cannot_own_the_same_maintenance_run(self):
        self.lenses(("probe",))
        maintenance.start(self.root, run_id="one", host="claude", session="s1")
        with self.assertRaises(ValueError):
            maintenance.start(self.root, run_id="two", host="codex", session="s2")
        self.assertEqual(maintenance.start(self.root, run_id="one", host="claude", session="s1")["id"], "one")

    def test_handoff_cannot_silently_change_claim_or_redispatch(self):
        self.lenses(("probe",))
        c = self.connect(); cid = self.finding(c, self.cfg)
        run = maintenance.start(self.root, run_id="r")
        base = {"id": "h", "run_id": "r"}
        maintenance.handoff(self.root, {**base, "action": "prepare", "role": "monitor", "claim": cid})
        maintenance.handoff(self.root, {**base, "action": "dispatch_unknown"})
        maintenance.handoff(self.root, {**base, "action": "dispatched", "host_ref": "fixture-agent-reference"})
        with self.assertRaises(ValueError):
            maintenance.handoff(self.root, {**base, "action": "dispatched", "host_ref": "another-agent"})
        result = {**base, "action": "result", "decision": "needs_fix", "reason": "The subtraction contract is not met", "evidence": ["app.py"], "reviewed_head": run["snapshot"]["head"], "acceptance": "The real deduction test must pass"}
        with self.assertRaises(ValueError):
            maintenance.handoff(self.root, {**result, "claim": "wrong"})
        self.assertEqual(maintenance.handoff(self.root, result)["claim"], cid)
        self.assertEqual(c.execute("SELECT count(*) FROM accepted_risk").fetchone()[0], 0)

    def repair_worktree(self, c, task="repair"):
        target=self.root.parent / task
        self.git("worktree","add","--detach",str(target),"HEAD")
        lifecycle.open_task(c,config.RepoConfig(target),task_id=task,request="Repair app.py",scope_globs=["app.py"])
        return target

    def test_worker_handoff_needs_explicit_scope_authorization(self):
        self.lenses(("probe",))
        c = self.connect();cid = self.finding(c,self.cfg)
        self.repair_worktree(c)
        maintenance.start(self.root,run_id="repair-run")
        data={"id":"worker","run_id":"repair-run","action":"prepare","role":"worker","claim":cid,"task":"repair"}
        with self.assertRaises(ValueError):maintenance.handoff(self.root,data)
        maintenance.configure(self.root,mode="repair",repair_scope=["tests/**"])
        with self.assertRaises(ValueError):maintenance.handoff(self.root,data)
        maintenance.configure(self.root,mode="repair",repair_scope=["app.py"])
        self.assertEqual(maintenance.handoff(self.root,data)["status"],"prepared")

    def test_partial_run_can_resume_without_borrowing_another_runs_coverage(self):
        self.lenses()
        c=self.connect()
        maintenance.start(self.root,run_id="resume")
        self.report_lens(c,"resume","probe")
        self.assertEqual(maintenance.finish(self.root,"resume")["status"],"partial")
        maintenance.resume_run(self.root,"resume")
        self.report_lens(c,"resume","other")
        self.assertEqual(maintenance.finish(self.root,"resume")["status"],"complete")

    def test_reported_findings_must_reference_real_ledger_findings(self):
        self.lenses(("probe",))
        c=self.connect()
        maintenance.start(self.root,run_id="counts")
        review.record_lens_run(c,slug="probe",lens=review.lenses(self.root)["probe"],root=self.root,run_id="counts")
        review.record_lens_reviewed(c,self.root,slug="probe",findings=5,run_id="counts")
        result=maintenance.finish(self.root,"counts")
        self.assertEqual(result["status"],"partial")
        self.assertEqual(result["finding_count_mismatches"]["probe"],{"reported":5,"recorded":0})
        self.assertIsNone(sweep.last(c))

    def test_cross_process_ownership_is_atomic(self):
        self.lenses(("probe",))
        code="from kernel.maintenance import start; import sys; start(sys.argv[1],run_id=sys.argv[2],host=sys.argv[3])"
        processes=[subprocess.Popen([sys.executable,"-c",code,str(self.root),name,host],cwd=SOURCE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
                   for name,host in [("a","claude"),("b","codex")]]
        results=[p.communicate(timeout=30) for p in processes]
        self.assertEqual(sorted(p.returncode for p in processes),[0,1],results)
        self.assertEqual(len(maintenance.status(self.root)["runs"]),1)

    def test_limits_reject_unread_keys_and_wrong_types_without_writing(self):
        for limits in (["one cycle"], {"manual_cycles": 1}, {"max_parallel_agents": True}, {"max_run_seconds": 0}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                maintenance.configure(self.root, limits=limits)
        self.assertFalse((maintenance.private_dir(self.root) / "maintenance.json").exists())

    def test_capacity_is_released_by_result_and_deadline_does_not_drop_results(self):
        self.lenses(("probe",))
        maintenance.configure(self.root, limits={"max_parallel_agents": 1, "max_run_seconds": 1})
        run=maintenance.start(self.root,run_id="capacity")
        base={"id":"first","run_id":"capacity"}
        maintenance.handoff(self.root,{**base,"action":"prepare","role":"monitor"})
        with self.assertRaises(ValueError):
            maintenance.handoff(self.root,{"id":"second","run_id":"capacity","action":"prepare","role":"reviewer"})
        maintenance.handoff(self.root,{**base,"action":"dispatched","host_ref":"real-test-agent"})
        c=self.connect()
        maintenance.event(c,maintenance.RUN,{"id":"capacity","started_at":"2000-01-01T00:00:00+00:00"})
        maintenance.handoff(self.root,{**base,"action":"result","decision":"no_findings","reason":"Inspected subtraction","evidence":["app.py:1"],"reviewed_head":run["snapshot"]["head"]})
        with self.assertRaisesRegex(ValueError,"expired"):
            maintenance.handoff(self.root,{"id":"second","run_id":"capacity","action":"prepare","role":"reviewer"})

    def test_late_worker_result_preserves_real_settlement_and_cannot_use_link_repair(self):
        self.lenses(("probe",)); c=self.connect(); cid=self.finding(c,self.cfg)
        self.repair_worktree(c)
        maintenance.configure(self.root,mode="repair",repair_scope=["app.py"])
        run=maintenance.start(self.root,run_id="late")
        base={"id":"worker","run_id":"late"}
        maintenance.handoff(self.root,{**base,"action":"prepare","role":"worker","claim":cid,"task":"repair"})
        maintenance.handoff(self.root,{**base,"action":"dispatched","host_ref":"test-worker"})
        settled=maintenance.handoff(self.root,{**base,"action":"settle"})
        self.assertEqual(settled["claim_state"],"ANSWERED")
        with self.assertRaises(ValueError):
            maintenance.handoff(self.root,{**base,"action":"link_repair","task":"repair","decision":"work_completed"})
        result=maintenance.handoff(self.root,{**base,"action":"result","decision":"work_failed","reason":"Worker reported an additional failure","evidence":["test_app.py"],"reviewed_head":run["snapshot"]["head"]})
        self.assertEqual(result["status"],"settled")
        self.assertEqual(result["attempt"],settled["attempt"])
        self.assertTrue(result["needs_attention"])

    def test_multiple_findings_require_actual_coordination_assessment(self):
        self.lenses(("probe",)); c=self.connect()
        (self.root/'.v4/review_contract.json').write_text(json.dumps({"schema":1,"checks":[{"id":"dedup","check":"Compare impacts"}]}))
        maintenance.start(self.root,run_id="coord")
        review.record_lens_run(c,slug="probe",lens=review.lenses(self.root)["probe"],root=self.root,run_id="coord")
        ids=[]
        for note in ("Incorrect deduction", "Negative inputs are not rejected"):
            cid,_,_=review.raise_finding(c,self.cfg,task_id=None,file="app.py",symbol="calculate",note=note,lens="probe")
            maintenance.observe_finding(c,self.root,"coord","probe",cid);ids.append(cid)
        review.record_lens_reviewed(c,self.root,slug="probe",findings=2,run_id="coord")
        self.assertEqual(maintenance.finish(self.root,"coord")["missing_coordination"],["dedup"])
        self.assertIsNone(sweep.last(c))
        maintenance.resume_run(self.root,"coord")
        result=maintenance.finish(self.root,"coord",coordination={"dedup":{"result":"completed","note":"Arithmetic and input validity have independent acceptance conditions; retain both.","claims":ids}})
        self.assertEqual(result["status"],"complete")

    def test_doctor_distinguishes_prose_job_and_scheduled_execution(self):
        self.lenses(("probe",));c=self.connect()
        (self.root/'README.md').write_text("Please schedule a sweep")
        self.assertFalse(any(maintenance.wiring(self.root)["entrypoints"].values()))
        maintenance.observe_schedule(self.root,host="codex",job_id="native-1",status="active",evidence="Host observation")
        self.assertEqual(maintenance.wiring(self.root)["executions"],{})
        maintenance.start(self.root,run_id="manual")
        self.report_lens(c,"manual","probe");maintenance.finish(self.root,"manual")
        self.assertEqual(maintenance.wiring(self.root)["executions"],{})
        maintenance.start(self.root,run_id="scheduled",host="codex",trigger="scheduled",job_id="native-1")
        self.assertEqual(maintenance.wiring(self.root)["executions"]["codex"]["native-1"]["status"],"running")
        self.report_lens(c,"scheduled","probe");maintenance.finish(self.root,"scheduled")
        self.assertEqual(maintenance.wiring(self.root)["executions"]["codex"]["native-1"]["status"],"complete")
        maintenance.observe_schedule(self.root,host="codex",job_id="native-1",status="paused",evidence="Host paused observation")
        with self.assertRaises(ValueError):maintenance.start(self.root,host="codex",trigger="scheduled",job_id="native-1")

    def test_session_summary_never_runs_attention_or_creates_a_ledger(self):
        with patch.object(attention,"scan",side_effect=AssertionError("hook must not scan")):
            self.assertIn("no attention",maintenance.session_summary(self.root))
        self.assertFalse(ledger.ledger_path(self.root).exists())
        self.lenses(("probe",));c=self.connect();maintenance.start(self.root,run_id="summary")
        with patch.object(attention,"scan",side_effect=AssertionError("hook must not scan")):
            self.assertIn("cached summary",maintenance.session_summary(self.root))
        maintenance.event(c,maintenance.RUN,{"id":"summary","attention":{"root":str(self.root.resolve()),"as_of":"2000-01-01T00:00:00+00:00"}})
        self.assertIn("expired",maintenance.session_summary(self.root))

    def test_dev_starting_after_prepare_blocks_dispatch_without_double_dispatch(self):
        self.lenses(("probe",)); c=self.connect();cid=self.finding(c,self.cfg)
        target=self.repair_worktree(c)
        maintenance.configure(self.root,mode="repair",repair_scope=["app.py"])
        maintenance.start(self.root,run_id="race")
        base={"id":"worker","run_id":"race"}
        maintenance.handoff(self.root,{**base,"action":"prepare","role":"worker","task":"repair","claim":cid})
        self.assertTrue(maintenance.handoff(self.root,{**base,"action":"check_dispatch"})["ready"])
        lifecycle.open_task(c,config.RepoConfig(target),task_id="user-dev",request="New user development",scope_globs=["app.py"])
        with self.assertRaisesRegex(ValueError,"occupied"):
            maintenance.handoff(self.root,{**base,"action":"check_dispatch"})
        result=maintenance.handoff(self.root,{**base,"action":"dispatched","host_ref":"already-sent-agent"})
        self.assertEqual(result["status"],"dispatch_unknown")
        self.assertEqual(result["host_ref"],"already-sent-agent")
        with self.assertRaises(ValueError):maintenance.handoff(self.root,{**base,"action":"check_dispatch"})

    def test_failed_monitor_result_prevents_complete_sweep(self):
        self.lenses(("probe",));c=self.connect();run=maintenance.start(self.root,run_id="monitor-failed")
        self.report_lens(c,run["id"],"probe")
        base={"id":"monitor","run_id":run["id"]}
        maintenance.handoff(self.root,{**base,"action":"prepare","role":"monitor"})
        maintenance.handoff(self.root,{**base,"action":"dispatched","host_ref":"monitor-agent"})
        maintenance.handoff(self.root,{**base,"action":"result","decision":"review_failed","reason":"Required artifact was unreadable","evidence":["artifact-read-error"],"reviewed_head":run["snapshot"]["head"]})
        self.assertEqual(maintenance.finish(self.root,run["id"])["status"],"partial")
        self.assertIsNone(sweep.last(c))

    def test_foreground_result_waits_for_the_actual_host_receipt(self):
        self.lenses(("probe",));c=self.connect();run=maintenance.start(self.root,run_id="foreground")
        self.report_lens(c,run["id"],"probe")
        base={"id":"monitor","run_id":run["id"]}
        maintenance.handoff(self.root,{**base,"action":"prepare","role":"monitor"})
        result=maintenance.handoff(self.root,{**base,"action":"result","decision":"no_findings","reason":"Reviewed applicable criteria","evidence":["criteria.json"],"reviewed_head":run["snapshot"]["head"]})
        self.assertEqual(result["status"],"result_pending_receipt")
        self.assertEqual(maintenance.finish(self.root,run["id"])["status"],"partial")
        receipt=maintenance.handoff(self.root,{**base,"action":"dispatched","host_ref":"actual-foreground-agent"})
        self.assertEqual(receipt["status"],"result")
        self.assertEqual(receipt["decision"],"no_findings")
        maintenance.resume_run(self.root,run["id"])
        self.assertEqual(maintenance.finish(self.root,run["id"])["status"],"complete")

    def test_foreground_worker_edits_do_not_make_its_receipt_a_conflict(self):
        self.lenses(("probe",));c=self.connect();cid=self.finding(c,self.cfg)
        target=self.repair_worktree(c);maintenance.configure(self.root,mode="repair",repair_scope=["app.py"])
        run=maintenance.start(self.root,run_id="worker-first")
        base={"id":"worker","run_id":run["id"]}
        maintenance.handoff(self.root,{**base,"action":"prepare","role":"worker","task":"repair","claim":cid})
        maintenance.handoff(self.root,{**base,"action":"check_dispatch"})
        (target/'app.py').write_text(APP+'\n# worker implementation change\n')
        maintenance.handoff(self.root,{**base,"action":"result","decision":"work_completed","reason":"Implemented scoped change","evidence":["app.py"],"reviewed_head":run["snapshot"]["head"]})
        receipt=maintenance.handoff(self.root,{**base,"action":"dispatched","host_ref":"foreground-worker"})
        self.assertEqual(receipt["status"],"result")
        self.assertNotIn("needs_attention",receipt)


if __name__ == "__main__":
    unittest.main()
