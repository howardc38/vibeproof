#!/usr/bin/env python3
"""Run the repo's declared test command.

One command, whole suite, no file-to-test mapping. Measured on the target repo:
4,418 tests in 72 seconds. Under that, a mapping saves nothing and adds a
failure mode -- map wrong, skip the test that mattered, pass anyway.

The command lives in .v4/config.json, which is on protected_paths precisely
because it is this claim's only oracle: `pytest -k nothing` would turn every
test claim green forever.
"""
import re
import argparse, json, subprocess, sys
from pathlib import Path
from typing import NoReturn

def main() -> NoReturn:
    """The program, as a function.

    A module-level script has no symbol a stack frame can be named after,
    so `review.resolve_symbol` refuses every `--symbol` for it and a
    finding raised with none has nothing for `redgreen` to trace -- which
    leaves a signature as the only exit, the outcome that function exists
    to prevent.

    `sys.exit` inside stays `sys.exit`: it raises, so it travels out
    through `main()` unchanged and the verdict is the one it always was.
    That is why the return is `NoReturn` and not `int`. The annotation said
    `int`, the last line was an unreachable `return 0`, and the caller was
    `sys.exit(main())` -- three statements about one function, two of them
    describing a value it has never once produced.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from kernel import config as config_mod  # noqa: E402

    p = argparse.ArgumentParser()
    p.add_argument("--subject", required=True); p.add_argument("--facts"); p.add_argument("--out")
    a = p.parse_args()
    try:
        s = json.loads(Path(a.subject).read_text())
        root = Path(s["repo_root"])
        cfg = json.loads((root / ".v4/config.json").read_text())
    except Exception as exc:
        print(f"cannot read config: {exc}", file=sys.stderr); sys.exit(5)

    # The kernel already holds a wall: `checkers.json` carries a `timeout_sec` per
    # checker and `runner.py` enforces it. A second one here made the smaller number
    # win invisibly -- the registry said 300, this said 1800, so a suite running
    # anywhere between them was killed by the kernel and recorded as CHECKER_ERROR,
    # and the worker saw a failure it did not cause and could not act on. One owner:
    # the registry. Register with `--timeout` if a suite needs longer.
    cmd = config_mod.declared(cfg, "test_command")
    if not cmd:
        # 4, not 5. A repo that has not said how to run its tests has not broken
        # this checker -- it has not answered it. Measured before this: the sentinel
        # `v4 init` writes went straight to the shell, `TODO exited 127`, and the
        # verdict was exit 1 -- which says this repo's tests fail, about a repo that
        # never said what its tests are.
        print("no `test_command` declared in .v4/config.json. It is the sole oracle "
              "for every `test` claim, so it is never guessed -- write the command "
              "that runs this repo's suite.")
        sys.exit(4)
    # Which files this change touched, decided *before* the suite runs. It used
    # to be computed after, from `r.returncode`, and the trace was then taken by
    # running the whole suite a second time under a tracing `sitecustomize` --
    # `lifecycle.EXPENSIVE_MS` records the measured median for `test` as 567s,
    # so a `v4 check` that reached the binding step paid about nineteen minutes
    # for one claim, and nothing in the output said the suite had run twice. The
    # trace and the verdict need the same run; splitting them is what doubled it.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from kernel.hashing import kernel_written
    base = s.get("diff_base") or ""
    changed = []
    if base:
        # `--diff-filter=d` drops deletions, and it is git that drops them
        # rather than a check further down: a file this change removed can
        # never be executed by any test, so "the suite reached none of the
        # changed files" is true of every delete and says nothing about any of
        # them. Measured on a real adopter: removing twelve trimmed checkers
        # turned this red with nineteen paths listed, all nineteen the files
        # that had just gone.
        #
        # The same shape was repaired in `secret_scan`, `fail_closed` and
        # `external_write` this week, where the fix was to tell a missing file
        # from a deleted one. Here there is nothing to tell apart -- a deleted
        # file is simply not a file the suite owes coverage of -- so it leaves
        # the list at the source.
        d = subprocess.run(["git", "diff", "--name-only", "--diff-filter=d",
                            base], cwd=root, capture_output=True, text=True)
        if d.returncode != 0:
            # Saying so, because the alternative is what this did: a non-zero
            # exit left `changed` empty, the tracer was never attached, and the
            # claim passed on the suite's exit code alone -- the binding half
            # switched itself off with nothing to say it had. An unreadable
            # diff is a fact about the diff, not a repo whose suite covers its
            # own change.
            print(f"the diff against {base} did not read (git exited "
                  f"{d.returncode}), so which files this task changed is "
                  f"unknown and the binding half of this claim is unanswered",
                  file=sys.stderr)
            if d.stderr.strip():
                print(f"  {d.stderr.strip()[:300]}", file=sys.stderr)
        else:
            # `kernel_written` with the repo root: a file whose bytes still match
            # what `v4 install` shipped is the framework's, not this task's, and
            # asking the suite to cover it is asking the wrong repo's tests.
            changed = [f for f in d.stdout.splitlines()
                       if f.endswith(".py") and not f.startswith("tests/")
                       and "fixtures" not in f and not kernel_written(f, root)]

    ran = None
    if changed:
        from kernel.redgreen import executed_files
        try:
            # No wall of its own. The comment forty lines up says the second
            # timeout was removed and that there is "One owner: the registry"
            # -- and this call was the second timeout. `.v4/checkers.json`
            # gives the `test` checker 900s and `runner` enforces it, so the
            # smaller number always won; a suite running between the two was
            # killed by the kernel and recorded as CHECKER_ERROR, a failure the
            # worker did not cause and cannot act on. One owner, for real now.
            rc, ran, combined = executed_files(root, cmd)
            r = subprocess.CompletedProcess(cmd, rc, combined, "")
        except Exception as exc:                                # noqa: BLE001
            # The tracer could not attach. That is not a verdict about the
            # suite, so the suite is run plainly and the binding half says it
            # could not be answered rather than answering it wrongly.
            print(f"the execution trace did not run ({type(exc).__name__}: "
                  f"{exc}), so the binding half of this claim is unanswered",
                  file=sys.stderr)
            ran = None
            r = subprocess.run(cmd, shell=True, cwd=root,
                               capture_output=True, text=True)
    else:
        r = subprocess.run(cmd, shell=True, cwd=root, capture_output=True, text=True)

    # A silent command with no semantic output is not proof, even when the exit
    # code is trusted. `/usr/bin/true` proves process success and nothing else, and
    # so does a runner that collected no tests: the ledger cannot tell either from a
    # suite that ran and passed. Bypass fixtures used all three shapes.
    out = (r.stdout or "") + (r.stderr or "")
    # The zero has to be a whole number, not the last digit of one. `"0 tests"`
    # was matched as a substring, so `Ran 1260 tests` contained it and this
    # repo's own suite -- 1,260 passing tests -- was reported as having run
    # nothing. Every count ending in a zero had the same fate: 10, 250, 1000.
    #
    # A word boundary alone is not enough either: `\b0 tests\b` still matches
    # inside `1260 tests`, because the boundary sits between `6` and `0` only
    # when the preceding character is not a word character. So the digit run is
    # matched whole and then compared to zero.
    COLLECTED_NOTHING = re.compile(
        r"collected\s+0\s+items?\b"
        r"|\bno\s+tests?\s+ran\b"
        r"|\bNO\s+TESTS\s+RAN\b"
        r"|(?<![\d.])0\s+tests?\b"
        r"|\bran\s+0\s+tests?\b",
        re.IGNORECASE)
    if r.returncode == 0:
        if not out.strip():
            print("the test command exited 0 and said nothing. A silent command is "
                  "not evidence -- `/usr/bin/true` proves process success, not that "
                  "anything was tested.", file=sys.stderr)
            sys.exit(1)
        if COLLECTED_NOTHING.search(out):
            print(f"the test command exited 0 having run nothing:\n{out.strip()[:400]}",
                  file=sys.stderr)
            sys.exit(1)
        # A runner says how many. `echo ok` contains "ok" and says nothing about
        # what ran -- a bypass fixture used exactly that, and the first version of
        # this check accepted it. What every runner emits is a count next to the
        # word, or an explicit per-test verdict.
        RAN = re.compile(r"\b\d+\s+(tests?|passed|items?|examples?)\b"
                         r"|\b(ran|collected)\s+\d+\b|\bPASSED\b|\bok\s+\d+",
                         re.I)
        if not RAN.search(out):
            print(f"the test command exited 0 without saying how much ran. A runner "
                  f"reports a count; this did not, so nothing distinguishes it from "
                  f"a command that tested nothing:\n{out.strip()[:400]}",
                  file=sys.stderr)
            sys.exit(1)

    # Binding. The suite passing says the suite works; it says nothing about this
    # change unless the change was executed. `review-finding` has refused a test
    # that never runs the symbol it claims to close since it was built -- this asks
    # the same of the suite, which nothing had.
    #
    # Only when a diff base exists and the diff touched Python. Reporting "nothing
    # ran" for a docs-only change would be a checker inventing a finding.
    REPORT_UNEXECUTED = []
    if changed and r.returncode == 0:
        if ran is not None:
            untouched = [f for f in changed if f not in ran]
            if len(untouched) == len(changed):
                print(f"the suite passed and executed none of the {len(changed)} "
                      f"changed file(s):\n  " + "\n  ".join(untouched[:8]) +
                      "\n\nA green suite that never reached the change proves the "
                      "suite works. It says nothing about this change.",
                      file=sys.stderr)
                sys.exit(1)
            if untouched:
                # Not a failure, and not nothing. Refusing here would fail a
                # config-only or docs-adjacent change that no test can reach, which
                # is a real shape. But the threshold was "none of them", so seven of
                # eight changed files going unexecuted read as a clean pass -- and
                # the trace that knows better was already being paid for.
                REPORT_UNEXECUTED.extend(untouched)

    #: What a runner prints when it reached the end and counted. If a failing run's
    #: tail has none of these it did not finish -- it died -- and twelve lines of
    #: progress dots read exactly like twelve lines of a suite that ran. Same output,
    #: two different facts, which is the shape this whole layer exists to remove.
    _COUNTED = ("passed", "failed", "error", "ok", "FAILED", "OK",
                "no tests ran", "deselected")
    _lines = (r.stdout or r.stderr).strip().splitlines()
    _n = 12
    if r.returncode != 0 and not any(m in ln for ln in _lines[-12:] for m in _COUNTED):
        _n = 80          # it died mid-run; the reason is above where the tail starts
    tail = _lines[-_n:]
    if tail:
        if _n > 12:
            print(f"`{cmd}` exited {r.returncode} without a run summary -- it did "
                  f"not finish. Last {min(_n, len(_lines))} line(s):\n")
        print("\n".join(tail))
    elif r.returncode != 0:
        # A command that fails without a word. Printing the empty tail left `v4
        # check` showing `test exit 1` and nothing else, so the one thing a reader
        # needs -- what to run to see it -- was the one thing missing.
        print(f"`{cmd}` exited {r.returncode} and produced no output. "
              f"Run it yourself to see why.")
    if a.out:
        Path(a.out).write_text(json.dumps({"command": cmd, "exit": r.returncode}, indent=2))
    if REPORT_UNEXECUTED and r.returncode == 0:
        print(f"\n{len(REPORT_UNEXECUTED)} changed file(s) the suite never executed:\n  "
              + "\n  ".join(REPORT_UNEXECUTED[:8])
              + ("" if len(REPORT_UNEXECUTED) <= 8
                 else f"\n  … {len(REPORT_UNEXECUTED) - 8} more")
              + "\n\nNot a failure: a config-only change is one no test can reach. "
                "Said anyway, because the threshold above is `none of them` -- seven "
                "of eight going unexecuted read as a clean pass, and the trace that "
                "knew better was already run.")

    sys.exit(0 if r.returncode == 0 else 1)


if __name__ == "__main__":
    # No `sys.exit(main())`: every path out of `main` is already a `sys.exit`,
    # so wrapping it described a returned code that never arrives.
    main()
