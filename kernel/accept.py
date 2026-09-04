"""Would a fresh clone of this tree still hold?  `v4 accept`.

Three questions get asked before a batch of work is believed, and until this
existed all three were asked by hand:

    the declared test command passes
    every registered checker still holds against its own fixtures
    every registered detector still does

`7d8f9c0` -- the commit that closed 195 findings -- ran exactly those, and
recorded why: *"Before any of it: the measuring instrument was broken. The CI
`Tests` step was red in all 6 runs that existed and the registration gate failed
on 2 of 31 checkers **in a clone**, so nothing could have been shown to be
fixed."*  Two of thirty-one only failed in a clone, which is the whole argument
for the archive: `.git/v4/` is not cloned, so anything that holds only because
of local state fails here and nowhere else.

And a fourth, which is what made this worth writing rather than scripting once:
the document-against-code checkers. Measured on the day this was written -- 1,285
tests green locally, pushed, and CI red on `spec-coverage`, because two new
commands existed and `docs/SPEC.md` never mentioned them. **Running the test
suite is not running the gates.**
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from . import config as config_mod
from . import layout

#: The checkers that ask a whole-tree question rather than a delta one.
#:
#: A list, and it was measured rather than assumed. Running *every* repo-scoped
#: checker against a fresh archive gives two false failures: `secret-chain`
#: reports nine handlers in `tests/fixtures/**` -- files broken on purpose --
#: and `sweep-current` and `request-coverage` cannot answer at all without a
#: ledger. Those are delta checkers being handed no delta, not defects.
#:
#: The same four `.github/workflows/v4.yml` names, and it names them by calling
#: this: two lists of the same four is the shape this project keeps removing.
DOC_CHECKERS = ("design-pins", "spec-coverage", "registry-consistency",
                "dead-wiring")


def archive(root: Path, into: Path) -> Path:
    """The staged tree, unpacked into a directory named after the repo.

    The name matters and cost a measurement to learn: `doctrine.render` titles
    the generated `CLAUDE.md` after the directory, and `.v4/facts.<repo>.json`
    is found by it. Unpacked into `tmp/acc`, `registry-consistency` failed with
    "CLAUDE.md is not what `v4 doctrine` generates" -- a true statement about a
    directory with the wrong name, and nothing about the tree.
    """
    dst = into / layout.repo_name(root)
    dst.mkdir(parents=True)
    # The index, not HEAD. `git write-tree` is what `7d8f9c0`'s acceptance used
    # and the reason is the whole point of running this before a commit: with
    # `HEAD` the work being accepted is the work already committed, so a staged
    # change is invisible. Found by breaking it -- a new `.claude/commands/*.md`
    # was staged, `accept --docs` passed, and the same file failed CI.
    #
    # Still not what is only in the editor. `git add -A` first, which is the
    # step before a commit anyway.
    tree = subprocess.run(["git", "write-tree"], cwd=root, capture_output=True,
                          text=True, check=True).stdout.strip()
    tar = subprocess.run(["git", "archive", tree], cwd=root,
                         capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(dst)], input=tar.stdout, check=True)
    for args in (["init", "-q"], ["add", "-A"],
                 ["-c", "user.email=accept@v4", "-c", "user.name=accept",
                  "commit", "-qm", "the tree under test"]):
        subprocess.run(["git", *args], cwd=dst, check=True,
                       stdout=subprocess.DEVNULL)
    _history(root, dst)
    return dst


def _history(root: Path, dst: Path):
    """Give the tree under test the history a clone would have carried.

    "No local state" was implemented as "no history", and those are different
    things. `.git/v4/ledger.db` is untracked and does not travel, which is the
    whole argument for this archive; the object store does travel, and every
    real clone of this repo can resolve every commit in it.

    What the difference cost, measured: `.v4/lenses/near-miss.json` cites two
    commits as its evidence and `tests/test_a_lens_reaches_the_reviewer.py`
    resolves them with `git cat-file`. A tree holding one synthetic commit
    cannot, so from `f608135` -- the commit that added that lens -- `v4 accept`
    reported 6/7 with `tests` red, and reported it about a property of this
    function rather than about the tree being accepted. The same question was
    red in CI at the same moment for the same reason from the other side:
    `actions/checkout` defaults to `fetch-depth: 1`.

    HEAD is untouched: the fetch lands under `refs/accepted/`, so what is being
    accepted is still the single commit built from the index above. This only
    adds objects that were already reachable in `root`, which is what a clone
    gets -- measured on this repo, the fetch alone is 3.2s for an 8 MB pack and
    `archive` end to end went from 1.9s to 8.0s.

    `check=True`: a tree that silently lacks the history is the state this
    exists to end, and it would come back as a test failure naming a lens.

    `resolve()` because this runs with `cwd=dst` while every other git call in
    `archive` runs with `cwd=root`. Caught by
    `tests/test_the_ground_ci_stands_on.py`: `archive(Path("."), ...)` fetched
    `.` relative to `dst`, so the tree fetched from itself, reported success,
    and resolved nothing.
    """
    subprocess.run(["git", "fetch", "--quiet", "--no-write-fetch-head",
                    "--tags", str(Path(root).resolve()),
                    "+refs/heads/*:refs/accepted/*"],
                   cwd=dst, check=True, stdout=subprocess.DEVNULL)


def _run(argv, cwd, env, timeout=1800):
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=timeout)


#: Lines of the tail quoted in the row, and the width of each. Both are the
#: size of a terminal row and nothing else; what a reader actually needs on a
#: failure is the file, and the file holds everything.
_TAIL_LINES = 3
_TAIL_WIDTH = 160


def _test_detail(cmd, code: int, out: str) -> str:
    """The `tests` row: the tail, plus where the rest of it went.

    It was `splitlines()[-1:]`, and the last line of a failing run is a count.
    `FAILED (failures=2, skipped=1)` says how many and never which, and the
    tree it ran in is a `TemporaryDirectory` that `cmd_accept` has already
    removed by the time anybody reads the row -- so recovering the names meant
    rebuilding the archive by hand and running the suite again. Measured
    2026-08-27: two sessions hit this on the same day and one never recovered
    them, which is how a gate that did fire ends up costing what a gate that
    did not would have.

    Nothing here reads the shape of the output. `test_command` is declared by
    the repo -- unittest here, pytest, `go test`, `cargo test` elsewhere -- so
    a rule that knew where the names are would be a rule about this repo
    written into the framework. The whole of it is kept instead, outside the
    tree so it outlives the run, and the path is named where the count used to
    be.

    Only on failure: a passing run has nothing anybody opens, and a file per
    green `v4 accept` is litter that teaches people to ignore the line.
    """
    lines = out.strip().splitlines()
    if code == 0:
        return lines[-1][:_TAIL_WIDTH] if lines else str(cmd)
    fd, path = tempfile.mkstemp(prefix="v4-accept-tests-", suffix=".log")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(out)
    tail = " | ".join(ln.strip() for ln in lines[-_TAIL_LINES:] if ln.strip())
    return f"{tail[:_TAIL_WIDTH]}  -- whole output: {path}"


def run(root: Path, *, tests=True, fixtures=True, docs=True, on_step=None):
    """[(step, ok, detail)] -- what held and what did not.

    `on_step` is called with each row as it lands, so a run that takes eight
    minutes says something in the meantime.
    """
    root = Path(root).resolve()
    cfg = config_mod.RepoConfig(root)
    # The framework, not only the tree under test. Every `checkers/*.py`
    # imports `kernel`, and an adopter has no `kernel/` of its own: `bin/v4`
    # puts the framework's on PYTHONPATH, and this line replaced it with the
    # adopter root, so every checker died on import. Measured on a three-file
    # adopter whose checkers had just passed their fixtures under `install`:
    # `accept --here --fixtures` printed 0/16 registrable in 0.4 s, and
    # `accept --here --docs` a traceback from `design_pins`. The framework is
    # wherever this module was imported from.
    framework = Path(__file__).resolve().parent.parent
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(
        dict.fromkeys([str(root), str(framework)])))
    out = []

    def say(step, ok, detail):
        out.append((step, ok, detail))
        if on_step:
            on_step(step, ok, detail)

    if tests:
        cmd = cfg.config.get("test_command")
        r = _run(cmd, root, env) if isinstance(cmd, list) else \
            subprocess.run(cmd, cwd=root, env=env, shell=True,
                           capture_output=True, text=True, timeout=3600)
        say("tests", r.returncode == 0,
            _test_detail(cmd, r.returncode, (r.stdout or "") + (r.stderr or "")))

    if fixtures:
        reg = json.loads((root / config_mod.CHECKERS).read_text())
        reg = reg.get("checkers", reg)
        bad = []
        for cid, entry in sorted(reg.items()):
            r = _run([sys.executable, "-m", "kernel.cli", "--repo", ".",
                      "verify", "--checker", entry["path"],
                      "--fixtures", entry["fixtures"],
                      "--kind", entry["kinds"][0]], root, env)
            if r.returncode:
                bad.append(cid)
        say("checkers", not bad,
            f"{len(reg) - len(bad)}/{len(reg)} registrable"
            + (f" -- {', '.join(bad)}" if bad else ""))

        det = json.loads((root / config_mod.DETECTORS).read_text())
        det = det.get("detectors", det)
        bad = []
        for name, entry in sorted(det.items()):
            r = _run([sys.executable, "-m", "kernel.cli", "--repo", ".",
                      "verify-detector", "--detector", entry["path"],
                      "--fixtures", entry["fixtures"]], root, env)
            if r.returncode:
                bad.append(name)
        say("detectors", not bad,
            f"{len(det) - len(bad)}/{len(det)} usable"
            + (f" -- {', '.join(bad)}" if bad else ""))

    if docs:
        reg = json.loads((root / config_mod.CHECKERS).read_text())
        reg = reg.get("checkers", reg)
        for cid in DOC_CHECKERS:
            entry = reg.get(cid)
            if not entry:
                say(cid, True, "not registered in this repo")
                continue
            # `subject_refs` is deliberately empty, and the reason moved here
            # with the list. Naming `docs/SPEC.md` was an allowlist, and it did
            # what allowlists do: a pin written into any other document was
            # never run, and `RATIONALE.md` sat on a pin naming a superseded
            # chain scheme. Empty means repo-scoped -- every tracked markdown
            # file that carries a pin.
            subj = {"claim_id": "accept", "claim_kind": cid, "task_id": "accept",
                    "repo_root": str(root), "diff_base": "HEAD",
                    "subject_refs": [], "symbol": "", "variant": "", "params": {}}
            with tempfile.TemporaryDirectory() as td:
                sp = Path(td) / "subject.json"
                sp.write_text(json.dumps(subj))
                r = _run([sys.executable, entry["path"], "--subject", str(sp),
                          "--out", str(Path(td) / "out.json")], root, env)
            first = ((r.stdout or "") + (r.stderr or "")).strip().splitlines()
            # 4 is UNSUPPORTED -- the checker saying it cannot answer here, which
            # is an answer and not a failure.
            say(cid, r.returncode in (0, 4), first[0][:160] if first else "")
    return out
