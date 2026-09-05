#!/usr/bin/env python3
"""Reproduce a misleading green suite and verify a real repair, offline."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


FRAMEWORK = Path(__file__).resolve().parents[2]
BASE_CODE = "def total(subtotal, discount):\n    return subtotal\n"
WRONG_CODE = "def total(subtotal, discount):\n    return subtotal + discount\n"
FIXED_CODE = "def total(subtotal, discount):\n    return subtotal - discount\n"
UNRELATED_TEST = '''import unittest

class TestCheckout(unittest.TestCase):
    def test_arithmetic(self):
        self.assertEqual(2 + 2, 4)

if __name__ == "__main__":
    unittest.main(verbosity=2)
'''
REAL_TEST = '''import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from checkout import total

class TestCheckout(unittest.TestCase):
    def test_discount_reduces_total(self):
        self.assertEqual(total(100, 20), 80)

if __name__ == "__main__":
    unittest.main(verbosity=2)
'''
TEST_COMMAND = "python3 -m unittest discover -s tests -v"


class DemoFailed(RuntimeError):
    pass


def _write(root, relative, body):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _environment():
    env = dict(os.environ)
    # The demo must observe its own interpreter, git repository and coverage.
    for key in list(env):
        if key.startswith(("GIT_", "V4_")) or key in {
            "PYTHONPATH", "PYTHONHOME", "NODE_V8_COVERAGE", "GOFLAGS"
        }:
            env.pop(key, None)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def demonstrate(output=None, quiet=False):
    if sys.version_info < (3, 12):
        raise DemoFailed("Use Python 3.12 or newer.")
    if not shutil.which("git"):
        raise DemoFailed("Git is required; install it before running this demo.")
    env = _environment()
    records = []
    started = time.monotonic()
    evidence = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario": "A 20-unit discount on a 100-unit cart should leave 80.",
        "disclosure": "An intentionally constructed example, not a recording of an AI session. "
                      "Every command result below comes from an actual subprocess. "
                      "This runs two checkers directly, not a full task/ship lifecycle.",
        "source_files": {"before": BASE_CODE, "wrong": WRONG_CODE,
                         "fixed": FIXED_CODE, "unrelated_test": UNRELATED_TEST,
                         "regression_test": REAL_TEST},
        "steps": records,
    }
    with tempfile.TemporaryDirectory(prefix="vibeproof-demo-") as directory:
        root = Path(directory).resolve()

        def execute(argv):
            return subprocess.run(argv, cwd=root, env=env, capture_output=True,
                                  text=True, timeout=90)

        def git(*args):
            result = execute(["git", "-c", "core.hooksPath=/dev/null", *args])
            if result.returncode:
                raise DemoFailed(f"Git failed: {result.stderr.strip()}")
            return result.stdout.strip()

        def record(identifier, title, argv, display, expected, contains=()):
            result = execute(argv)
            raw = (result.stdout or "") + (result.stderr or "")
            text = raw.replace(str(root), "<demo>").replace(str(FRAMEWORK), "<vibeproof>")
            item = {"id": identifier, "title": title, "command": display,
                    "expected_exit": expected, "exit_code": result.returncode,
                    "output": text.strip()}
            records.append(item)
            if not quiet:
                print(f"\n[{len(records)}] {title}\n$ {display}\n{text.strip()}", flush=True)
                print(f"exit {result.returncode}", flush=True)
            if result.returncode != expected or any(s not in raw for s in contains):
                raise DemoFailed(f"{identifier}: expected exit {expected} and {contains!r}; "
                                 f"got exit {result.returncode}:\n{text}")
            return result

        git("init", "-q")
        git("config", "user.name", "vibeproof demo")
        git("config", "user.email", "demo@example.invalid")
        git("config", "commit.gpgsign", "false")
        _write(root, "checkout.py", BASE_CODE)
        _write(root, "tests/test_checkout.py", UNRELATED_TEST)
        _write(root, ".gitignore", "__pycache__/\n*.pyc\n")
        git("add", ".")
        git("commit", "-qm", "Before the discount feature")
        baseline = git("rev-parse", "HEAD")
        _write(root, ".v4/config.json", json.dumps({"test_command": TEST_COMMAND}))
        _write(root, "checkout.py", WRONG_CODE)
        subject = root / "subject.json"
        subject.write_text(json.dumps({"repo_root": str(root), "diff_base": baseline,
                                       "subject_refs": [{"kind": "file", "path": "checkout.py"}]}))
        checker = [sys.executable, str(FRAMEWORK / "checkers/test.py"),
                   "--subject", str(subject)]
        tests = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
        record("green_suite", "The unrelated test is green", tests, TEST_COMMAND, 0,
               ("Ran 1 test", "OK"))
        record("wrong_result", "The feature still returns the wrong total",
               [sys.executable, "-c", "from checkout import total; print(total(100, 20))"],
               'python3 -c "from checkout import total; print(total(100, 20))"', 0, ("120",))
        record("unexecuted", "vibeproof finds that the suite never reached the changed file",
               checker, "python3 <vibeproof>/checkers/test.py --subject subject.json", 1,
               ("executed none of the 1 changed file(s)", "checkout.py"))
        git("add", "checkout.py")
        git("commit", "-qm", "The deliberately wrong discount implementation")
        wrong_commit = git("rev-parse", "HEAD")
        _write(root, "tests/test_checkout.py", REAL_TEST)
        record("regression_red", "A real regression test exposes the wrong total",
               tests, TEST_COMMAND, 1, ("120 != 80", "FAILED"))
        _write(root, "checkout.py", FIXED_CODE)
        record("suite_verified", "The repaired code passes and the suite reaches the change",
               checker, "python3 <vibeproof>/checkers/test.py --subject subject.json", 0,
               ("test_discount_reduces_total", "OK"))
        review_subject = root / "review-subject.json"
        review_subject.write_text(json.dumps({
            "repo_root": str(root), "file": "checkout.py", "symbol": "total",
            "params": {"closing_test": "tests/test_checkout.py",
                       "parent_commit": wrong_commit, "test_one_file_command": "python3 {path}"},
        }))
        record("repair_verified", "The review checker verifies red, green and function execution",
               [sys.executable, str(FRAMEWORK / "checkers/review_finding.py"),
                "--subject", str(review_subject)],
               "python3 <vibeproof>/checkers/review_finding.py --subject review-subject.json", 0,
               ("fails at", "passes at HEAD", "executed total"))
        record("correct_result", "The repaired cart total is 80",
               [sys.executable, "-c", "from checkout import total; print(total(100, 20))"],
               'python3 -c "from checkout import total; print(total(100, 20))"', 0, ("80",))
        # Negative controls keep the published demonstration honest about limits.
        _write(root, "checkout.py", WRONG_CODE)
        _write(root, "tests/test_checkout.py", "import checkout\n" + UNRELATED_TEST)
        record("limit_import_only", "Limit: importing the file is enough for the ordinary test checker",
               checker, "python3 <vibeproof>/checkers/test.py --subject subject.json", 0, ("OK",))
        _write(root, "tests/test_checkout.py", UNRELATED_TEST)
        record("limit_unrelated_closure", "An unrelated test cannot verify the repair",
               [sys.executable, str(FRAMEWORK / "checkers/review_finding.py"),
                "--subject", str(review_subject)],
               "python3 <vibeproof>/checkers/review_finding.py --subject review-subject.json", 1,
               ("FAIL:",))

    tracked = ["checkers/test.py", "checkers/review_finding.py", "kernel/redgreen.py",
               "examples/first-proof/run.py"]
    evidence["implementation_sha256"] = {
        rel: hashlib.sha256((FRAMEWORK / rel).read_bytes()).hexdigest() for rel in tracked
    }
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=FRAMEWORK,
                              env=env, capture_output=True, text=True)
    evidence["framework_base_commit"] = revision.stdout.strip() if revision.returncode == 0 else None
    evidence["duration_seconds"] = round(time.monotonic() - started, 3)
    evidence["verified"] = True
    if output:
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        (output / "demo.json").write_text(json.dumps(evidence, indent=2) + "\n")
        transcript = [evidence["disclosure"]]
        for step in records:
            transcript.append(f"\n[{step['id']}] {step['title']}\n$ {step['command']}\n"
                              f"{step['output']}\nexit {step['exit_code']}")
        (output / "demo.txt").write_text("\n".join(transcript) + "\n")
    if not quiet:
        print("\nDEMO VERIFIED: expected failures, repair proof and negative controls all matched.")
        print("No full framework installation, network calls or edits to your project were needed.")
    return evidence


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="save the measured transcript and JSON here")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()
    try:
        demonstrate(args.output, args.quiet)
    except (DemoFailed, subprocess.TimeoutExpired, OSError) as exc:
        print(f"DEMO FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
