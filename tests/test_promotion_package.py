"""The promotion builder must emit a demo that needs no source checkout."""

import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
release_path = ROOT / ".public-release.json"
if release_path.is_file():
    release = json.loads(release_path.read_text())
    if release.get("schema") != 1 or not isinstance(release.get("files"), dict):
        raise ValueError("Invalid public distribution manifest")
    private_tools = {"tools/package_promotion.py", "tools/verify_launch.py"}
    if not private_tools.intersection(release["files"]):
        raise unittest.SkipTest(
            "Private canonical promotion tooling is outside this distribution's "
            "declared product scope; test_public_first_proof covers its public demo")

from tools.package_promotion import copy_demo
from tools.verify_launch import recorded_source_problems


class PromotionPackage(unittest.TestCase):
    def run_packaged_demo(self, package):
        home = package / "home"
        home.mkdir()
        env = {
            "HOME": str(home),
            "TMPDIR": str(home),
            "PATH": str(Path(sys.executable).parent) + os.pathsep + os.defpath,
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_TERMINAL_PROMPT": "0",
        }
        return subprocess.run(
            [sys.executable, "-I", "07-demo/examples/first-proof/run.py",
             "--quiet", "--output", "observed"],
            cwd=package, env=env, text=True, capture_output=True, timeout=120,
        )

    def test_emitted_demo_proves_failure_repair_and_evidence_expiry(self):
        with tempfile.TemporaryDirectory(prefix="vibeproof-package-") as directory:
            package = Path(directory)
            copy_demo(package)
            result = self.run_packaged_demo(package)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            evidence = json.loads((package / "observed/demo.json").read_text())
            self.assertTrue(evidence["verified"])
            self.assertIsNone(evidence["framework_base_commit"])
            steps = {step["id"]: step for step in evidence["steps"]}
            self.assertEqual(steps["green_suite"]["exit_code"], 0)
            self.assertEqual(steps["wrong_result"]["output"], "120")
            self.assertEqual(steps["unexecuted"]["exit_code"], 1)
            self.assertEqual(steps["regression_red"]["exit_code"], 1)
            self.assertEqual(steps["repair_verified"]["exit_code"], 0)
            self.assertEqual(steps["correct_result"]["output"], "80")
            self.assertEqual(steps["limit_unrelated_closure"]["exit_code"], 1)
            current = evidence["evidence_lifetime"]["current"]
            changed = evidence["evidence_lifetime"]["after_edit"]
            self.assertEqual(current["state"], "ANSWERED")
            self.assertEqual(changed["state"], "STALE")
            self.assertEqual(current["attempts"], 1)
            self.assertEqual(changed["attempts"], current["attempts"])
            self.assertNotEqual(current["source_sha256"], changed["source_sha256"])
            self.assertIn("checkout.py", changed["reason"])

    def test_missing_bundled_dependency_cannot_use_source_checkout(self):
        # Each negative control changes its own package, never the source tree.
        for relative in ("examples/first-proof/proof_state.py",
                         ".v4/checkers.json"):
            with self.subTest(relative=relative), tempfile.TemporaryDirectory(
                    prefix="vibeproof-package-missing-") as directory:
                package = Path(directory)
                copy_demo(package)
                (package / "07-demo" / relative).unlink()
                result = self.run_packaged_demo(package)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(Path(relative).name, result.stdout + result.stderr)
                self.assertFalse((package / "observed/demo.json").exists())


class RecordedPromotionSources(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="vibeproof-recording-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.evidence = self.root / "docs/launch/evidence"
        shutil.copytree(ROOT / "docs/launch/evidence/media-baseline",
                        self.evidence / "media-baseline")
        for name in ("demo.json", "media-baseline.json"):
            shutil.copy2(ROOT / "docs/launch/evidence" / name, self.evidence / name)
        self.proof = self.evidence / "demo.json"
        self.manifest = self.evidence / "media-baseline.json"

    def test_each_dated_recording_accepts_its_original_bytes(self):
        for proof in (self.proof, self.evidence / "media-baseline/demo.json"):
            with self.subTest(proof=proof):
                self.assertEqual(recorded_source_problems(self.root, proof), [])

    def test_unrecognized_evidence_is_not_an_archived_recording(self):
        self.proof.write_bytes(self.proof.read_bytes() + b"\n")
        self.assertIn("one provenance entry", " ".join(
            recorded_source_problems(self.root, self.proof)))

    def test_incomplete_source_inventory_fails(self):
        baseline = json.loads(self.manifest.read_text())
        del baseline["recordings"][1]["implementation_sources"]["kernel/lifecycle.py"]
        self.manifest.write_text(json.dumps(baseline))
        self.assertIn("inventory differs", " ".join(
            recorded_source_problems(self.root, self.proof)))

    def test_missing_archive_fails_even_if_live_source_matches(self):
        relative = "kernel/lifecycle.py"
        archive = self.evidence / "media-baseline/2026-09-14" / (relative + ".txt")
        live = self.root / relative
        live.parent.mkdir(parents=True)
        shutil.copy2(archive, live)
        archive.unlink()
        self.assertIn(relative, " ".join(recorded_source_problems(self.root, self.proof)))

    def test_changed_archive_bytes_fail(self):
        relative = "kernel/lifecycle.py"
        archive = self.evidence / "media-baseline/2026-09-14" / (relative + ".txt")
        archive.write_text("changed historical source\n")
        self.assertIn(relative, " ".join(recorded_source_problems(self.root, self.proof)))

    def test_malformed_manifest_reports_a_failure(self):
        self.manifest.write_text("{broken")
        self.assertIn("Cannot verify", " ".join(
            recorded_source_problems(self.root, self.proof)))


if __name__ == "__main__":
    unittest.main()
