# First proof: green tests, verified repair, expired evidence

Run from the vibeproof checkout:

```sh
python3 examples/first-proof/run.py
```

Requires **Git and Python 3.12+** on macOS or Linux. Native Windows is not validated. No packages, API keys, coding-agent subscription or network connection are needed after cloning.

The script creates a temporary git repository, executes the real checkers and kernel state queries from this checkout, and removes the temporary repository when it finishes. It does not install the framework into your project. It disables git hooks and uses a temporary repository identity for its local commits.

## What you will see

The request is deliberately small: a 20-unit discount on a 100-unit cart should leave 80.

1. An unrelated arithmetic test passes even though the new implementation returns **120**.
2. The ordinary `test` checker returns **exit 1**: the suite never executed the changed Python file.
3. A new test actually calls the function and fails with **120 != 80**.
4. The code is repaired; the suite and the `test` checker pass.
5. The `review-finding` checker runs that regression test against the bad commit and the repaired tree. It verifies that the test fails before the repair, passes after it, and executes `total`.
6. The kernel executes and records a real review checker attempt: the finding becomes `ANSWERED`.
7. The function changes again. A read-only state query returns `STALE` with `its subject moved: checkout.py`. The original attempt remains; no new test run is invented. Restoring the exact checked source returns to `ANSWERED`.
8. Two negative controls expose the limits: importing a file is enough for the ordinary `test` checker; an unrelated test cannot prove the repair through `review-finding`.

The final line is `DEMO VERIFIED`. Expected FAIL output is part of a successful demonstration. If a checker unexpectedly passes or fails, the demo exits 1.

**This is an intentionally constructed example, not a captured AI conversation.** The command output is real. It demonstrates two checkers plus a review claim, its engagement and evidence lifetime in a temporary ledger. It does not demonstrate full installation, host hooks or a ship decision. `proof_state.py` copies the checker and its executable surface dependencies so the shipped registration pins still match; it does not re-register or weaken the checker. `PASS` is not proof that arbitrary business requirements were met.

## Inspect or regenerate the evidence

```sh
python3 examples/first-proof/run.py --output /tmp/vibeproof-evidence
```

`demo.json` includes commands, observed exit codes, output, source examples and implementation hashes. The evidence lifetime record includes the source hashes, observed states and attempt counts before and after the edit. `demo.txt` is the readable transcript. Temporary directory paths are replaced with `<demo>`; framework paths with `<vibeproof>`. Those tokens are display labels, not copy-and-paste shell commands.

Inspect [run.py](run.py) and the [kernel-state adapter](proof_state.py) before running it. Verify the demonstration as part of the test suite:

```sh
python3 -m unittest discover -s tests -p test_public_first_proof.py -v
```

## Try one real task next

Follow [the adoption guide](../../docs/GETTING_STARTED.md). Start with one small change in a repo you can restore. Full installation adds files and asks repo-specific questions; the short demo deliberately does neither.

If the demo fails on your machine, open a [demo issue](https://github.com/howardc38/vibeproof/issues/new?template=first-run.yml). Include the Python version, OS, command, and sanitized failure output. Do not include credentials or private source code.
