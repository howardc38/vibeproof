"""Real Git repositories prove export boundaries, drift refusal and history isolation."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from export_public import ExportError, MANIFEST, git, plan, tree, verify, write_export
from publish_public import publish


class PublicDistribution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "private"
        self.target = self.root / "public"
        for path in (self.source, self.target):
            path.mkdir()
            self.git(path, "init", "-q", "-b", "main")
            self.git(path, "config", "user.name", "distribution test")
            self.git(path, "config", "user.email", "test@example.invalid")
            self.git(path, "config", "commit.gpgsign", "false")
        self.put(self.target, "README.md", "old public\n")
        self.put(self.target, "retired/file.txt", "managed only in the bootstrap snapshot\n")
        self.commit(self.target)
        self.parent = self.git(self.target, "rev-parse", "HEAD")
        self.git(self.target, "remote", "add", "origin", "git@github.com:howardc38/vibeproof.git")
        self.put(self.source, "README.md", "new canonical docs\n")
        self.put(self.source, "bin/program", "#!/bin/sh\nexit 0\n")
        (self.source / "bin/program").chmod(0o755)
        self.put(self.source, ".v4/risks/internal.json", '{"private": "must not leave"}\n')
        self.put(self.source, "tools/export_public.py", (ROOT / "tools/export_public.py").read_text())
        self.policy = {"schema": 1, "include": ["README.md", "bin/*"],
                       "required": ["README.md"], "overrides": {}}
        self.save_policy()
        self.put(self.source, "publishing/bootstrap.json", json.dumps({
            "public_head": self.parent,
            "files": {p: e["blob"] for p, e in tree(self.target, "HEAD").items()}}))
        self.commit(self.source)

    def git(self, root, *args):
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
        if p.returncode:
            self.fail(p.stderr)
        return p.stdout.strip()

    def put(self, root, path, text):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def commit(self, root):
        self.git(root, "add", "--all")
        self.git(root, "commit", "-qm", "test snapshot")

    def save_policy(self):
        self.put(self.source, "publishing/public-files.json", json.dumps(self.policy))

    def test_committed_source_only_and_private_records_excluded(self):
        self.put(self.source, "README.md", "uncommitted edit is not a release\n")
        manifest, files = plan(self.source, "HEAD", self.parent)
        self.assertEqual(files["README.md"][1], b"new canonical docs\n")
        self.assertEqual(set(files), {"README.md", "bin/program"})
        self.assertNotIn(".v4/risks/internal.json", manifest["files"])

    def test_policy_cannot_accidentally_export_private_operational_paths(self):
        self.policy["include"].append(".v4/**")
        self.save_policy(); self.commit(self.source)
        with self.assertRaisesRegex(ExportError, "private operational"):
            plan(self.source, "HEAD", self.parent)

    def test_symlink_and_traversal_are_refused_before_output(self):
        (self.source / "leak").symlink_to(".v4/risks/internal.json")
        self.policy["include"].append("leak")
        self.save_policy(); self.commit(self.source)
        with self.assertRaisesRegex(ExportError, "regular files"):
            plan(self.source, "HEAD", self.parent)
        self.policy["include"].remove("leak")
        self.policy["overrides"] = {"../outside": "README.md"}
        self.save_policy(); self.commit(self.source)
        with self.assertRaisesRegex(ExportError, "unsafe path"):
            plan(self.source, "HEAD", self.parent)

    def test_missing_required_file_is_a_failure(self):
        self.policy["required"].append("missing.py")
        self.save_policy(); self.commit(self.source)
        with self.assertRaisesRegex(ExportError, "required public"):
            plan(self.source, "HEAD", self.parent)

    def test_git_failure_reports_a_stdout_only_diagnostic(self):
        self.put(self.target, "README.md", "new trailing whitespace   \n")
        with self.assertRaisesRegex(ExportError, "trailing whitespace"):
            git(self.target, "diff", "--check")

    def test_private_override_and_sealed_segments_cannot_be_renamed_into_public(self):
        for origin in (".v4/risks/internal.json", ".v4/ledger_export.jsonl.0001"):
            with self.subTest(origin=origin):
                self.put(self.source, origin, "private operational record\n")
                self.policy["overrides"] = {"docs/innocent.txt": origin}
                self.save_policy(); self.commit(self.source)
                with self.assertRaisesRegex(ExportError, "private override"):
                    plan(self.source, "HEAD", self.parent)

    def test_first_identical_export_still_records_provenance(self):
        self.put(self.source, "README.md", "old public\n")
        self.put(self.source, "retired/file.txt", "managed only in the bootstrap snapshot\n")
        self.policy["include"] = ["README.md", "retired/*"]
        self.save_policy(); self.commit(self.source)
        result = publish(self.source, self.target, "HEAD", self.parent, apply=True)
        self.assertTrue(result["applied"])
        self.assertTrue((self.target / MANIFEST).is_file())
        self.assertEqual(verify(self.target, source=self.source)["public_parent"], self.parent)

    def test_late_ci_for_an_older_source_cannot_roll_public_back(self):
        old_source = self.git(self.source, "rev-parse", "HEAD")
        self.put(self.source, "README.md", "newer source already published\n")
        self.commit(self.source)
        publish(self.source, self.target, "HEAD", self.parent, apply=True)
        current = self.git(self.target, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ExportError, "older than"):
            publish(self.source, self.target, old_source, current, apply=True)
        self.assertEqual(self.git(self.target, "rev-parse", "HEAD"), current)
        self.assertEqual((self.target / "README.md").read_text(), "newer source already published\n")

    def test_verify_catches_changed_missing_and_extra_files(self):
        manifest, files = plan(self.source, "HEAD", self.parent)
        out = self.root / "export"
        write_export(out, manifest, files)
        verify(out, source=self.source)
        (out / "README.md").write_text("drift\n")
        (out / "bin/program").unlink()
        (out / "extra").write_text("not managed\n")
        with self.assertRaises(ExportError) as ctx:
            verify(out)
        self.assertIn("content drift", str(ctx.exception))
        self.assertIn("extra", str(ctx.exception))
        self.assertIn("bin/program", str(ctx.exception))

    def test_publish_preserves_public_ancestry_and_removes_retired_files(self):
        source_head = self.git(self.source, "rev-parse", "HEAD")
        dry = publish(self.source, self.target, "HEAD", self.parent)
        self.assertFalse(dry["applied"])
        self.assertEqual((self.target / "README.md").read_text(), "old public\n")
        done = publish(self.source, self.target, "HEAD", self.parent, apply=True)
        self.assertTrue(done["applied"])
        self.assertEqual(self.git(self.target, "rev-parse", "HEAD^"), self.parent)
        self.assertFalse((self.target / "retired").exists())
        self.assertFalse((self.target / ".v4/risks/internal.json").exists())
        self.assertTrue((self.target / "bin/program").stat().st_mode & 0o111)
        self.assertEqual(verify(self.target, source=self.source)["source_revision"], source_head)
        private_object = subprocess.run(["git", "-C", str(self.target), "cat-file", "-e", source_head],
                                        capture_output=True)
        self.assertNotEqual(private_object.returncode, 0)

    def test_dirty_source_or_target_and_wrong_head_never_mutate_target(self):
        original = self.git(self.target, "rev-parse", "HEAD")
        self.put(self.source, "draft.txt", "uncommitted\n")
        with self.assertRaisesRegex(ExportError, "canonical source"):
            publish(self.source, self.target, "HEAD", self.parent, apply=True)
        (self.source / "draft.txt").unlink()
        self.put(self.target, "README.md", "user edit\n")
        with self.assertRaisesRegex(ExportError, "local changes"):
            publish(self.source, self.target, "HEAD", self.parent, apply=True)
        self.assertEqual((self.target / "README.md").read_text(), "user edit\n")
        self.git(self.target, "restore", "README.md")
        with self.assertRaisesRegex(ExportError, "HEAD moved"):
            publish(self.source, self.target, "HEAD", "0" * 40, apply=True)
        self.assertEqual(self.git(self.target, "rev-parse", "HEAD"), original)

    def test_public_edit_with_rewritten_manifest_is_not_silently_overwritten(self):
        publish(self.source, self.target, "HEAD", self.parent, apply=True)
        self.put(self.target, "README.md", "a contribution outside the publisher\n")
        manifest = json.loads((self.target / MANIFEST).read_text())
        import hashlib
        manifest["files"]["README.md"]["sha256"] = hashlib.sha256((self.target / "README.md").read_bytes()).hexdigest()
        self.put(self.target, MANIFEST, json.dumps(manifest)); self.commit(self.target)
        current = self.git(self.target, "rev-parse", "HEAD")
        with self.assertRaisesRegex(ExportError, "canonical source export"):
            publish(self.source, self.target, "HEAD", current, apply=True)
        self.assertEqual(self.git(self.target, "rev-parse", "HEAD"), current)


if __name__ == "__main__":
    unittest.main()
