#!/usr/bin/env python3
"""Run the suite and refuse an environment skip.

The predecessor named this trap and built a command for it, whose manifest
entry says only "without silent skips". This project walked into it: the facts
drift checks say in their own documentation that they "should run in CI, not by
hand", and they raise SkipTest when the reference repo is absent. CI is a fresh
clone. So every one of them is skipped, the job is green, and the green means
the checks did not run.

A skip for a reason inside the code -- a feature flag off, a case that does not
apply -- is fine and stays quiet. A skip because the environment is missing
something is the suite reporting on a smaller world than it was asked about,
and that is the one that has to be loud.

Exit 0 clean | 1 something was skipped for an environment reason.

Three things kept this at exit 1 on every CI run that exists, and all three
were one-machine assumptions rather than failures of the code under test:

* ``tests/test_facts.py`` pinned an adopter checkout to an absolute path and
  raised ``SkipTest`` anywhere else -- and that reason matches ``ENVIRONMENT``
  below, so the suite reported failure for a question it had decided not to
  ask. It reads ``V4_ADOPTER_REPO`` now, and says so.
* ``tests/test_kernel.py`` shelled out to ``derive --task t-eng`` against this
  repo's own checkout, which a clone does not have -- and every local run
  appended to the real ledger. It builds a throwaway repo.
* ``tests/test_dependency_audit.py`` read a fixture's ignored state from
  whatever git config the machine had. The case is built by
  ``kernel/register._Case`` now, the same way the gate builds it.

A gate that has never gone green is one nobody can tell apart from a broken
one, which is the argument this file makes about skips.
"""

import re
import sys
import unittest
from pathlib import Path

#: Reasons that mean "the world was smaller than the question".
ENVIRONMENT = re.compile(
    r"not present|not available|absent|missing|no such|not installed|"
    r"unavailable|requires? .*(repo|network|binary|tool)", re.I)

#: A sweep proposed widening the pattern above to catch three more reasons this
#: suite produces -- an adopter commit that is gone, a checkout whose name no
#: facts table matches, and `V4_ADOPTER_REPO` unset. It was tried and reverted,
#: because this repo had already decided and written the decision down:
#: `tests/test_which_repo_which_task_which_environment.py::
#: test_and_the_reason_stays_out_of_the_environment_class` pins it, and its
#: docstring gives the argument -- "unset is a decision, and matching would make
#: the sole oracle for every `test` claim permanently red for a question CI can
#: never put".
#:
#: The split is not loud-versus-quiet, it is *chose not to ask* versus *could
#: not ask*. Anything in the first class that reaches `env_skips` fails the
#: suite forever, in every environment, for a question nobody has withheld.


class LoudSkips(unittest.TextTestResult):
    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        if ENVIRONMENT.search(reason or ""):
            self.env_skips.append((str(test), reason))
        else:
            # The other half of the sentence this file's docstring makes. A
            # skip for a reason inside the code "is fine and stays quiet" --
            # and quiet turned out to mean invisible. `unittest` prints
            # `OK (skipped=1)`, this runner printed nothing at all, and the
            # exit code is 0, so a reader has the number and not the question.
            #
            # Measured, and it is the reason this branch exists:
            # `tests/test_facts.py::AgainstAdopterTable` opts out unless
            # `V4_ADOPTER_REPO` names a checkout, and no environment that runs
            # this suite sets it -- not `.github/workflows/v4.yml`, not
            # `.claude/settings.json`, not `.v4/config.json`. The workflow's
            # own comment excludes the adopter's table from the facts
            # step because it "is verified by tests/test_facts.py where that
            # repo exists", and where that repo exists is nowhere. 153 rows,
            # verified in no environment, and the suite said `OK`.
            #
            # Still exit 0: a check this environment cannot perform is a gate
            # that does not run here, and turning that into red would make the
            # sole oracle for every `test` claim permanently red for a question
            # CI can never put. What changes is that it is on the screen.
            self.declared_skips.append((str(test), reason))


def main():
    root = Path(__file__).resolve().parent
    sys.path.insert(0, str(root.parent))
    suite = unittest.defaultTestLoader.discover(str(root))

    LoudSkips.env_skips = []
    LoudSkips.declared_skips = []
    runner = unittest.TextTestRunner(resultclass=LoudSkips, verbosity=1)
    result = runner.run(suite)

    if LoudSkips.declared_skips:
        print(f"\n{len(LoudSkips.declared_skips)} test(s) did not run here, "
              f"and each says what that leaves unchecked:\n")
        for name, why in LoudSkips.declared_skips:
            print(f"  {name}\n    {why}")
        print("\nThis is not a failure and it is not nothing. A skip whose "
              "reason is a decision is allowed to pass; it is not allowed to "
              "be invisible, because `OK (skipped=N)` reads exactly like a "
              "suite that ran everything.")

    if LoudSkips.env_skips:
        print(f"\nFAIL: {len(LoudSkips.env_skips)} test(s) skipped because "
              f"something was not there.\n")
        for name, why in LoudSkips.env_skips:
            print(f"  {name}\n    {why}")
        print("\nA suite that skips what it cannot reach and reports green is "
              "reporting on a smaller world than it was asked about. Either "
              "make the input reachable in this environment, or say plainly "
              "that the check does not run here -- do not do both silently.")
        return 1
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
