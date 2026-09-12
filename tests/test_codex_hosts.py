"""Behavioral parity probes against real disposable Git repositories."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from kernel import config, doctrine, doctor, host_binding, hosts, install, ledger, lifecycle

ROOT = Path(__file__).resolve().parent.parent


class HostIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "project"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Host test")
        self.git("config", "user.email", "host-test@example.invalid")
        (self.root / "app.py").write_text("value = 1\n")
        (self.root / "other.py").write_text("value = 2\n")
        (self.root / ".v4").mkdir()
        (self.root / ".v4/config.json").write_text(json.dumps({
            "test_command": "python3 -m unittest discover", "policy": "allow_accepted_risk"}))
        (self.root / ".v4/claim_kinds.json").write_text("{}")
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "base")
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        for k in ("V4_TASK", "V4_REPO", "V4_HOME", "PYTHONPATH"):
            self.env.pop(k, None)

    def git(self, *args, root=None):
        return subprocess.run(["git", *args], cwd=root or self.root,
                              text=True, capture_output=True, check=True).stdout.strip()

    def task(self, tid, root=None, scope=None):
        root = root or self.root
        conn = ledger.connect(root)
        try:
            lifecycle.open_task(conn, config.RepoConfig(root), task_id=tid,
                                request="Update the bounded application behavior",
                                scope_globs=scope or ["app.py"])
        finally:
            conn.close()

    def hook(self, name, payload, root=None, host="codex"):
        root = root or self.root
        data = {"cwd": str(root), "session_id": "parent", **payload}
        env = dict(self.env, V4_HOST=host)
        # Host root is supplied by payload for Codex; Claude legacy has its explicit override.
        if host == "claude":
            env["V4_REPO"] = str(root)
        r = subprocess.run([sys.executable, "-B", str(ROOT / "hooks" / (name + ".py"))],
                           cwd=root, env=env, input=json.dumps(data), capture_output=True,
                           text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout), r.stderr

    def patch(self, *names):
        body = "\n".join("*** Update File: " + str(p) + "\n@@\n-value = 1\n+value = 3"
                         for p in names)
        return {"tool_name": "apply_patch", "hook_event_name": "PreToolUse",
                "tool_input": {"command": "*** Begin Patch\n" + body + "\n*** End Patch"}}

    def denied(self, answer):
        return answer.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_default_remains_claude_and_dual_doctrine_preserves_local_text(self):
        self.assertEqual(hosts.selected(self.root), ("claude",))
        doctrine.write(config.RepoConfig(self.root))
        self.assertFalse((self.root / "AGENTS.md").exists())
        (self.root / "AGENTS.md").write_text("Local contributor instructions\n")
        hosts.configure(self.root, "both")
        doctrine.write(config.RepoConfig(self.root))
        self.assertTrue((self.root / "AGENTS.md").read_text().startswith("Local contributor"))
        self.assertIsNone(doctrine.drift(config.RepoConfig(self.root)))
        for name in ("AGENTS.md", "CLAUDE.md"):
            p = self.root / name
            text = p.read_text()
            doctrine.write(config.RepoConfig(self.root))
            self.assertEqual(p.read_text(), text)
        p = self.root / "AGENTS.md"
        p.write_text(p.read_text().replace("永遠適用", "tampered"))
        self.assertIn("AGENTS.md", doctrine.drift(config.RepoConfig(self.root)))

    def test_hook_activation_preserves_other_handlers_and_is_idempotent(self):
        p = self.root / ".claude/settings.json"
        p.parent.mkdir()
        p.write_text(json.dumps({"permissions": {"allow": ["Read"]}, "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "echo independent"}]}]}}))
        hosts.activate_hooks(self.root, "claude")
        first = p.read_text()
        hosts.activate_hooks(self.root, "claude")
        self.assertEqual(p.read_text(), first)
        data = json.loads(first)
        self.assertEqual(data["permissions"], {"allow": ["Read"]})
        self.assertIn("echo independent", first)
        cp = hosts.activate_hooks(self.root, "codex")
        self.assertIn("SubagentStop", json.loads(cp.read_text())["hooks"])

    def test_codex_assets_are_generated_and_roles_have_real_instructions(self):
        for rel, wanted in hosts.codex_assets(ROOT).items():
            self.assertEqual((ROOT / rel).read_text(), wanted, rel)
            if rel.endswith(".toml") and "/agents/" in rel:
                role = tomllib.loads(wanted)
                self.assertGreater(len(role["developer_instructions"]), 100)
                self.assertTrue(role["name"].startswith("v4-"))

    def test_patch_inside_scope_passes_and_outside_scope_is_refused(self):
        self.task("one")
        answer, _ = self.hook("write_block", self.patch("app.py"))
        self.assertFalse(self.denied(answer))
        answer, _ = self.hook("write_block", self.patch("app.py", "other.py"))
        self.assertTrue(self.denied(answer))
        self.assertIn("other.py", str(answer))
        self.assertEqual((self.root / "app.py").read_text(), "value = 1\n")

    def test_patch_rename_checks_destination_and_deletion_checks_target(self):
        self.task("one")
        p = self.patch("app.py")
        p["tool_input"]["command"] = p["tool_input"]["command"].replace(
            "*** Update File: app.py", "*** Update File: app.py\n*** Move to: other.py")
        self.assertTrue(self.denied(self.hook("write_block", p)[0]))
        p["tool_input"]["command"] = "*** Begin Patch\n*** Delete File: other.py\n*** End Patch"
        self.assertTrue(self.denied(self.hook("write_block", p)[0]))

    def test_protected_patch_without_task_and_malformed_patch_are_refused(self):
        self.assertTrue(self.denied(self.hook("write_block", self.patch(".v4/config.json"))[0]))
        p = self.patch("app.py")
        p["tool_input"]["command"] = "*** Begin Patch\n"
        self.assertTrue(self.denied(self.hook("write_block", p)[0]))

    def test_claude_write_and_notebook_inputs_still_use_the_existing_scope(self):
        self.task("one")
        for tool, key in [("Write", "file_path"), ("Edit", "file_path"),
                          ("MultiEdit", "file_path"), ("NotebookEdit", "notebook_path")]:
            p = {"tool_name": tool, "tool_input": {key: str(self.root / "other.py")}}
            self.assertTrue(self.denied(self.hook("write_block", p, host="claude")[0]))

    def test_shell_guard_reports_codex_host_and_cannot_claim_patch_coverage(self):
        self.task("one")
        p = {"tool_name": "Bash", "hook_event_name": "PreToolUse",
             "tool_input": {"command": "echo changed > .v4/config.json"}}
        self.assertTrue(self.denied(self.hook("bash_guard", p)[0]))
        conn = ledger.connect_readonly(self.root)
        try:
            got = hosts.evidence(conn, task_id="one", host="codex")
            self.assertEqual(got["observed"], ["bash_guard"])
            self.assertNotIn("write_block", got["checked"])
        finally:
            conn.close()

    def test_worktree_patch_and_subagent_stop_resolve_their_own_tasks(self):
        other = Path(self.tmp.name) / "other-worktree"
        self.git("worktree", "add", "--detach", "-q", str(other))
        self.task("main")
        self.task("child", root=other)
        answer, _ = self.hook("write_block", self.patch(other / "app.py"))
        self.assertFalse(self.denied(answer), answer)
        conn = ledger.connect(other)
        try:
            host_binding.bind(conn, other, task_id="child", session="parent", agent="child-agent")
            self.assertEqual(host_binding.lookup(conn, session="parent", agent="child-agent")["task_id"],
                             "child")
        finally:
            conn.close()
        answer, _ = self.hook("stop_gate", {"hook_event_name": "SubagentStop",
                             "agent_id": "child-agent", "stop_hook_active": False})
        self.assertEqual(answer.get("decision"), "block", answer)
        self.assertIn("child", answer["reason"])
        self.assertNotIn("2 tasks", answer["reason"])
        answer, _ = self.hook("stop_gate", {"hook_event_name": "SubagentStop",
                             "agent_id": "child-agent", "stop_hook_active": True})
        self.assertEqual(answer, {})

    def test_two_tasks_in_one_worktree_are_never_guessed(self):
        self.task("one")
        self.task("two")
        answer, _ = self.hook("write_block", self.patch("app.py"))
        self.assertTrue(self.denied(answer))
        self.assertIn("tasks", str(answer))

    def _parent_binding_selects_only_its_task(self, host):
        self.task("one")
        self.task("two", scope=["other.py"])
        payload = self.patch("app.py") if host == "codex" else {
            "tool_name": "Write", "tool_input": {"file_path": str(self.root / "app.py")}}
        self.assertTrue(self.denied(self.hook("write_block", payload, host=host)[0]))
        result = subprocess.run([sys.executable, "-m", "kernel.cli", "--repo", str(self.root),
                                 "host", "bind", "--host", host, "--task", "one",
                                 "--session", "parent"], cwd=ROOT, env=self.env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Parent tool payloads have a session_id but no agent_id.
        answer, _ = self.hook("write_block", payload, host=host)
        self.assertFalse(self.denied(answer), answer)
        other = self.patch("other.py") if host == "codex" else {
            "tool_name": "Write", "tool_input": {"file_path": str(self.root / "other.py")}}
        self.assertTrue(self.denied(self.hook("write_block", other, host=host)[0]))
        self.assertTrue(self.denied(self.hook("write_block", {
            **payload, "session_id": "unrelated-session"}, host=host)[0]))
        c = ledger.connect_readonly(self.root)
        try:
            rows = [dict(r) for r in c.execute("SELECT task_id,payload FROM event WHERE kind='hook_seen'")]
            allowed = [r for r in rows if json.loads(r["payload"]).get("allowed")]
            self.assertEqual({r["task_id"] for r in allowed}, {"one"})
        finally:
            c.close()

    def test_codex_parent_binding_selects_only_its_task(self):
        self._parent_binding_selects_only_its_task("codex")

    def test_claude_parent_binding_selects_only_its_task(self):
        self._parent_binding_selects_only_its_task("claude")

    def _displayed_binding_remedies_use_the_actual_host(self, host):
        import shlex
        self.task("one")
        self.task("two", scope=["other.py"])
        for hook in ("write_block", "stop_gate"):
            session = host + "-" + hook
            payload = self.patch("app.py") if host == "codex" else {
                "tool_name": "Write", "tool_input": {"file_path": str(self.root / "app.py")}}
            if hook == "stop_gate":
                payload = {"hook_event_name": "Stop", "stop_hook_active": False}
            payload["session_id"] = session
            answer, _ = self.hook(hook, payload, host=host)
            reason = answer.get("reason") or answer["hookSpecificOutput"]["permissionDecisionReason"]
            lines = [line.strip() for line in reason.splitlines() if line.strip().startswith("./bin/v4")]
            self.assertEqual(len(lines), 1, reason)
            args = shlex.split(lines[0])
            args[0] = str(ROOT / "bin/v4")
            args[args.index("--task") + 1] = "one"
            result = subprocess.run(args, cwd=self.root, env=self.env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            binding = json.loads(result.stdout)
            self.assertEqual((binding["host"], binding["session"]), (host, session))
            answer, _ = self.hook(hook, payload, host=host)
            if hook == "write_block":
                self.assertFalse(self.denied(answer), answer)
            else:
                self.assertEqual(answer.get("decision"), "block", answer)
                self.assertIn("one", answer["reason"])
                self.assertNotIn("2 tasks", answer["reason"])

    def test_codex_displayed_binding_remedies_work(self):
        self._displayed_binding_remedies_use_the_actual_host("codex")

    def test_claude_displayed_binding_remedies_work(self):
        self._displayed_binding_remedies_use_the_actual_host("claude")

    def test_a_bound_parent_cannot_write_another_worktrees_task(self):
        child = Path(self.tmp.name) / "child"
        self.git("worktree", "add", "--detach", "-q", str(child))
        self.task("main")
        self.task("child", root=child)
        c = ledger.connect(self.root)
        try:
            host_binding.bind(c, self.root, task_id="main", session="parent")
        finally:
            c.close()
        answer, _ = self.hook("write_block", self.patch(child / "app.py"))
        self.assertTrue(self.denied(answer), answer)
        self.assertIn("different worktree", str(answer))

    def test_worktree_bootstrap_finds_shared_marker_without_local_marker(self):
        shutil.copytree(ROOT / "hooks", self.root / "hooks",
                        ignore=shutil.ignore_patterns("__pycache__"))
        install.write_launcher(self.root, ROOT)
        self.git("add", "-A")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "installed footprint")
        other = Path(self.tmp.name) / "new-worktree"
        self.git("worktree", "add", "--detach", "-q", str(other))
        self.assertFalse((other / ".v4/home").exists())
        r = subprocess.run([sys.executable, "-B", "-c",
                            "import sys;sys.path.insert(0,'hooks');import _framework;"
                            "from pathlib import Path;print(_framework.home(Path.cwd()))"],
                           cwd=other, env=self.env, capture_output=True, text=True, check=True)
        self.assertEqual(Path(r.stdout.strip()).resolve(), ROOT.resolve())

    def test_doctor_never_creates_a_missing_ledger(self):
        with self.assertRaises(Exception):
            doctor._ro(self.root)
        self.assertFalse((self.root / ".git/v4/ledger.db").exists())

    def test_session_context_exposes_agent_identity_without_opening_a_task(self):
        answer, _ = self.hook("session_context", {"hook_event_name": "SubagentStart",
                              "agent_id": "worker-1"})
        text = answer["hookSpecificOutput"]["additionalContext"]
        self.assertIn("worker-1", text)
        self.assertIn("host bind", text)
        self.assertFalse((self.root / ".git/v4/ledger.db").exists())



    def test_claude_linked_worktree_writes_are_checked_against_that_task(self):
        other = Path(self.tmp.name) / "claude-worktree"
        self.git("worktree", "add", "--detach", "-q", str(other))
        self.task("main")
        self.task("child", root=other)
        allowed = {"tool_name": "Write", "tool_input": {"file_path": str(other / "app.py")}}
        denied = {"tool_name": "Edit", "tool_input": {"file_path": str(other / "other.py")}}
        self.assertFalse(self.denied(self.hook("write_block", allowed, host="claude")[0]))
        self.assertTrue(self.denied(self.hook("write_block", denied, host="claude")[0]))
        c = ledger.connect_readonly(other)
        try:
            rows = list(c.execute("SELECT task_id,payload FROM event WHERE kind='hook_seen'"))
            self.assertTrue(rows)
            self.assertEqual({r["task_id"] for r in rows}, {"child"})
        finally:
            c.close()

    def test_codex_events_do_not_make_claude_hooks_look_healthy(self):
        shutil.copytree(ROOT / "hooks", self.root / "hooks", ignore=shutil.ignore_patterns("__pycache__"))
        hosts.activate_hooks(self.root, "claude")
        c = ledger.connect(self.root)
        for hook in hosts.HOOKS:
            ledger.insert(c, "event", task_id=None, claim_id=None, kind="hook_seen",
                          actor="hook", payload={"host": "codex", "hook": hook},
                          created_at="2026-09-07")
        c.close()
        rows = []
        try:
            doctor._check_hooks_are_called_and_not_merely_present(self.root, rows)
        finally:
            doctor._close_reader()
        self.assertEqual(rows[0]["status"], "warn", rows)
        self.assertIn("none has", rows[0]["detail"])

    def test_launcher_uses_the_same_relocated_home_as_hooks(self):
        shutil.copytree(ROOT / "hooks", self.root / "hooks", ignore=shutil.ignore_patterns("__pycache__"))
        install.write_launcher(self.root, ROOT)
        new = Path(self.tmp.name) / "relocated framework"
        (new / "kernel").mkdir(parents=True)
        (new / "kernel/__init__.py").write_text("")
        (new / "kernel/cli.py").write_text("print('RELOCATED_KERNEL_EXECUTED')\n")
        (self.root / ".v4/home").write_text(str(new) + "\n")
        r = subprocess.run([str(self.root / "bin/v4")], cwd=self.root, env=self.env,
                           capture_output=True, text=True, check=True)
        self.assertEqual(r.stdout.strip(), "RELOCATED_KERNEL_EXECUTED")

    def test_malformed_tool_input_cannot_crash_the_patch_guard_open(self):
        for value in ("patch text in the wrong field", [1, 2]):
            answer, _ = self.hook("write_block", {"tool_name": "apply_patch", "tool_input": value})
            self.assertTrue(self.denied(answer))



    def test_activation_preserves_another_projects_same_named_hook(self):
        p = self.root / ".claude/settings.json"
        p.parent.mkdir()
        other = "python3 /opt/another-project/hooks/stop_gate.py"
        p.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [
            {"type": "command", "command": other}]}]}}))
        hosts.activate_hooks(self.root, "claude")
        got = json.loads(p.read_text())
        commands = [h["command"] for group in got["hooks"]["Stop"] for h in group["hooks"]]
        self.assertIn(other, commands)
        self.assertEqual(len(commands), 2)

    def test_invalid_host_selection_fails_at_configuration_load(self):
        p = self.root / ".v4/config.json"
        original = json.loads(p.read_text())
        for value in (None, [], "both", ["not-a-host"]):
            p.write_text(json.dumps({**original, "agent_hosts": value}))
            with self.subTest(value=value), self.assertRaises(config.ConfigError):
                config.RepoConfig(self.root)


if __name__ == "__main__":
    unittest.main()
