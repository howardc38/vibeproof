#!/usr/bin/env python3
"""Trigger it, then go and read the row.  PL-3.

Six cuts shipped, every claim green, and the feature never ran once. Nothing was
wrong with any of the answers -- they were all about whether the change was safe,
and none of them was about whether it happened.

Every other checker reads the tree. This one runs the thing and then reads
somewhere else: the table the repo's own migrations name as the owner of that
data. The repo has already written down which table that is, in the words it
used at the time:

    0011_multi_brand_substrate.sql   brands table (tenant identity truth owner)
    0026_content_drafts_text…        content_drafts.text_content becomes truth owner

Why this is a checker and not a lens
------------------------------------
"This handler is probably not wired" is a guess, and guesses go to reviewers.
"Is there a row in `lead_magnets` matching what I just asked for" has one
answer, a program can ask it, and the answer is the same every time. The line
between the two layers is exactly that, and this question is on the checker side.

What a repo declares
--------------------
    "runtime_proof": [
      {"name": "a lead magnet reaches the table that owns it",
       "trigger": "python3 -m app.cli make-lead-magnet --dry-run",
       "truth": "select count(*) from lead_magnets where created_at > now() - interval '5 minutes'",
       "expect": "gt:0"}
    ]

`trigger` runs. `truth` is asked of whatever `truth_command` is -- a psql, a
sqlite3, a script -- and its stdout is compared to `expect`. Three forms only:
`gt:N`, `eq:N`, `contains:text`. A richer language here would become a place to
write the answer you wanted.

Exit
----
  0  every declared proof triggered and its truth owner agreed
  1  something triggered and the truth owner did not agree
  4  nothing declared, or no `truth_command` to ask -- this needs a live
     environment and saying so is not a finding about the code
  5  broke
"""

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel import config as config_mod  # noqa: E402

KIND = "runtime-proof"

#: What a repo that has declared nothing is told. The concept and the shape:
#: naming the key without showing the object leaves the adopter to find out
#: elsewhere that `{run_id}` exists and that `expect` has three forms.
NOT_DECLARED = """no `runtime_proof` declared. This is the one question that needs a live
environment: trigger the feature, then ask whatever owns that data -- not the
code that just wrote it.

In .v4/config.json:

    "runtime_proof": [
      {
        "name":    "what this proves, in one line",
        "trigger": "a command that exercises the feature, carrying {run_id}",
        "truth":   "a query the owner answers, naming {run_id}",
        "expect":  "gt:N"   |   "eq:N"   |   "contains:some text"
      }
    ],
    "truth_command": "the command that answers a query arriving on stdin"

`{run_id}` is a fresh id per run, substituted into `trigger` and `truth`, and
it is required in `truth`. Without it the query asks "is there a row" and the
answer may be a row from last week -- a trigger that does nothing passes.

`truth_command` is not necessarily a database. It is whatever owns the data:
`psql -tA -d <db>`, `sqlite3 <path>`, `gcloud storage ls gs://<bucket>/...`.
It must read the query from stdin; one that answers the same thing to an empty
query is refused, because it is not being asked anything."""


def _cfg(root: Path) -> dict:
    return json.loads((root / ".v4" / "config.json").read_text(encoding="utf-8"))


def usable(expect: str):
    """`None` if this expectation could ever be false, else why it could not.

    The registration gate asked for three ways past this checker and two of
    them were here: `gt:-1` against a count, and `contains:` against anything.
    Both pass whatever the truth owner says, which makes the proof a sentence
    rather than a question. Refusing them is not pedantry -- an expectation that
    cannot fail is the same shape as a test that cannot fail, which is the thing
    this whole framework was built to find.
    """
    kind, _, want = expect.partition(":")
    if kind == "contains" and not want.strip():
        return "`contains:` with nothing after it matches every possible answer"
    if kind == "gt":
        try:
            if int(want) < 0:
                return (f"`gt:{want}` against a count is true no matter what the "
                        f"truth owner says -- a count is never negative")
        except ValueError:
            return f"`gt:{want}` is not a number"
    if kind == "eq":
        try:
            int(want)
        except ValueError:
            return f"`eq:{want}` is not a number"
    if kind not in ("gt", "eq", "contains"):
        return f"unknown expectation {expect!r}; use gt:N, eq:N or contains:text"
    return None


