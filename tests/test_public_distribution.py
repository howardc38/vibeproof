"""Real Git repositories prove export boundaries, drift refusal and history isolation."""
import hashlib
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
            self.git(path, "config", "core.filemode", "true")
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

    def test_committed_public_export_satisfies_its_spec(self):
        # Release input is committed HEAD, even when this test is run before
        # committing a documentation edit. Use the full real policy/exporter:
        # a miniature fixture cannot expose a private-only path in our SPEC.
        manifest, files = plan(ROOT, "HEAD", self.parent)
        out = self.root / "full-public"
        write_export(out, manifest, files)
        self.git(out, "init", "-q", "-b", "main")
        self.git(out, "add", "--all")
        self.assertFalse((out / ".github/workflows/private-history.yml").exists())
        self.assertFalse((out / ".v4/risks").exists())

        def problems():
            # Import the exported checker in a new interpreter. Neither the
            # canonical working tree nor its private Git history can answer.
            result = subprocess.run([
                sys.executable, "-c",
                "import json; from pathlib import Path; "
                "from kernel.spec_coverage import check; root = Path.cwd(); "
                "print(json.dumps(check(root, "
                "(root / 'docs/SPEC.md').read_text(encoding='utf-8'))))"],
                cwd=out, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        self.assertEqual(problems(), [])
        # The negative control changes only this disposable exported tree.
        # A checker that silently ignores shipped paths must still fail here.
        (out / "hooks/stop_gate.py").unlink()
        self.assertIn("the spec names hooks/stop_gate.py, which is not there",
                      problems())

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
        for origin in (".v4/risks/internal.json", ".v4/risks/repo/lint.json",
                       ".v4/ledger_export.jsonl.0001", ".v4/ledger_export.jsonl.projection.json"):
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


    def _public_hotfix(self, *, import_source=True):
        previous = self.git(self.source, "rev-parse", "HEAD")
        publish(self.source, self.target, "HEAD", self.parent, apply=True)
        contribution = "public contribution imported without changing its bytes\n"
        self.put(self.target, "README.md", contribution)
        manifest = json.loads((self.target / MANIFEST).read_text())
        manifest["files"]["README.md"]["sha256"] = hashlib.sha256(contribution.encode()).hexdigest()
        self.put(self.target, MANIFEST, json.dumps(manifest))
        self.commit(self.target)
        public_head = self.git(self.target, "rev-parse", "HEAD")
        if import_source:
            self.put(self.source, "README.md", contribution)
            self.commit(self.source)
        imported = self.git(self.source, "rev-parse", "HEAD")
        return previous, public_head, imported

    def _target_state(self):
        return {
            "head": self.git(self.target, "rev-parse", "HEAD"),
            "refs": self.git(self.target, "show-ref"),
            "branch": self.git(self.target, "symbolic-ref", "HEAD"),
            "index": self.git(self.target, "ls-files", "--stage"),
            "status": self.git(self.target, "status", "--porcelain", "--untracked-files=all"),
            "files": {path: ((self.target / path).read_bytes(),
                             (self.target / path).stat().st_mode)
                      for path in tree(self.target, "HEAD")},
        }

    def _refuse_import(self, expected, public_head, imported, *, revision="HEAD"):
        before = self._target_state()
        with self.assertRaisesRegex(ExportError, expected):
            publish(self.source, self.target, revision, public_head, apply=True,
                    imported_public_revision=imported)
        self.assertEqual(self._target_state(), before)

    def test_import_reconciliation_repairs_only_manifest_and_allows_next_publish(self):
        _, public_head, imported = self._public_hotfix()
        before = self._target_state()
        dry = publish(self.source, self.target, "HEAD", public_head,
                      imported_public_revision=imported)
        self.assertEqual(dry["imported_public_revision"], imported)
        self.assertFalse(dry["applied"])
        self.assertEqual(dry["added"] + dry["changed"] + dry["removed"], [])
        self.assertEqual(self._target_state(), before)
        # Exercise the real CLI option, not just the Python entry point.
        applied = subprocess.run([
            sys.executable, str(ROOT / "tools/publish_public.py"),
            "--source", str(self.source), "--target", str(self.target),
            "--expected-public-head", public_head,
            "--imported-public-revision", imported, "--apply"],
            capture_output=True, text=True)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        result = json.loads(applied.stdout)
        self.assertTrue(result["applied"])
        self.assertEqual(self.git(self.target, "diff", "--name-only", public_head, "HEAD"), MANIFEST)
        self.assertEqual(self.git(self.target, "rev-parse", "HEAD^"), public_head)
        manifest = verify(self.target, source=self.source)
        self.assertEqual(manifest["source_revision"], imported)
        self.assertEqual(manifest["public_parent"], public_head)
        repaired = result["public_commit"]
        unchanged = publish(self.source, self.target, "HEAD", repaired, apply=True)
        self.assertTrue(unchanged["unchanged"])
        self.put(self.source, "README.md", "next reviewed canonical release\n")
        self.commit(self.source)
        release = self.git(self.source, "rev-parse", "HEAD")
        next_release = publish(self.source, self.target, "HEAD", repaired, apply=True)
        self.assertTrue(next_release["applied"])
        self.assertEqual(self.git(self.target, "rev-parse", "HEAD^"), repaired)
        self.assertEqual(verify(self.target, source=self.source)["source_revision"], release)
        self.assertEqual((self.target / "README.md").read_text(), "next reviewed canonical release\n")
        self.assertNotIn(".v4/risks/internal.json", tree(self.target, "HEAD"))
        for private_commit in (imported, release):
            with self.subTest(private_commit=private_commit):
                found = subprocess.run(["git", "-C", str(self.target), "cat-file", "-e", private_commit],
                                       capture_output=True)
                self.assertNotEqual(found.returncode, 0)

    def test_import_anchor_can_precede_the_requested_release(self):
        _, public_head, imported = self._public_hotfix()
        self.put(self.source, "README.md", "canonical release after import\n")
        self.commit(self.source)
        released = self.git(self.source, "rev-parse", "HEAD")
        result = publish(self.source, self.target, released, public_head, apply=True,
                         imported_public_revision=imported)
        self.assertTrue(result["applied"])
        self.assertEqual(result["changed"], ["README.md"])
        self.assertEqual(verify(self.target, source=self.source)["source_revision"], released)
        self.assertEqual(self.git(self.target, "rev-parse", "HEAD^"), public_head)

    def test_import_option_refuses_an_unimported_public_edit(self):
        _, public_head, imported = self._public_hotfix(import_source=False)
        self._refuse_import("content/mode differs", public_head, imported)

    def test_import_option_refuses_a_missing_public_file(self):
        _, _, imported = self._public_hotfix()
        (self.target / "bin/program").unlink()
        self.commit(self.target)
        self._refuse_import("missing=.*bin/program", self.git(self.target, "rev-parse", "HEAD"), imported)

    def test_import_option_refuses_an_extra_public_file(self):
        _, _, imported = self._public_hotfix()
        self.put(self.target, "unimported.txt", "another contribution\n")
        self.commit(self.target)
        self._refuse_import("extra=.*unimported.txt", self.git(self.target, "rev-parse", "HEAD"), imported)

    def test_import_option_refuses_public_mode_drift(self):
        _, _, imported = self._public_hotfix()
        (self.target / "bin/program").chmod(0o644)
        self.commit(self.target)
        self._refuse_import("content/mode differs.*bin/program", self.git(self.target, "rev-parse", "HEAD"), imported)

    def test_import_option_still_requires_the_expected_public_head(self):
        _, _, imported = self._public_hotfix()
        self._refuse_import("HEAD moved", self.parent, imported)

    def test_import_cannot_reconcile_a_divergent_canonical_history(self):
        _, public_head, _ = self._public_hotfix()
        # All export bytes still match, but this root has no prior source ancestry.
        self.git(self.source, "checkout", "--orphan", "unrelated")
        self.commit(self.source)
        imported = self.git(self.source, "rev-parse", "HEAD")
        self._refuse_import("diverges from the published source", public_head, imported)

    def test_import_must_be_in_the_requested_release_history(self):
        previous, public_head, imported = self._public_hotfix()
        self.git(self.source, "switch", "-c", "sibling", previous)
        self.put(self.source, "README.md", "a sibling change without the import\n")
        self.commit(self.source)
        self._refuse_import("ancestor of the requested release", public_head, imported)

    def test_import_option_requires_a_previous_manifest(self):
        imported = self.git(self.source, "rev-parse", "HEAD")
        self._refuse_import("requires a prior public manifest", self.parent, imported)

    def test_import_option_refuses_an_invalid_prior_manifest(self):
        _, _, imported = self._public_hotfix()
        manifest = json.loads((self.target / MANIFEST).read_text())
        manifest["schema"] = 99
        self.put(self.target, MANIFEST, json.dumps(manifest))
        self.commit(self.target)
        self._refuse_import("invalid prior public manifest", self.git(self.target, "rev-parse", "HEAD"), imported)


    def _replace_manifest_with_external_symlink(self):
        external = self.root / "outside-manifest.json"
        external.write_bytes((self.target / MANIFEST).read_bytes())
        (self.target / MANIFEST).unlink()
        (self.target / MANIFEST).symlink_to(external)
        self.commit(self.target)
        self.assertEqual(tree(self.target, "HEAD")[MANIFEST]["mode"], "120000")
        return external

    def test_import_rejects_manifest_symlink_without_writing_outside_target(self):
        _, _, imported = self._public_hotfix()
        external = self._replace_manifest_with_external_symlink()
        external_before = external.read_bytes()
        self._refuse_import("manifest must be a tracked regular file",
                            self.git(self.target, "rev-parse", "HEAD"), imported)
        self.assertEqual(external.read_bytes(), external_before)
        self.assertTrue((self.target / MANIFEST).is_symlink())

    def test_normal_publish_rejects_manifest_symlink_before_any_write(self):
        publish(self.source, self.target, "HEAD", self.parent, apply=True)
        external = self._replace_manifest_with_external_symlink()
        # A complete canonical manifest with the actual parent used to satisfy
        # normal baseline verification even though its bytes live outside target.
        manifest = json.loads(external.read_text())
        manifest["public_parent"] = self.git(self.target, "rev-parse", "HEAD^")
        external.write_text(json.dumps(manifest))
        verify(self.target, source=self.source)
        self.put(self.source, "README.md", "new release that would copy through the symlink\n")
        self.commit(self.source)
        public_head = self.git(self.target, "rev-parse", "HEAD")
        before = self._target_state()
        external_before = external.read_bytes()
        with self.assertRaisesRegex(ExportError, "manifest must be a tracked regular file"):
            publish(self.source, self.target, "HEAD", public_head, apply=True)
        self.assertEqual(self._target_state(), before)
        self.assertEqual(external.read_bytes(), external_before)
        self.assertTrue((self.target / MANIFEST).is_symlink())


    def test_import_rejects_skip_worktree_symlink_without_outside_write(self):
        _, public_head, imported = self._public_hotfix()
        external = self.root / "outside-readme.txt"
        external.write_bytes((self.target / "README.md").read_bytes())
        self.git(self.target, "update-index", "--skip-worktree", "README.md")
        (self.target / "README.md").unlink()
        (self.target / "README.md").symlink_to(external)
        self.assertEqual(self.git(self.target, "status", "--porcelain"), "")
        self.put(self.source, "README.md", "later canonical release\n")
        self.commit(self.source)
        outside_before = external.read_bytes()
        self._refuse_import("public checkout", public_head, imported)
        self.assertEqual(external.read_bytes(), outside_before)
        self.assertTrue((self.target / "README.md").is_symlink())

    def test_import_rejects_skip_worktree_content_drift(self):
        _, public_head, imported = self._public_hotfix()
        self.git(self.target, "update-index", "--skip-worktree", "README.md")
        self.put(self.target, "README.md", "uncommitted edit hidden from status\n")
        self.assertEqual(self.git(self.target, "status", "--porcelain"), "")
        self._refuse_import("public checkout", public_head, imported)

    def test_import_rejects_skip_worktree_mode_drift(self):
        _, public_head, imported = self._public_hotfix()
        self.git(self.target, "update-index", "--skip-worktree", "bin/program")
        (self.target / "bin/program").chmod(0o644)
        self.assertEqual(self.git(self.target, "status", "--porcelain"), "")
        self._refuse_import("public checkout", public_head, imported)

    def test_new_release_path_cannot_follow_an_ignored_directory_symlink(self):
        _, public_head, imported = self._public_hotfix()
        outside = self.root / "outside-directory"
        outside.mkdir()
        self.put(self.target, ".git/info/exclude", "newdir\n")
        (self.target / "newdir").symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.git(self.target, "status", "--porcelain"), "")
        self.policy["include"].append("newdir/*")
        self.save_policy()
        self.put(self.source, "newdir/file.txt", "must not write through the ignored symlink\n")
        self.commit(self.source)
        self._refuse_import("unsafe public destination", public_head, imported)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertTrue((self.target / "newdir").is_symlink())


if __name__ == "__main__":
    unittest.main()
