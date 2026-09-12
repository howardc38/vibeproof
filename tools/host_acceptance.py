#!/usr/bin/env python3
"""Exercise an installed framework in disposable application repositories.

No original checkout is changed. Evidence is explicit about requested, executed,
and verified cases. Native model runs are a separate opt-in stage.
"""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

FRAMEWORK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(FRAMEWORK))


def inventory():
    tree = ast.parse((FRAMEWORK / "kernel/cli.py").read_text())
    commands = sorted({n.args[0].value for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "add_parser" and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "sub" and n.args and isinstance(n.args[0], ast.Constant)})
    return {"commands": commands,
            "lenses": sorted(p.stem for p in (FRAMEWORK / ".v4/lenses").glob("*.json")),
            "claude_agents": sorted(p.stem for p in (FRAMEWORK / ".claude/agents").glob("*.md")),
            "claude_commands": sorted(p.stem for p in (FRAMEWORK / ".claude/commands").glob("*.md")),
            "codex_agents": sorted(p.stem for p in (FRAMEWORK / ".codex/agents").glob("*.toml")),
            "codex_skills": sorted(p.parent.name for p in (FRAMEWORK / ".agents/skills").glob("*/SKILL.md")),
            "hooks": sorted(p.stem for p in (FRAMEWORK / "hooks").glob("*.py")
                            if not p.name.startswith("_"))}