def judge(expect: str, out: str):
    """`(ok, why)`.  Three forms, because a fourth becomes a place to hide."""
    out = (out or "").strip()
    kind, _, want = expect.partition(":")
    if kind == "contains":
        return (want in out), f"{want!r} {'in' if want in out else 'not in'} {out!r}"
    try:
        got = int(out.splitlines()[-1].strip()) if out else None
    except (ValueError, IndexError):
        return False, f"expected a number from the truth owner, got {out!r}"
    if got is None:
        return False, "the truth owner returned nothing"
    if kind == "gt":
        return got > int(want), f"{got} > {want}" if got > int(want) else f"{got} is not > {want}"
    if kind == "eq":
        return got == int(want), f"{got} == {want}" if got == int(want) else f"{got} != {want}"
    return False, f"unknown expectation {expect!r}; use gt:N, eq:N or contains:text"


#: The one substitution this checker makes, in both `trigger` and `truth`.
#: The kernel's exit vocabulary, by name. The `covers` branch returned
#: `UNSUPPORTED` and nothing here defined it, so the one path that says "this
#: proof is not about the change under review" raised `NameError` and the
#: kernel recorded exit 5 -- a broken checker -- for a verdict the checker had
#: reached correctly.
PASS, FAIL, UNSUPPORTED, ERROR = 0, 1, 4, 5

#: A fourth verdict from `run_one`, and the docstring above says a fourth
#: becomes a place to hide -- so this one is named and printed rather than
#: folded into `False`.
#:
#: `False` means the truth owner was asked and disagreed. A trigger that never
#: succeeded produces no such answer, and folding the two together printed
#: `1 proof(s) ran and the truth owner disagreed` over `curl ... exited 7`,
#: closing with `The trigger succeeded and the row is not there`. Both
#: sentences were false. Measured on an adopter: a task with no runtime code in
#: scope was failed by a server nobody had started, and the first line sent the
#: reader to the database.
NOT_TRIGGERED = "not-triggered"

RUN_ID = "{run_id}"


