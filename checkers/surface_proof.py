#!/usr/bin/env python3
"""Did the surface this repo already tests still work?  PL-1.

An adopter had eight Playwright specs, a `test:e2e` script that runs them, and
`ui_globs` in its facts naming the directory they live in. `test_command` was
`pytest -q`, so V4 ran the Python suite and never saw one of them. Eight tests
somebody wrote and maintains, invisible to the thing whose whole job is asking
whether the change works.

This is not a new mechanism. The specs exist, the runner exists, somebody keeps
them green. The claim is that they ran and passed, and the exit code answers it.

Why a separate kind rather than folding it into `test`
-----------------------------------------------------
`test` is the repo's own suite and it is one claim: a repo has one. A surface
suite is a different command with a different runner, usually a different
language, and it is absent in most repos -- folding it in would mean `test`
returning 4 for half of every repo's answer and 0 for the other half, from one
exit code. Two questions, two claims.

Exit
----
  0  the surface command ran and passed
  1  it ran and failed
  4  no surface command declared, or the runner is not installed here -- the
     second is not a finding about this repo's code and must not read as one
  5  broke
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel import config as config_mod  # noqa: E402

KIND = "surface-proof"

#: What a repo calls the command that drives its own surface. Read from config
#: rather than guessed: guessing `npm test` would run a unit suite and report it
#: as a surface answer, which is the substitution this whole framework refuses.
CONFIG_KEY = "surface_command"


def _facts(root: Path) -> dict:
    # `config.facts_path_for` -- the only answer. This spelled it again, with
    # no preference for `facts.<repo>.json`, so a repo carrying a second table
    # is judged against another project's vocabulary.
    from kernel.config import facts_path_for
    p = facts_path_for(root)
    if p is None:
        return {}
    try:
        f = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return f if isinstance(f, dict) else {}



def surface_command(root: Path):
    """`(command, cwd)` or `(None, why)`."""
    try:
        cfg = json.loads((root / ".v4" / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"cannot read .v4/config.json: {exc}"
    # `declared` rather than `get`: `v4 init` writes the sentinel for a field
    # only this repo can answer, and a sentinel that reaches the shell is a
    # command-not-found reported as a failing surface.
    cmd = config_mod.declared(cfg, CONFIG_KEY)
    if not cmd:
        globs = _facts(root).get("ui_globs") or []
        hint = (f" This repo declares ui_globs ({', '.join(globs[:3])}), so it "
                f"has a surface; nothing says how to drive it." if globs else "")
        return None, (
            f"no {CONFIG_KEY!r} in .v4/config.json.{hint}\n\n"
            f"    \"{CONFIG_KEY}\": \"the command that drives this repo's own "
            f"surface\",\n"
            f"    \"surface_cwd\":     \"where to run it from, if not the "
            f"repo root\"\n\n"
            f"It is not guessed. A repo's unit-test command usually exits 0 "
            f"without touching the surface at all, so guessing one would hand "
            f"out a green claim about something that never ran. This is the "
            f"suite you already maintain -- name it.")
    return cmd, cfg.get("surface_cwd") or "."


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--subject", required=True)
    p.add_argument("--facts")
    p.add_argument("--out")
    a = p.parse_args()
    try:
        s = json.loads(Path(a.subject).read_text(encoding="utf-8"))
        root = Path(s["repo_root"]).resolve()
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr)
        return 5

    cmd, where = surface_command(root)
    if cmd is None:
        print(where)
        return 4

    # There was a list of four spellings of "do nothing" here -- `true`, `:`,
    # `exit 0`, `/bin/true` -- and `/usr/bin/true`, `bash -c true`, `sh -c :`
    # and `python3 -c pass` all walked through it into a permanently green
    # surface claim. SPEC.md §10 records the same shape being reversed in
    # `control-plane-budget`: "all whitelists end up dying this way".
    #
    # The rule that catches every one of them is already below and is about
    # what the command *did* rather than how it was spelled: exit 0 having
    # printed nothing is what a wrapper round nothing looks like from outside,
    # and it is the only thing this checker can see without knowing the runner.
    cwd = (root / where).resolve()
    if not cwd.is_dir():
        print(f"surface_cwd {where!r} is not a directory here", file=sys.stderr)
        return 5

    # A missing runner is not a failing surface. Measured elsewhere in this
    # project: reading "command not found" as red hands out half a proof for
    # free, and the half it hands out is the half that matters.
    parts = cmd.split()
    if not parts:
        print(f"{CONFIG_KEY} is set and empty", file=sys.stderr)
        return 1
    exe = parts[0]
    # Asked of a shell, because the command is run by one. `shutil.which` reads
    # PATH and knows nothing about builtins, so `surface_command: ":"` came
    # back as "not installed here" -- exit 4, the answer that means *this repo
    # cannot be judged* -- for a command that runs fine and does nothing. That
    # is the same green the no-op list below used to catch, arriving through
    # the other door. `command -v` is the shell's own answer to the question
    # and needs no list of what a shell can run.
    if subprocess.run(["/bin/sh", "-c", f"command -v {shlex.quote(exe)}"],
                      capture_output=True).returncode != 0:
        print(f"{exe!r} is not installed here, so this cannot be answered. "
              f"That is not a finding about this repo -- install it, or run "
              f"this where it is installed.")
        return 4

    # No timeout here. `checkers.json` carries `timeout_sec` for this checker and
    # `runner.py` enforces it; a second wall in the checker made the smaller
    # number win invisibly, and the reader got exit 8 with `checker exceeded Ns`
    # instead of the message written here. `checkers/test.py` states the rule and
    # this was written the day after, with the same defect. One owner: register
    # with `--timeout` if a surface suite needs longer.
    try:
        r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                           text=True)
    except Exception as exc:                                    # noqa: BLE001
        print(f"could not run `{cmd}`: {exc}", file=sys.stderr)
        return 5

    tail = (r.stdout or r.stderr or "").strip().splitlines()[-20:]
    if a.out:
        Path(a.out).write_text(json.dumps(
            {"command": cmd, "cwd": str(cwd), "exit": r.returncode,
             "tail": tail}, indent=2))
    #: A suite that ran prints something. Silence plus exit 0 is what a wrapper
    #: round nothing looks like, and it is the one shape this checker can see
    #: from outside without knowing the runner.
    if r.returncode == 0 and not (r.stdout or r.stderr).strip():
        print(f"`{cmd}` exited 0 and printed nothing. A surface suite that ran "
              f"says so; this says only that a process started and stopped.",
              file=sys.stderr)
        return 1

    if r.returncode != 0:
        # A surface suite needs the surface running, and this checker cannot
        # tell a dead server from a broken page: both come back as a failing
        # suite. Telling them apart would mean matching a connection error --
        # `ECONNREFUSED`, `net::ERR_CONNECTION_REFUSED`, whatever the runner
        # this repo chose happens to say -- which is that runner's vocabulary
        # hardcoded into a framework that does not know which one it is.
        #
        # So it is a sentence, not a branch. Measured on the reference adopter
        # the first time this ran for real: the app had stopped, 30 of 30 specs
        # failed in 8.5s, and a green run of the same suite takes 12.6s. Whoever
        # was driving knew to restart it and was green ninety seconds later. An
        # agent reading "MediaUpload page renders dropzone -- failed" goes and
        # reads the dropzone, and that is the hour this line is for.
        many = len([t for t in tail if t.strip()])
        print(f"`{cmd}` exited {r.returncode} in {where}:\n  "
              + "\n  ".join(tail)
              + (f"\n\n{many} line(s) of failure. If that is most of the suite, "
                 f"check the surface is actually up before reading any of it -- "
                 f"this checker runs a command and reads an exit code, so an app "
                 f"that is not listening and a page that is broken are the same "
                 f"answer to it." if many >= 5 else ""),
              file=sys.stderr)
        return 1
    print(f"`{cmd}` passed in {where}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
