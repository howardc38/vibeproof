# Checks, evidence and limits

This is the technical companion to the short README. For the operating loop see [USING.md](USING.md); for installation see [GETTING_STARTED.md](GETTING_STARTED.md).

## What happens in a task

1. `task` records the request, allowed files and starting commit.
2. `derive` runs detectors, which raise questions about the code.
3. Some question kinds require an engagement sentence before their checker runs.
4. `check` runs checkers and stores their actual results.
5. Results expire when their relevant inputs change.
6. `ship` re-derives, checks the configured blocking predicate and reports remaining issues.

The normal CLI is still named `v4`. The repository and product name are vibeproof.

## Exit codes

| Exit | Meaning |
|---:|---|
| 0 | PASS for the check that ran |
| 1 | FAIL; a finding remains |
| 4 | UNSUPPORTED; the checker could not answer, not a pass |
| 5 | ERROR |
| 6 | CHECKER_TAMPERED; registered checker bytes do not match |
| 7 | SUBJECT_MOVED during the check |
| 8 | TIMEOUT |

See [runner.py](../kernel/runner.py) and [state.py](../kernel/state.py) for the actual state mapping.

## Which checks hold ship

The current registry gives immediate blocking status to `test`, `scope`, `secret`, `fail-closed`, `external-write`, `review-finding`, `runtime-proof` and `surface-proof`. Other registered kinds report initially; age, count and repeated failure thresholds can make them block later. Accepted risk follows the configured policy.

In particular, **test deletion detection is not an unconditional ship blocker by default**. See [the registry](../.v4/claim_kinds.json) and [the grouping predicate](../kernel/state.py).

## Language and runner support

| Mechanism | Current implementation |
|---|---|
| Ordinary suite command | Runs the configured command and checks exit status and recognizable output |
| Did the suite reach changed files? | Python `.py` files; excludes designated test/fixture/framework files. It fails when none were reached, not when any one was missed |
| Repair test fails before, passes after and executes the target | Python tracing, Go coverage, and Node/V8 coverage paths exist. Availability depends on language, runner and source attribution |
| Python structural analysis | Standard-library AST |
| Go structural analysis | Go AST helper; requires a working Go toolchain |
| TS/JS structural analysis | Masked scanners; not a complete compiler or type checker |
| Rust | Limited scanning support; do not assume parity with Python |

The earlier README said the Go/Node tracing code was unreachable from the review checker. That was out of date: [review_finding.py](../checkers/review_finding.py) now dispatches on `redgreen.traceable`, and [redgreen.py](../kernel/redgreen.py) includes Go and Node paths. This does not establish compatibility with every runner. A plain Node example has been exercised; each target runner still needs verification.

## What a green result does not establish

- Importing a Python file is enough to mark the file as executed for the ordinary suite check. It does not prove that the changed function, branch or requirement was tested.
- The test-weakening check compares live-test counts with the base. Removing one meaningful test and replacing it with an unrelated nonempty test can preserve the count.
- Source-pattern checks can miss wrappers and cross-file behavior. They can also report a pattern that is safe for reasons outside the file.
- Runtime proof needs your real trigger, truth query and expected result. Surface proof runs the UI suite you configured; it does not invent comprehensive UI tests.
- A review finding can have a text-based closure. That proves text changed, not behavior.
- An engagement sentence passes mechanical checks; its acceptance is not proof of understanding.
- Prompts requesting independent or blind reviews are not proof that reviewers operated independently.

## Hooks and accepted risk

The shipped hook configuration targets Claude Code. Other tools can invoke the CLI, but this repository does not ship equivalent native hook adapters for them.

Write/Edit hooks examine supported tool payloads and the declared scope. The shell guard examines command text. Shell scripts and other execution routes are not a security boundary. Hooks stand down when required state cannot be read. The stop hook blocks once and permits the next stop.

The ledger uses SQLite triggers and hash chains to make ordinary writes controlled and recorded. It is not an immutable external trust service. The `task` table is not covered by the same chain as attempts and events. A process with sufficient file access can tamper with local state; do not market the ledger as impossible to edit.

The risk command permits an agent-signing path, including `--no-tty-check`. A stored name is not independently verified human approval. A risk signature must meet the kernel's acceptance conditions and expires against the inputs it covers. Read [risk.py](../kernel/risk.py) for attribution and [lifecycle.py](../kernel/lifecycle.py) for what ship actually accepts.

## Evidence you can reproduce

The [public demo](../examples/first-proof/README.md) includes positive and negative controls. Its [saved transcript](launch/evidence/demo.txt) and [JSON](launch/evidence/demo.json) are captured command output with normalized local paths.

The framework's suite is run with:

```sh
python3 tests/run_without_silent_skips.py
```

A passing local run, fixture corpus size or author's development history does not measure how much time a new user saves. That needs independent adoption trials.