class Matrix:
    def __init__(self, out, host, repo=None):
        self.out = Path(out).resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        self.repo = Path(repo).resolve() if repo else Path(tempfile.mkdtemp(prefix=host + "-adopter-")).resolve()
        self.host = host
        prior = self.out / (self.repo.name + ".json")
        self.results = json.loads(prior.read_text())["cases"] if repo and prior.is_file() else []
        self.env = {k: v for k, v in os.environ.items()
                    if k in ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TZ")}
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        self.save()

    def save(self):
        data = {"framework": str(FRAMEWORK), "repo": str(self.repo), "host": self.host,
                "inventory": inventory(), "cases": self.results,
                "commands_executed": sorted({r["argv"][0] for r in self.results
                    if r.get("kind") == "cli" and r["code"] is not None})}
        (self.out / (self.repo.name + ".json")).write_text(json.dumps(data, indent=2) + "\n")

    def run(self, label, argv, *, expected=(0,), kind="cli", timeout=1800, repo=None):
        working = Path(repo) if repo else self.repo
        cmd = [str(FRAMEWORK / "bin/v4"), "--repo", str(working), *argv] if kind == "cli" else argv
        start = time.monotonic()
        try:
            r = subprocess.run(cmd, cwd=working, env=self.env, capture_output=True,
                               text=True, timeout=timeout)
            code, stdout, stderr = r.returncode, r.stdout, r.stderr
        except subprocess.TimeoutExpired as exc:
            code, stdout, stderr = None, str(exc.stdout or ""), "timed out"
        number = len(self.results) + 1
        log = self.out / f"{self.repo.name}-{number:03d}.log"
        log.write_text(stdout + "\nSTDERR:\n" + stderr)
        result = {"case": label, "kind": kind, "argv": argv, "code": code,
                  "expected": list(expected), "ok": code in expected, "log": str(log), "repo": str(working),
                  "seconds": round(time.monotonic() - start, 3)}
        self.results.append(result)
        self.save()
        print(json.dumps(result), flush=True)
        return code, stdout

    def git(self, *argv):
        return self.run("git " + " ".join(argv[:2]), ["git", *argv], kind="git")[0]

    def setup(self):
        self.git("init", "-q")
        self.git("config", "user.name", "Vibeproof acceptance")
        self.git("config", "user.email", "acceptance@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "checkout.py").write_text(
            "def total(price, discount=0):\n    return price + discount\n")
        (self.repo / "tests").mkdir()
        (self.repo / "tests/test_checkout.py").write_text(
            "import unittest\nfrom checkout import total\n"
            "class Checkout(unittest.TestCase):\n"
            "    def test_no_discount(self):\n        self.assertEqual(total(100), 100)\n")
        (self.repo / ".gitignore").write_text("__pycache__/\n*.pyc\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "Disposable application's baseline")
        self.run("initialize selected hosts", ["init", "--hosts", self.host])
        p = self.repo / ".v4/config.json"
        cfg = json.loads(p.read_text())
        cfg.update(test_command="python3 -m unittest discover -s tests -v",
                   surface_command=None, runtime_proof=[], truth_command=None)
        p.write_text(json.dumps(cfg, indent=2) + "\n")
        a = self.repo / ".v4/acceptance.json"
        criteria = json.loads(a.read_text())
        criteria["criteria"] = ["total(100, 20) is 80", "negative discount raises ValueError"]
        a.write_text(json.dumps(criteria, indent=2) + "\n")
        code, _ = self.run("install and register real fixtures", [
            "install", "--hosts", self.host, "--activate-hooks"], timeout=1800)
        if code != 0:
            raise RuntimeError("installation failed; inspect its evidence log")
        return self.repo



    def verify(self, label, condition, detail):
        self.results.append({"case": label, "kind": "readback", "argv": [], "code": 0 if condition else 1,
                             "ok": bool(condition), "detail": detail})
        self.save()
        print(json.dumps(self.results[-1]), flush=True)

    def finalize_setup(self):
        from kernel import doctrine, config
        for draft in self.repo.glob(".v4/facts.*.json.draft"):
            facts = json.loads(draft.read_text())
            for key in facts.get("absent", {}):
                facts["absent"][key] = (
                    "Confirmed from checkout.py: this application is a pure price/discount "
                    "calculation called by unittest. It has no network or storage calls, "
                    "authentication decision, service entry point or user interface.")
            draft.with_suffix("").write_text(json.dumps(facts, indent=2) + "\n")
            draft.unlink()
        doctrine.write(config.RepoConfig(self.repo))
        self.run("facts schema and stated absences", ["facts", "validate"])
        self.git("add", "-A")
        self.git("commit", "-qm", "Install the selected hosts before application work")
        _, commit = self.run("installed baseline", ["git", "rev-parse", "HEAD"], kind="git")
        (self.out / (self.repo.name + "-installed-head.txt")).write_text(commit.strip() + "\n")

    def exercise_cli(self):
        from kernel import ledger, review
        request = "Apply a fixed discount once in checkout.py and reject negative discounts."
        self.run("open measurement round", ["round", "open", "--label", "host-acceptance"])
        self.run("open application task", ["task", "--id", "t-fix", "--request", request,
                                         "--scope", "checkout.py,tests/test_checkout.py"])
        self.run("bind parent context", ["host", "bind", "--task", "t-fix", "--session", "matrix"])
        self.run("permission proposal", ["host", "permissions"])
        self.run("permission proposal with Git", ["host", "permissions", "--git-operations"])
        self.run("scope read", ["scope", "show", "--task", "t-fix"])
        why = "Exercise reversible scope declarations for notes.md in this temporary acceptance task without changing notes.md."
        self.run("scope widen", ["scope", "widen", "--task", "t-fix", "--add", "notes.md", "--why", why])
        self.run("scope narrow", ["scope", "narrow", "--task", "t-fix", "--drop", "notes.md", "--why", why])
        self.run("foresee callers", ["foresee", "--scope", "checkout.py", "--json"])
        self.run("derive pre-edit claims", ["derive", "--task", "t-fix"])
        self.run("status before answering", ["status", "--task", "t-fix", "--json"], expected=(1,))
        self.run("explain test mechanism", ["explain", "--kind", "test"])
        self.run("engagement before implementation", ["engage", "--task", "t-fix", "--kind", "test",
            "--actor", "splitter", "--text",
            "tests/test_checkout.py must execute checkout.total with a nonzero discount; a zero-discount test alone cannot prove this arithmetic repair."])
        # A deliberately broken implementation and a genuine finding, not a fabricated PASS.
        self.run("raise arithmetic finding", ["review", "add", "--task", "t-fix",
            "--file", "checkout.py", "--symbol", "total", "--note",
            "checkout.total adds the discount instead of subtracting it."])
        conn = ledger.connect_readonly(self.repo)
        claims = [dict(r) for r in conn.execute("SELECT * FROM claim WHERE task_id='t-fix'")]
        cid = next(r["id"] for r in claims if r["kind"] == "review-finding")
        conn.close()
        self.run("amend the finding explanation", ["review", "amend", "--claim", cid, "--note",
            "A price of 100 with a discount of 20 returns 120 instead of 80 in checkout.total."])
        self.run("independent sighting of the same defect", ["review", "add", "--task", "t-fix",
            "--file", "checkout.py", "--symbol", "total", "--lens", "test-sufficiency", "--note",
            "The discount regression must observe subtraction rather than the existing addition."])
        conn = ledger.connect_readonly(self.repo)
        second = next(r["id"] for r in conn.execute("SELECT id FROM claim WHERE task_id='t-fix' AND kind='review-finding'") if r["id"] != cid)
        conn.close()
        self.run("group the finding by its cause", ["review", "group", "--name", "discount arithmetic",
            "--claim", cid, "--claim", second, "--why", "The only application arithmetic function applies the discount with the wrong sign."])
        self.run("explicitly defer before repair", ["review", "defer", "--claim", cid,
            "--why", "The next step in this acceptance run repairs the arithmetic and supplies a real regression test.",
            "--target", "tests/test_checkout.py"])
        parent = self.git_output("rev-parse", "HEAD")
        (self.repo / "tests/test_checkout.py").write_text(
            "import unittest\nfrom checkout import total\n"
            "class Checkout(unittest.TestCase):\n"
            "    def test_no_discount(self):\n        self.assertEqual(total(100), 100)\n"
            "    def test_discount_is_subtracted(self):\n        self.assertEqual(total(100,20),80)\n"
            "    def test_negative_discount_is_rejected(self):\n"
            "        with self.assertRaises(ValueError):\n            total(100,-1)\n")
        self.run("regression is red before repair",
                 [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"],
                 kind="behavior", expected=(1,))
        (self.repo / "checkout.py").write_text(
            "def total(price, discount=0):\n"
            "    if discount < 0:\n        raise ValueError('negative discount')\n"
            "    return price - discount\n")
        self.run("regression is green after repair",
                 [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], kind="behavior")
        self.run("bind real closing proof", ["review", "close", "--claim", cid,
            "--test", "tests/test_checkout.py", "--command",
            "python3 -m unittest discover -s tests -v", "--parent", parent])
        self.run("close independent finding with the same real proof", ["review", "close", "--claim", second,
            "--test", "tests/test_checkout.py", "--command", "python3 -m unittest discover -s tests -v", "--parent", parent])
        self.run("derive edited subjects", ["derive", "--task", "t-fix"])
        self.engage_all("t-fix")
        self.run("account for requested behavior", ["cover", "--task", "t-fix",
            "--quote", request, "--symbol", "checkout.py::total", "--test", "tests/test_checkout.py",
            "--acceptance", "total(100,20) returns 80 and total(100,-1) raises ValueError"])
        self.run("request coverage readback", ["cover", "--task", "t-fix", "--show"])
        self.run("check complete application task", ["check", "--task", "t-fix", "--all"])
        self.run("status with actual attempt output", ["status", "--task", "t-fix", "--detail"], expected=(0, 1))
        self.run("risk waiting is visible", ["risk", "waiting", "--task", "t-fix"])
        self.run("record labelled synthetic cost", ["cost", "record", "--task", "t-fix", "--tokens", "0", "--wall-ms", "1"])
        self.run("read cost provenance", ["cost", "show"])
        self.run("adopter without an operational-risk rubric is unsupported", ["coverage"], expected=(4,))
        self.run("read framework operational-risk coverage", ["coverage"], repo=FRAMEWORK)
        self.run("read actual ledger trend", ["trend", "--json"])
        self.run("remerge status", ["remerge", "show"])
        self.run("record remerge evidence", ["remerge", "record", "--task", "t-fix"])
        self.run("read host coverage without assuming hooks ran", ["host", "status", "--task", "t-fix"])
        self.run("ship predicate", ["ship", "--task", "t-fix"])
        self.run("export portable evidence", ["export", "--out", str(self.out / (self.repo.name + "-ledger.jsonl"))])
        self.run("audit live chain", ["audit"])
        self.run("audit exported chain", ["audit", "--events", str(self.out / (self.repo.name + "-ledger.jsonl"))])
        self.run("doctor reports remaining wiring", ["doctor"], expected=(0, 1))
        self.run("check generated doctrine", ["doctrine", "--check"])
        self.run("doctrine engagement audit", ["doctrine", "--audit"])
        self.exercise_registries()
        self.exercise_lenses()
        self.exercise_maintenance()
        self.run("close measurement round", ["round", "close", "--label", "host-acceptance",
                                           "--note", "Disposable host acceptance measurements completed"])
        self.run("open abandonment control", ["task", "--id", "t-abandon", "--request",
            "Exercise abandonment without changing application files.", "--scope", "checkout.py"])
        self.run("abandon explicitly", ["abandon", "--task", "t-abandon", "--why",
            "This task exists only to verify that abandonment records an ending and retains the evidence."])
        self.git("add", "-A")
        self.run("isolated application acceptance", ["accept", "--tests"])
        missing = sorted(set(inventory()["commands"]) - {r["argv"][0] for r in self.results if r["kind"] == "cli"})
        self.verify("every top-level command was executed", not missing, {"missing": missing})

    def git_output(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env, text=True).strip()

    def engage_all(self, task):
        from kernel import ledger
        conn = ledger.connect_readonly(self.repo)
        claims = [dict(r) for r in conn.execute("SELECT * FROM claim WHERE task_id=?", (task,))]
        conn.close()
        texts = {
            "test": "tests/test_checkout.py calls checkout.total with zero, positive and negative discounts; its real assertions must observe the requested behavior rather than inspect source text.",
            "scope": "checkout.py and tests/test_checkout.py are the complete allowed application repair. No installed registry, checker, host setting or generated instruction is part of this task's edit.",
            "secret": "checkout.py and tests/test_checkout.py use numeric examples and ValueError only. They contain no credential material and call no network or secret-bearing environment.",
            "review-finding": "checkout.total is the single site with this sign defect. The same regression test must fail before the repair, pass afterwards, and execute total; a textual diff alone does not close it.",
            "fail-closed": "checkout.py rejects a negative discount by raising ValueError before performing the calculation. There is no catch handler that can swallow the failure and continue.",
        }
        kinds = json.loads((self.repo / ".v4/claim_kinds.json").read_text())
        for c in claims:
            if not kinds[c["kind"]].get("engagement"):
                continue
            text = texts.get(c["kind"], f"checkout.py and tests/test_checkout.py must satisfy the {c['kind']} rule on their actual changed behavior; this probe preserves the installed checker and evaluates its recorded result.")
            self.run("read engagement " + c["kind"], ["engage", "--claim", c["id"]])
            self.run("record engagement " + c["kind"], ["engage", "--claim", c["id"], "--text", text])

    def exercise_registries(self):
        checkers = json.loads((self.repo / ".v4/checkers.json").read_text())
        cid = "test"
        entry = checkers[cid]
        self.run("verify installed checker fixtures", ["verify", "--checker", entry["path"],
            "--fixtures", entry["fixtures"], "--kind", entry["kinds"][0]])
        self.run("register identical validated checker", ["register", "--id", cid,
            "--checker", entry["path"], "--fixtures", entry["fixtures"],
            "--kinds", ",".join(entry["kinds"]), "--reads", ",".join(entry.get("reads", ["**"]))])
        detectors = json.loads((self.repo / ".v4/detectors.json").read_text())
        name, det = next(iter(detectors.items()))
        path = det.get("path", name)
        for verb in ("verify-detector", "register-detector"):
            self.run(verb + " on installed fixtures", [verb, "--detector", path, "--fixtures", det["fixtures"]])
        subject = self.out / (self.repo.name + "-subject.json")
        subject.write_text(json.dumps({"repo_root": str(self.repo), "claim_kind": "test",
            "claim_id": "probe", "task_id": "t-fix", "diff_base": self.git_output("rev-parse", "HEAD"),
            "subject_refs": [], "params": {}}))
        self.run("run checker through public CLI", ["run-checker", "--checker", str(self.repo / entry["path"]),
            "--subject", str(subject)], expected=(0, 1, 4))

    def exercise_lenses(self):
        # These exercise all briefs/recording; actual independent model review is a separate stage.
        for lens in inventory()["lenses"]:
            self.run("lens brief " + lens, ["review", "lens", "--lens", lens])
            self.run("lens completion record " + lens, ["review", "done", "--lens", lens, "--findings", "0"])
        self.run("sweep history readback", ["sweep", "--history"])
        self.run("sweep due decision", ["sweep", "--if-due"], expected=(0, 1))
        self.run("record synthetic zero-finding sweep", ["sweep", "--done", "--findings", "0",
            "--note", "This controlled CLI probe rendered every lens and submitted zero findings; it is not a model review."])



    def exercise_maintenance(self):
        self.run("maintenance schema", ["maintain", "schema"])
        self.run("read maintenance state", ["maintain", "status"])
        data=self.out / (self.repo.name + "-maintenance-control.json")
        data.write_text(json.dumps({"mode":"review","limits":{"max_parallel_agents":2}}))
        self.run("configure review without creating a host job", ["maintain", "setup", "--data", str(data)])
        data.write_text(json.dumps({"limits":["invented prose limit"]}))
        self.run("reject unread maintenance limits", ["maintain", "setup", "--data", str(data)], expected=(2,))
        self.run("pause requires actual host action", ["maintain", "pause"])
        self.run("resume requires actual host action", ["maintain", "resume"])
        host="claude" if self.host=="both" else self.host
        lens=inventory()["lenses"][0]
        rid="cli-selected-review"
        self.run("start explicitly selected review", ["maintain","start","--host",host,"--id",rid,"--lens",lens])
        self.run("versioned lens brief", ["review","lens","--run",rid,"--lens",lens])
        self.run("record labelled synthetic lens completion", ["review","done","--run",rid,"--lens",lens,"--findings","0","--note","CLI protocol fixture only; no model reading is claimed"])
        self.run("finish selected review coverage", ["maintain","finish","--id",rid])
        self.run("unconfigured notification status", ["maintain","notifications"])
        self.run("acknowledgement needs configured transport", ["maintain","ack","--id","missing"],expected=(2,))
        self.run("receiver needs configured transport", ["maintain","receiver"],expected=(2,))
        self.run("listen needs configured transport", ["maintain","listen","--once"],expected=(2,))

    def exercise_auxiliary(self):
        from kernel import ledger
        request = "Record risk signature provenance. Leave a refund service outside this fixture."
        self.run("open risk attribution probe", ["task", "--id", "t-risk", "--request", request,
                                               "--scope", "checkout.py"])
        self.run("raise explicit unresolved fixture policy", ["review", "add", "--task", "t-risk",
            "--file", "checkout.py", "--symbol", "total",
            "--note", "This isolated fixture leaves a maximum-discount policy unspecified; only subtraction is in its contract."])
        c = ledger.connect_readonly(self.repo)
        claim = c.execute("SELECT id FROM claim WHERE task_id='t-risk'").fetchone()[0]
        c.close()
        why = "This synthetic acceptance probe intentionally leaves the discount cap unspecified; it verifies signature attribution, not a production billing decision."
        self.run("monitor cannot sign a hand-raised finding", ["risk", "accept", "--claim", claim,
            "--kind", "unprovable", "--why", why, "--as-monitor"], expected=(2,))
        self.run("non-TTY does not silently sign as a person", ["risk", "accept", "--claim", claim,
            "--kind", "unprovable", "--why", why], expected=(2,))
        self.run("explicit synthetic agent signature", ["risk", "accept", "--claim", claim,
            "--kind", "unprovable", "--why", why, "--no-tty-check"])
        c = ledger.connect_readonly(self.repo)
        signed = c.execute("SELECT signed_by,was_tty FROM accepted_risk WHERE claim_id=?",
                           (claim,)).fetchone()
        c.close()
        self.verify("agent signature attribution readback",
                    signed is not None and tuple(signed) == ("agent", 0),
                    {"signed_by": signed[0] if signed else None, "was_tty": signed[1] if signed else None})
        quote = "Leave a refund service outside this fixture"
        self.run("record deliberate non-delivery", ["cover", "--task", "t-risk", "--quote", quote,
            "--not-done", "--why", "The fixture deliberately does not implement a refund service; this probe only measures risk metadata."])
        self.run("withdraw accounting without erasing history", ["cover", "--task", "t-risk", "--quote", quote,
            "--withdraw", "--why", "This acceptance control withdraws the earlier accounting entry and will inspect both recorded events."])
        self.run("read withdrawn accounting", ["cover", "--task", "t-risk", "--show"])
        self.run("end risk-only probe", ["abandon", "--task", "t-risk", "--why",
            "The synthetic attribution and withdrawal controls have completed; no application change is being shipped by this probe."])
        self.run("waiting includes ended tasks only explicitly", ["risk", "waiting", "--ended"])
        self.run("composition audit runs", ["audit", "--compositions"])
        self.run("explicit doctrine generation", ["doctrine", "--write"])
        self.facts_probe()

    def facts_probe(self):
        other = Path(tempfile.mkdtemp(prefix="facts-probe-", dir=self.out))
        for argv in (["init", "-q"], ["config", "user.name", "Facts probe"],
                     ["config", "user.email", "facts@example.invalid"],
                     ["config", "commit.gpgsign", "false"], ["config", "maintenance.auto", "false"]):
            self.run("facts fixture git " + argv[0], ["git", *argv], kind="git", repo=other)
        source = "from pathlib import Path\ndef write(path, value):\n    Path(path).write_text(value)\ndef read(path):\n    return Path(path).read_text()\n"
        (other / "storage.py").write_text(source)
        (other / ".v4").mkdir()
        (other / ".v4/config.json").write_text(json.dumps({
            "test_command": "python3 -m unittest discover", "policy": "allow_accepted_risk"}))
        self.run("facts fixture commit", ["git", "add", "-A"], kind="git", repo=other)
        self.run("facts fixture commit", ["git", "commit", "-qm", "Real local read and write sites"], kind="git", repo=other)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=other, text=True).strip()
        data = {"repo": other.name, "generated_from_commit": head,
                "outbound_write": [{"pattern": ".write_text", "seen_at": "storage.py:3", "kind": "fs"}],
                "outbound_read": [{"pattern": ".read_text", "seen_at": "storage.py:5", "kind": "fs"}],
                "auth_decision": [], "absent": {"auth_decision": "The two local storage helpers make no authorization decisions.",
                "entrypoint_globs": "storage.py defines only two imported helpers; it has no CLI, server, worker loop or main entry point."},
                "entrypoint_globs": [], "ui_globs": [], "config_files": [], "protected_paths": [".v4/**"]}
        table = other / ".v4" / ("facts." + other.name + ".json")
        table.write_text(json.dumps(data, indent=2) + "\n")
        self.run("actual local truth-owner readback", [sys.executable, "-c",
            "from storage import write,read;write('value.txt','observed');assert read('value.txt')=='observed';print('OWNER_READBACK_OK')"],
            kind="behavior", repo=other)
        for verb in ("propose", "validate", "verify"):
            self.run("facts " + verb + " through public CLI", ["facts", verb], repo=other)
        self.run("facts category routing", ["facts", "scan", "outbound_write", "--sites"], repo=other)
        (other / "storage.py").write_text("\n" + source)
        self.run("facts detects moved citation", ["facts", "verify"], expected=(1,), repo=other)
        self.run("facts distinguishes moved from gone", ["facts", "verify", "--gone-only"], repo=other)
        self.run("facts restates actual moved sites", ["facts", "restate"], repo=other)
        updated = json.loads(table.read_text())
        self.verify("restate readback follows real source lines",
                    updated["outbound_write"][0]["seen_at"] == "storage.py:4" and
                    updated["outbound_read"][0]["seen_at"] == "storage.py:6", updated)
        self.run("facts verifies repaired citations", ["facts", "verify"], repo=other)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--hosts", choices=("claude", "codex", "both"), default="both")
    p.add_argument("--inventory-only", action="store_true")
    p.add_argument("--resume", type=Path)
    p.add_argument("--setup-only", action="store_true")
    args = p.parse_args()
    if args.inventory_only:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "inventory.json").write_text(json.dumps(inventory(), indent=2) + "\n")
        return 0
    matrix = Matrix(args.out, args.hosts, args.resume)
    if not args.resume:
        matrix.setup()
    if not args.setup_only:
        matrix.finalize_setup()
        matrix.exercise_cli()
        matrix.exercise_auxiliary()
    print("SETUP COMPLETE " + str(matrix.repo))
    return 0 if all(r["ok"] for r in matrix.results) else 1


if __name__ == "__main__":
    sys.exit(main())
