"""How a detector is run and how its output is read.  SPEC.md §2.

Everything here was inside `kernel/derive.py`, and `kernel/register.py` reached
into that module to get it -- while `derive` reached back for the one constant
that says which detectors are unconditional. Two modules importing each other
is not a style complaint: it is `register.py` line 522 doing its import inside a
function body because doing it at the top raises ImportError, in three separate
places, with nothing saying why. `lint` reported it as LINT-IMPORT-CYCLE the
first time anyone touched the file, which is a trap rather than a rule -- the
person who trips it did not put it there.

The cycle existed because these three things are neither derivation nor
registration. They are the contract a detector is written against: run it this
way, name it this way, read it back this way. Both callers need the contract,
neither needs the other, and a contract with no dependents of its own can sit
under both.
"""

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

CLAIM_LINE = re.compile(
    r"^V4-CLAIM:\s*(?P<fields>.+)$", re.M
)
FIELD = re.compile(r"(\w+)=(?:\"([^\"]*)\"|(\S+))")

ALLOWED_FIELDS = {"kind", "file", "symbol", "variant", "line", "note"}

#: Detectors whose output does not depend on the tree, so "should not fire" is
#: not expressible for them.  SPEC.md §2 argues this: they emit one fixed line
#: and their behaviour is decided by `claim_kinds.json`, so the gate for that
#: kind belongs on its checker.  Named by prefix because that is what the
#: convention already is; every other detector must be registered.
UNCONDITIONAL = "always_"


class DeriveError(RuntimeError):
    pass


def parse_claim_lines(text):
    """Read `V4-CLAIM:` lines.  Unknown fields are refused, not ignored.

    SPEC.md §2: everything that decides which checker judges a claim, or
    which bytes it is checked against, has to be an enum or a path the kernel
    validates. Free text lives in `note` and touches neither.
    """
    out = []
    for m in CLAIM_LINE.finditer(text):
        fields = {}
        for name, quoted, bare in FIELD.findall(m.group("fields")):
            fields[name] = quoted if quoted else bare
        unknown = set(fields) - ALLOWED_FIELDS
        if unknown:
            raise DeriveError(
                f"V4-CLAIM carries fields the kernel does not accept: "
                f"{sorted(unknown)}. Anything outside {sorted(ALLOWED_FIELDS)} would "
                f"let the emitter decide something the kernel is supposed to."
            )
        if "kind" not in fields:
            raise DeriveError(f"V4-CLAIM without a kind: {m.group(0)!r}")
        out.append(fields)
    return out


def run_detector(repo_root: Path, detector_path: Path, subject_files, facts=None,
                 timeout=300, diff_base="", params=None):
    """Detectors take the same shape as checkers -- one mechanism, two uses.

    `diff_base` used to be the empty string here, unconditionally, and three
    detectors read it: `test_weakened`, `test_expectation`, `signature_change`.
    All three fall back to `HEAD`, so they saw uncommitted work and nothing
    else -- a worker who weakens a test and commits it was invisible to the
    detector whose whole subject is that. Measured on the reference adopter:
    `test-weakened` raised 0 claims across 36 tasks.

    `params` was `{}` for the same reason and cost the same way. A checker's
    subject carries `derive_exclude` there and a detector's did not, which was
    invisible while the kernel pre-filtered `subject_files` -- and stopped being
    invisible the moment a detector swept the whole repo instead. `test_shape`
    then reported 17 findings inside `tests/fixtures/`, every one of them a red
    fixture doing its job.

    It was wrong in both callers and differently. `derive` had the task's
    `base_commit` and did not pass it. `verify_detector` builds a parent commit
    from `parent_content` -- support added there specifically so that "did this
    change weaken something" could be gated -- and then handed over a subject
    with no base, so every red case of such a detector emitted nothing and the
    gate could not have failed. `verify_checker` had passed `HEAD` for its
    half the whole time.
    """
    # `runner.Subject`, the same constructor the checker half uses. This was the
    # third hand-typed copy of that payload, and the two failures above are what
    # three copies of one shape cost: a field added to the contract reaches
    # whichever of them the author was looking at.
    #
    # `params` stays a pass-through rather than going through
    # `runner.subject_params`, and that is the deliberate half of the
    # divergence: `derive` hands over the repo's `derive_exclude`, while the
    # fixture harnesses in `register` hand over nothing, because a red fixture
    # *is* the broken code and excluding it would make every red case pass
    # (`subject_files.exclusions` states this).
    from . import runner
    payload = runner.Subject(
        repo_root=str(repo_root), diff_base=diff_base,
        subject_refs=tuple({"kind": "file", "path": p} for p in subject_files),
        params=params or {},
    ).as_dict()
    with tempfile.TemporaryDirectory() as td:
        subj = Path(td) / "subject.json"
        subj.write_text(json.dumps(payload))
        argv = [sys.executable, str(detector_path), "--subject", str(subj)]
        if facts is not None:
            f = Path(td) / "facts.json"
            f.write_text(json.dumps(facts))
            argv += ["--facts", str(f)]
        out_path = Path(td) / "out.json"
        argv += ["--out", str(out_path)]
        # `runner._run_contained`, not `subprocess.run`. `register.
        # probe_repo_subject` already called this out as one-rule-two-
        # implementations and fixed the install path; this is the third site,
        # on the path every `derive` takes for all 30 detectors. Both failures
        # it was written for apply here: `capture_output` waits on the pipe
        # rather than the process, so a detector that leaves a child holding
        # stdout hangs forever, and a bare `timeout=` kills the direct child
        # only. It also turns a timeout into a recorded exit code instead of a
        # `TimeoutExpired` raised through `derive.derive` and `lifecycle` to
        # `cli.main`, whose handler catches three exception types and not that
        # one -- so one slow detector ended `v4 derive` in a traceback.
        code, stdout, stderr, survivors = runner._run_contained(
            argv, repo_root, timeout, td)
        if survivors:
            stderr += (f"\n[kernel] this detector exited and left {survivors} "
                       f"process(es) running in its group")
        # 14 detectors write JSON here and the file was never read before the
        # temp directory was torn down. `runner.record` keeps the checker
        # equivalent as a `checker_out` event, with a comment saying the
        # alternative is "the kernel parsed this, held it in memory, and
        # dropped it"; here it was not even parsed. Returned rather than
        # stored, so the caller that owns the ledger decides.
        payload_out = None
        if out_path.is_file():
            try:
                payload_out = json.loads(out_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                stderr += f"\n[kernel] --out was written and does not parse: {exc}"
    return code, stdout, stderr, payload_out
