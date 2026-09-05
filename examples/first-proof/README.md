# First proof: green tests, untested change

Run from the vibeproof checkout:

```sh
python3 examples/first-proof/run.py
```

Requires **Git and Python 3.12+**. No packages, API keys, coding-agent subscription or network connection are needed after cloning.

The script creates a temporary git repository, executes the real checkers from this checkout, and removes the temporary repository when it finishes. It does not install the framework into your project. It disables git hooks and uses a temporary repository identity for its local commits.

## What you will see

The request is deliberately small: a 20-unit discount on a 100-unit cart should leave 80.

1. An unrelated arithmetic test passes even though the new implementation returns **120**.
2. The ordinary `test` checker returns **exit 1**: the suite never executed the changed Python file.
3. A new test actually calls the function and fails with **120 != 80**.
4. The code is repaired; the suite and the `test` checker pass.
5. The `review-finding` checker runs that regression test against the bad commit and the repaired tree. It verifies that the test fails before the repair, passes after it, and executes `total`.
6. Two negative controls expose the limits: importing a file is enough for the ordinary `test` checker; an unrelated test cannot prove the repair through `review-finding`.

The final line is `DEMO VERIFIED`. Expected FAIL output is part of a successful demonstration. If a checker unexpectedly passes or fails, the demo exits 1.

**This is an intentionally constructed example, not a captured AI conversation.** The command output is real. It demonstrates two checkers, not the complete task, engagement, hooks and ship workflow. `PASS` is not proof that arbitrary business requirements were met.

## Inspect or regenerate the evidence

```sh
python3 examples/first-proof/run.py --output /tmp/vibeproof-evidence
```

`demo.json` includes commands, observed exit codes, output, source examples and implementation hashes. `demo.txt` is the readable transcript. Temporary directory paths are replaced with `<demo>`; framework paths with `<vibeproof>`. Those tokens are display labels, not copy-and-paste shell commands.

Inspect [run.py](run.py) before running it. Verify the demonstration as part of the test suite:

```sh
python3 -m unittest tests.test_public_first_proof -v
```

## Try one real task next

Follow [the adoption guide](../../docs/GETTING_STARTED.md). Start with one small change in a repo you can restore. Full installation adds files and asks repo-specific questions; the short demo deliberately does neither.

If the demo fails on your machine, open a [demo issue](https://github.com/howardc38/vibeproof/issues/new?template=first-run.yml). Include the Python version, OS, command, and sanitized failure output. Do not include credentials or private source code.
