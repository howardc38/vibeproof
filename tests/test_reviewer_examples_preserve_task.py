"""Execute maintained reviewer examples and read their actual ledger ownership."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest

from kernel import config, install, ledger, lifecycle

ROOT = Path(__file__).resolve().parent.parent


class ReviewerExampleOwnership(unittest.TestCase):
    def example(self, host, action, sweep):
        if host == "claude":
            body = (ROOT / ".claude/agents/reviewer.md").read_text()
        else:
            body = tomllib.loads((ROOT / ".codex/agents/v4-reviewer.toml").read_text())["developer_instructions"]
        body = body.replace("\\\n", " ")
        examples = re.findall(r"^\./bin/v4[^\n]*\breview " + action + r"\b[^\n]*", body, re.M)
        self.assertTrue(examples, f"{host} has no executable {action} example")
        # The first example is assigned work; the second is the explicitly
        # taskless sweep. Old prompts had just one example for both modes.
        return examples[-1 if sweep else 0]

    def run_examples(self, host, sweep):
        with tempfile.TemporaryDirectory(prefix="reviewer adopter ") as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / ".v4/lenses").mkdir(parents=True)
            (root / "app.py").write_text("def value():\n    return 0\n")
            (root / ".v4/config.json").write_text(json.dumps({
                "test_command": "python3 -m unittest discover", "policy": "allow_accepted_risk"}))
            (root / ".v4/claim_kinds.json").write_text(json.dumps({
                "review-finding": {"checker": "review-finding", "question_template": "q", "staleness": "subject"}}))
            shutil.copy(ROOT / ".v4/lenses/test-sufficiency.json", root / ".v4/lenses/test-sufficiency.json")
            install.write_launcher(root, ROOT)
            subprocess.run(["git", "add", "-A"], cwd=root, check=True)
            subprocess.run(["git", "-c", "user.name=Reviewer test", "-c", "user.email=reviewer@example.invalid",
                            "-c", "commit.gpgsign=false", "commit", "-qm", "baseline"], cwd=root, check=True)
            conn = ledger.connect(root)
            lifecycle.open_task(conn, config.RepoConfig(root), task_id="t-target",
                                request="value in app.py must return one", scope_globs=["app.py"])
            conn.close()
            replacements = {"<repo-root>": str(root), "<task-id>": "t-target", "<名>": "test-sufficiency",
                            "<path>": "app.py", "<包住嗰個 symbol>": "value",
                            "<一句,錯咗乜>": "app.py value returns zero when the contract requires one",
                            "<你開咗幾多條>": "1"}
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", V4_TASK="ambient-wrong-task")
            for key in ("V4_HOME", "V4_REPO", "PYTHONPATH"):
                env.pop(key, None)
            for action in ("lens", "add", "done"):
                command = self.example(host, action, sweep)
                for key, value in replacements.items():
                    command = command.replace(key, value)
                result = subprocess.run(["bash", "-c", command], cwd=root, env=env,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            conn = ledger.connect_readonly(root)
            try:
                events = list(conn.execute("SELECT kind,task_id FROM event WHERE kind IN ('lens_run','lens_reviewed')"))
                findings = list(conn.execute("SELECT task_id FROM claim WHERE kind='review-finding'"))
                self.assertEqual(len(events), 2)
                self.assertEqual(len(findings), 1)
                self.assertEqual([(r["kind"], r["task_id"]) for r in events],
                                 [("lens_run", None if sweep else "t-target"),
                                  ("lens_reviewed", None if sweep else "t-target")])
                self.assertEqual(findings[0]["task_id"], "repo-review" if sweep else "t-target")
            finally:
                conn.close()

    def test_claude_assigned_review_stays_with_its_task(self):
        self.run_examples("claude", False)

    def test_codex_assigned_review_stays_with_its_task(self):
        self.run_examples("codex", False)

    def test_claude_sweep_does_not_borrow_an_ambient_task(self):
        self.run_examples("claude", True)

    def test_codex_sweep_does_not_borrow_an_ambient_task(self):
        self.run_examples("codex", True)