# No timeout on the three subprocesses below. `checkers.json` carries
# `timeout_sec` for this checker and `runner.py` enforces it; a second wall
# here made the smaller number win invisibly, and a slow trigger came back as
# exit 8 `checker exceeded Ns` -- CHECKER_ERROR, which reads as the checker
# being broken rather than the environment being slow. `checkers/test.py`
# states the rule and this file was written the day after with the same
# defect. One owner: register with `--timeout` if a trigger needs longer.
def _changed_paths(root, subject):
    """What this change touched, or `None` when that cannot be answered.

    `None` rather than `[]`: a subject with no `diff_base`, or a directory git
    cannot answer for, is not a change that touched nothing.
    """
    base = subject.get("diff_base")
    if not base:
        return None
    r = subprocess.run(["git", "diff", "--name-only", base], cwd=root,
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return [f for f in r.stdout.splitlines() if f.strip()]


def empty_query(root: Path, truth_command: str):
    """Ask the truth owner nothing, and keep what it said.

    A command that answers a query and an empty query the same way is not being
    asked anything -- found by declaring a `truth_command` that ignores stdin,
    under which every proof passed. It is a fact about the command, so it is
    asked once per invocation; it used to be asked inside `run_one`, which
    `main` calls once per declared proof, so a repo declaring five proofs ran
    five identical control queries against its own database.
    """
    return subprocess.run(truth_command, shell=True, cwd=root, input="",
                          capture_output=True, text=True)


def run_one(root: Path, truth_command: str, proof: dict, run_id="", control=None):
    name = proof.get("name") or proof.get("trigger", "?")
    for field in ("trigger", "truth", "expect"):
        if not proof.get(field):
            return None, f"{name}: no {field!r}"
    why_not = usable(proof["expect"])
    if why_not:
        return False, f"{name}: {why_not}"
    if RUN_ID not in proof["truth"]:
        # The whole difference between a proof and a sentence. Without it the
        # query asks "is there a row" and the answer may be a row from last
        # week -- demonstrated with `trigger: "true"` against a table an earlier
        # run had populated: exit 0, "the truth owner agreed", nothing written.
        #
        # The first version of this asked the query *before* the trigger and
        # refused if the expectation already held. That is the same idea and it
        # cannot work: it makes the answer depend on what the previous run left
        # behind, so running the checker twice on identical bytes gives two
        # verdicts -- the registration gate refused it on exactly that, and a
        # correctly written real proof (`created_at > now() - interval '5
        # minutes'`) would have failed on its second run within the window.
        #
        # A fresh id per invocation has neither problem: nothing that already
        # exists carries it, and every run is its own question.
        return False, (
            f"{name}: `truth` does not contain {RUN_ID}, so this query cannot "
            f"tell this run from any other and a trigger that does nothing "
            f"passes it. Put {RUN_ID} in what the trigger creates and in what "
            f"the query asks for.")
    proof = dict(proof)
    proof["trigger"] = proof["trigger"].replace(RUN_ID, run_id)
    proof["truth"] = proof["truth"].replace(RUN_ID, run_id)

    # Does the truth owner read the question at all? `empty_query` above says
    # why, and the caller passes the one answer in -- it is a fact about the
    # command, and this function is called once per declared proof.
    if control is None:
        control = empty_query(root, truth_command)

    t = subprocess.run(proof["trigger"], shell=True, cwd=root,
                       capture_output=True, text=True)
    if t.returncode != 0:
        # Its own verdict, not `False`. `False` is read by the caller as "the
        # truth owner disagreed", and here the truth owner was never asked.
        return NOT_TRIGGERED, (
            f"{name}: the trigger failed before anything could be "
            f"read back -- `{proof['trigger']}` exited {t.returncode}\n"
            + "  " + (t.stderr or t.stdout or "").strip()[-400:])
    q = subprocess.run(truth_command, shell=True, cwd=root, input=proof["truth"],
                       capture_output=True, text=True)
    if q.returncode != 0:
        # Cannot ask is not "the answer is no". The trigger already ran, so this
        # says nothing either way and must not be read as a finding.
        return None, (f"{name}: could not ask the truth owner "
                      f"(exit {q.returncode}): {(q.stderr or '').strip()[-200:]}")
    if control.returncode == 0 and control.stdout == q.stdout:
        return False, (f"{name}: `truth_command` answered {q.stdout.strip()!r} to "
                       f"this query and the same to an empty one, so it is not "
                       f"reading the query. Every proof under it would pass.")
    ok, why = judge(proof["expect"], q.stdout)
    return ok, f"{name}: {why}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--subject", required=True)
    p.add_argument("--facts")
    p.add_argument("--out")
    a = p.parse_args()
    try:
        s = json.loads(Path(a.subject).read_text(encoding="utf-8"))
        root = Path(s["repo_root"]).resolve()
        cfg = _cfg(root)
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject or config: {exc}", file=sys.stderr)
        return 5

    proofs = config_mod.declared(cfg, "runtime_proof") or []
    truth_command = config_mod.declared(cfg, "truth_command")

    # Which of the declared proofs is about the change under review.
    #
    # The claim reads "Did triggering this leave a row in the table that owns
    # the data?" and this never triggered *this*: the list is repo-level and
    # identical for every task, so every task got the same green from the same
    # probe. Measured: claim 285afb060f2092b7 on `repo-review` went ANSWERED
    # exit 0 with "2 proof(s) triggered, and the truth owner agreed" while the
    # review touched nothing that probe exercises. PL-8 -- the lens's own stated
    # origin -- is six ships green while the feature never ran, and the kind
    # built to close PL-8 was reproducing its shape.
    #
    # A proof may name the paths it exercises. One that does is run only when
    # the change touches them; one that does not is run and *said* to be
    # repo-level, so the reader knows which of the two greens this is. Optional,
    # because demanding it would refuse every table declared before today.
    changed = _changed_paths(root, s)
    if changed is not None:
        scoped = [pr for pr in proofs if pr.get("covers")]
        if scoped:
            from kernel.analysis.subject_files import matches
            live = [pr for pr in scoped
                    if any(matches(f, pr["covers"]) for f in changed)]
            unscoped = [pr for pr in proofs if not pr.get("covers")]
            if not live and not unscoped:
                print(f"CANNOT VERIFY: {len(scoped)} declared proof(s), and none "
                      f"of them covers anything this change touched "
                      f"({len(changed)} path(s)). A proof about a part of the "
                      f"system the diff did not reach answers a question nobody "
                      f"asked here.\n\n"
                      f"  add `covers` to a proof that does reach this change, "
                      f"or say why not `v4 risk accept --claim <id> "
                      f"--kind unprovable --why '…'`", file=sys.stderr)
                return UNSUPPORTED
            proofs = live + unscoped
    if not proofs:
        # The shape, not just the name. An adopter told only "declare
        # runtime_proof" has to go and find what the object looks like, that
        # `{run_id}` exists, and that `expect` takes three forms -- and the
        # first repo this was turned on for needed 160 lines of hand-written
        # prose to carry what belongs here. A JSON shape assumes no stack.
        print(NOT_DECLARED)
        return 4
    if not truth_command:
        print("`runtime_proof` is declared and `truth_command` is not, so there "
              "is nothing to ask. Name the command that answers a query on "
              "stdin -- a psql, a sqlite3, a `gcloud storage ls`, a script that "
              "prints one line.\n" + NOT_DECLARED)
        return 4

    # One per invocation, shared by every proof in it: they are all statements
    # about the same run. Never printed on the success path -- the registration
    # gate compares stdout across two runs, and an id in the output would make
    # every case "not deterministic" for a reason that has nothing to do with
    # the checker.
    run_id = "rt" + uuid.uuid4().hex[:12]
    results, failed, unanswerable, not_triggered = [], [], [], []
    control = empty_query(root, truth_command)
    for proof in proofs:
        try:
            ok, why = run_one(root, truth_command, proof, run_id=run_id,
                              control=control)
        except Exception as exc:                                # noqa: BLE001
            print(f"checker failed: {exc}", file=sys.stderr)
            return 5
        results.append({"proof": proof.get("name"), "ok": ok, "why": why})
        if ok is NOT_TRIGGERED:
            not_triggered.append(why)
        elif ok is False:
            failed.append(why)
        elif ok is None:
            unanswerable.append(why)

    if a.out:
        Path(a.out).write_text(json.dumps({"results": results}, indent=2))
    if not_triggered:
        print(f"FAIL: {len(not_triggered)} declared proof(s) never ran -- the "
              f"trigger did not succeed.\n\n  "
              + "\n  ".join(not_triggered)
              + "\n\nNothing was read back, so this says nothing either way about "
                "whether the write lands. What the trigger needs has to be up "
                "before this can answer -- that is a different repair from a row "
                "that is missing.",
              file=sys.stderr)
        return 1
    if failed:
        print(f"FAIL: {len(failed)} proof(s) ran and the truth owner disagreed.\n\n  "
              + "\n  ".join(failed)
              + "\n\nThe trigger succeeded and the row is not there. That is the "
                "gap between shipping and working, which is what this asks about.",
              file=sys.stderr)
        return 1
    agreed = [r for r in results if r["ok"] is True]
    if unanswerable and not agreed:
        print("could not ask the truth owner for any declared proof:\n  "
              + "\n  ".join(unanswerable))
        return 4
    if unanswerable:
        # One proof agreeing and two never asked used to print "1 proof(s)
        # triggered, and the truth owner agreed" and exit 0, naming neither of
        # the two. That is the shape CLAUDE.md refuses in one line: a check that
        # could not read what it was supposed to read is not a green light, and
        # it reports the coverage it actually had. Still exit 4, not 1 -- a proof
        # that could not be asked says nothing about the code either way.
        print(f"{len(agreed)} of {len(results)} proof(s) triggered and the truth "
              f"owner agreed. {len(unanswerable)} could not be asked, so this "
              f"claim is not answered:\n  " + "\n  ".join(unanswerable))
        return 4
    print(f"{len(agreed)} proof(s) triggered, and the truth owner agreed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
