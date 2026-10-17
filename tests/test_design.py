"""Document meaning needs current, independent evidence rather than headings."""
import hashlib
import json
import unittest

from kernel import design, ledger
from tests.test_coordination import AUTHOR, CoordinationFixture

REVIEWER = {"host": "claude", "session": "review-session", "agent": "independent-reviewer"}


class Design(CoordinationFixture):
    def dmutation(self, action, *, ok=True, root=None, **fields):
        self.counter += 1
        data = {"id": f"design-operation-{self.counter}", **fields}
        result = self.cli("design", action, data, root=root)
        if ok:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertNotIn("Traceback", result.stderr)
        return result, data

    def register(self, *, design_id="architecture", revision=0, path="ARCHITECTURE.md",
                 basis="approved_intent", input_id=None, quote="Build alpha.", author=None, ok=True):
        return self.dmutation("register", design_id=design_id, expected_design_revision=revision,
            path=path, coverage=[{"path": "app.py", "reference": "calculator contract"}], basis=basis,
            intent_refs=[{"input_id": input_id or self.input_id, "quote": quote}], author=author or AUTHOR,
            evidence=["Read the disposable calculator and this document"], ok=ok)

    def disposition(self, *, work="alpha", revision=1, disposition="required", ok=True):
        fields = {"goal_id": "goal", "work_id": work, "revision": revision,
                  "disposition": disposition, "reason": "The calculator contract describes this work.",
                  "author": AUTHOR}
        if disposition == "required":
            fields.update(design_ids=["architecture"], coverage=[
                {"design_id": "architecture", "reference": "calculator contract"}])
        return self.dmutation("disposition", ok=ok, **fields)

    def review(self, *, task="task-alpha", work="alpha", revision=1, design_revision=1,
               reviewer=None, result="approved", sha=None, ok=True):
        path = self.tasks[task] / "ARCHITECTURE.md"
        digest = sha or hashlib.sha256(path.read_bytes()).hexdigest()
        return self.dmutation("review", root=self.tasks[task], goal_id="goal", work_id=work, revision=revision,
            design_id="architecture", design_revision=design_revision, content_sha256=digest,
            reviewer=reviewer or REVIEWER, result=result,
            coverage=[{"reference": "calculator contract",
                       "intent_ref": {"input_id": self.input_id, "quote": getattr(self, "intent_quote", "Build alpha.")},
                       "evidence": ["The real app.py calculation matches the stated deduction."]}],
            reason="Read the stated intent, document and implementation together.",
            evidence=["Independent disposable semantic review observation"], ok=ok)

    def design_holds(self, task="task-alpha"):
        return design.task_holds(self.conn, self.tasks[task], task)

    def managed(self, works=("alpha",)):
        self.input_id = self.plan(works)
        self.intent_quote = "Build " + " and ".join(works) + "."
        self.register(quote=self.intent_quote)
        for work in works:
            self.assign(work)

    def test_existing_document_keeps_its_flexible_structure(self):
        self.input_id = self.plan()
        original = (self.root / "ARCHITECTURE.md").read_bytes()
        self.register()
        self.assertEqual((self.root / "ARCHITECTURE.md").read_bytes(), original)
        state = design.snapshot(self.conn, self.root)
        self.assertIn("architecture", state["designs"])
        self.assertEqual(state["designs"]["architecture"]["basis"], "approved_intent")
        self.assign()
        self.disposition()
        self.assertTrue(self.design_holds())
        self.review()
        self.assertFalse(self.design_holds())

    def test_missing_document_is_registered_as_uninitialized_until_authored(self):
        self.input_id = self.plan()
        self.register(path="MISSING.md", basis="observed_baseline")
        document = design.snapshot(self.conn, self.root)["designs"]["architecture"]
        self.assertEqual(document["status"], "needs_initialization")
        self.assign()
        self.disposition()
        self.assertTrue(self.design_holds())
        self.review(ok=False)
        (self.root / "MISSING.md").write_text("A paragraph written after reading the calculator.\n")
        self.register(path="MISSING.md", revision=1, basis="observed_baseline")
        updated = design.snapshot(self.conn, self.root)["designs"]["architecture"]
        self.assertEqual(updated["design_revision"], 2)
        self.assertEqual(updated["basis"], "observed_baseline")
        self.assertNotEqual(updated["status"], "needs_initialization")

    def test_observed_baseline_is_not_relabelled_as_approved_intent(self):
        self.input_id = self.plan()
        self.register(basis="observed_baseline")
        document = design.snapshot(self.conn, self.root)["designs"]["architecture"]
        self.assertEqual(document["basis"], "observed_baseline")
        self.assertNotEqual(document["status"], "approved_intent")

    def test_required_document_without_review_holds_only_affected_task(self):
        self.managed(("alpha", "beta"))
        self.disposition()
        self.disposition(work="beta", disposition="not_applicable")
        self.assertTrue(self.design_holds("task-alpha"))
        self.assertFalse(self.design_holds("task-beta"))
        self.review()
        self.assertFalse(self.design_holds("task-alpha"))

    def test_each_work_revision_needs_current_disposition(self):
        self.input_id = self.plan()
        self.assign()
        self.assertTrue(self.design_holds())
        self.disposition(disposition="not_applicable")
        self.assertFalse(self.design_holds())
        iid = self.incoming("Revise alpha.")
        self.assessment(iid, "Revise alpha.", [self.change("change", "alpha", 1,
                        title="alpha", request="Revised alpha", depends_on=[])])
        self.receipt("assignment-task-alpha", "not_started")
        self.assign("alpha", 2, task="replacement")
        self.assertTrue(self.design_holds("replacement"))
        self.disposition(revision=2, disposition="not_applicable")
        self.assertFalse(self.design_holds("replacement"))

    def test_not_applicable_requires_a_reason(self):
        self.input_id = self.plan()
        self.assign()
        self.dmutation("disposition", goal_id="goal", work_id="alpha", revision=1,
                       disposition="not_applicable", reason="", author=AUTHOR, ok=False)
        self.assertTrue(self.design_holds())

    def test_author_and_assigned_worker_cannot_supply_independent_approval(self):
        self.managed()
        self.disposition()
        self.review(reviewer=AUTHOR, ok=False)
        self.review(reviewer={"host": "codex", "session": "native-session", "agent": "worker-task-alpha"}, ok=False)
        self.assertTrue(self.design_holds())
        self.review()
        self.assertFalse(self.design_holds())

    def test_review_binds_actual_document_bytes_and_declared_revision(self):
        self.managed()
        self.disposition()
        self.review(sha="0" * 64, ok=False)
        self.review(design_revision=9, ok=False)
        self.assertTrue(self.design_holds())
        self.review()
        self.assertFalse(self.design_holds())
        (self.tasks["task-alpha"] / "ARCHITECTURE.md").write_text("Changed meaning after approval.\n")
        self.assertTrue(self.design_holds())

    def test_review_binds_real_source_in_assigned_worktree(self):
        self.managed()
        self.disposition()
        self.review()
        self.assertFalse(self.design_holds())
        (self.tasks["task-alpha"] / "app.py").write_text("def calculate(value):\n    return value + 2\n")
        self.assertTrue(self.design_holds())

    def test_needs_changes_or_not_evaluable_does_not_count_as_approval(self):
        self.managed()
        self.disposition()
        for result in ("needs_changes", "not_evaluable"):
            with self.subTest(result=result):
                self.review(result=result)
                self.assertTrue(self.design_holds())
        self.review()
        self.assertFalse(self.design_holds())

    def test_unregistered_reference_or_unrelated_intent_cannot_approve(self):
        self.managed()
        self.disposition()
        path = self.tasks["task-alpha"] / "ARCHITECTURE.md"
        for coverage in ([{"reference": "invented reference", "intent_ref": {"input_id": self.input_id, "quote": "Build alpha."}, "evidence": ["Read"]}],
                         [{"reference": "calculator contract", "intent_ref": {"input_id": self.input_id, "quote": "Unrequested promise"}, "evidence": ["Read"]}]):
            with self.subTest(coverage=coverage):
                self.dmutation("review", root=self.tasks["task-alpha"], goal_id="goal", work_id="alpha", revision=1,
                    design_id="architecture", design_revision=1,
                    content_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), reviewer=REVIEWER,
                    result="approved", coverage=coverage, reason="Read the actual sources.", evidence=["Fixture review"], ok=False)
        self.assertTrue(self.design_holds())

    def test_paths_cannot_escape_repository_through_parent_or_symlink(self):
        self.input_id = self.plan()
        outside = self.home / "outside.md"
        outside.write_text("Outside repository.\n")
        (self.root / "escape.md").symlink_to(outside)
        self.register(path="../outside.md", ok=False)
        self.register(path="escape.md", ok=False)

    def test_unmanaged_task_has_no_invented_design_obligation(self):
        self.task("historical")
        self.assertFalse(self.design_holds("historical"))

    def test_parent_identity_normalizes_empty_agent_and_distinct_child_can_review(self):
        source = {"host": "codex", "session": "actual-parent-session"}
        self.mutation("input", id="parent-intent", text="Build alpha.", source=source)
        self.input_id = "parent-intent"
        self.assessment(self.input_id, "Build alpha.", [self.change("add", "alpha", 0,
                        title="alpha", request="Implement alpha", depends_on=[])])
        self.register(author=source)
        self.assign()
        self.disposition()
        self.review(reviewer={**source, "agent": ""}, ok=False)
        self.assertTrue(self.design_holds())
        self.review(reviewer={**source, "agent": "actual-child-agent"})
        self.assertFalse(self.design_holds())

    def test_status_cannot_create_missing_ledger_or_modify_existing_schema(self):
        isolated = self.home / "status-only"
        isolated.mkdir()
        self.git(isolated, "init", "-q")
        (isolated / ".v4").mkdir()
        (isolated / ".v4/config.json").write_text(json.dumps({"test_command": "python3 -m unittest discover"}))
        before = {str(p.relative_to(isolated)): p.read_bytes() for p in isolated.rglob("*") if p.is_file()}
        result = self.cli("design", "status", root=isolated)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertFalse(ledger.ledger_path(isolated).exists())
        after = {str(p.relative_to(isolated)): p.read_bytes() for p in isolated.rglob("*") if p.is_file()}
        self.assertEqual(after, before)
        schema_before = list(self.conn.execute("SELECT name,sql FROM sqlite_schema ORDER BY name"))
        result = self.cli("design", "status")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(list(self.conn.execute("SELECT name,sql FROM sqlite_schema ORDER BY name")), schema_before)

    def test_new_design_revision_expires_previous_approval_even_for_same_bytes(self):
        self.managed()
        self.disposition()
        self.review()
        self.assertFalse(self.design_holds())
        self.register(revision=1)
        self.assertTrue(self.design_holds())
        self.review(design_revision=2)
        self.assertFalse(self.design_holds())

    def test_redaction_cannot_change_document_coordinates_or_author_attribution(self):
        self.input_id = self.plan()
        for field, marker in (("author", "synthetic-author-session"), ("path", "ARCHITECTURE[.]md"),
                              ("reference", "calculator contract")):
            with self.subTest(field=field):
                (self.root / ".v4/secret_patterns.json").write_text(json.dumps({"patterns": [{
                    "id": "fixture-design-coordinate", "label": "Synthetic design coordinate fixture",
                    "kind": "opaque", "regex": marker, "order": 0}]}))
                author = {"host": "codex", "session": marker} if field == "author" else AUTHOR
                self.register(author=author, ok=False)
                self.assertEqual(design.snapshot(self.conn, self.root)["designs"], {})

    def test_redaction_cannot_change_independent_reviewer_identity(self):
        self.managed()
        self.disposition()
        (self.root / ".v4/secret_patterns.json").write_text(json.dumps({"patterns": [{
            "id": "fixture-reviewer", "label": "Synthetic reviewer identity fixture", "kind": "opaque",
            "regex": "review-session", "order": 0}]}))
        # The semantic receipt runs in the assigned worktree and reads its own table.
        (self.tasks["task-alpha"] / ".v4/secret_patterns.json").write_bytes(
            (self.root / ".v4/secret_patterns.json").read_bytes())
        self.review(ok=False)
        self.assertEqual(design.snapshot(self.conn, self.root)["reviews"], [])
        self.assertTrue(self.design_holds())

    def test_future_worker_must_be_observed_before_independent_review(self):
        self.input_id = self.plan()
        self.register()
        path = self.task("future-worker")
        self.mutation("assign", id="future-worker-assignment", work_id="alpha", revision=1,
                      task_id="future-worker", worktree=str(path), host="codex", session="actual-parent-session",
                      dependency_versions=[])
        self.disposition()
        self.assertTrue(self.design_holds("future-worker"))
        self.review(task="future-worker", ok=False)
        self.prelaunch("future-worker-assignment")
        actual = {"host": "codex", "session": "actual-parent-session", "agent": "actual-native-worker"}
        self.mutation("receipt", assignment_id="future-worker-assignment", outcome="dispatched", worker=actual,
                      host_ref="native-child-reference", evidence=["The host returned this actual child identity"])
        self.review(task="future-worker", reviewer=actual, ok=False)
        self.review(task="future-worker")
        self.assertFalse(self.design_holds("future-worker"))
        self.mutation("receipt", assignment_id="future-worker-assignment", outcome="dispatched",
                      worker={**actual, "agent": "another-native-worker"}, host_ref="native-child-reference",
                      evidence=["Later host observation conflicts with the accepted child identity"])
        self.assertTrue(self.design_holds("future-worker"))


if __name__ == "__main__":
    unittest.main()
