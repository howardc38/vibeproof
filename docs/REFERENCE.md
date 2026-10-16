# Checks, evidence and limits

This is the detailed companion to the [feature and command map](FEATURES.md) and the short README. It explains mechanisms, evidence and limits; implementation and [SPEC.md](SPEC.md) remain the sources to check when behavior changes. For the operating loop see [USING.md](USING.md); for installation see [GETTING_STARTED.md](GETTING_STARTED.md).

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
| 6 | CHECKER_TAMPERED; checker entry or registered program fingerprint does not match |
| 7 | SUBJECT_MOVED during the check |
| 8 | TIMEOUT |
| Other codes, including 2 and 3 | UNKNOWN_EXIT; not a pass |

See [runner.py](../kernel/runner.py) and [state.py](../kernel/state.py) for the actual state mapping.

## Which checks hold ship

The shipped tree has 20 detector files, of which 11 have conditional-detector registrations, and 21 registered checker/kind entries. Installation filters framework-only and inapplicable components; another repo does not necessarily receive every entry.

Current immediate ship kinds:

`test`, `scope`, `secret`, `fail-closed`, `external-write`, `review-finding`, `runtime-proof`, `surface-proof`.

Current initially report-only kinds:

`test-shape`, `test-weakened`, `test-expectation`, `test-token-shape`, `signature-change`, `dangling-ref`, `lint`, `layer-boundary`, `design-pins`, `control-plane-budget`, `dead-wiring`, `registry-consistency`, `spec-coverage`.

The last four framework self-checks are not ordinary adopter checks. `layer-boundary` also needs a layer declaration. The actual table is [claim_kinds.json](../.v4/claim_kinds.json).

`derive` stores a claim before any checker verdict. `check` records attempts regardless of whether the kind is a blocker or a report. `ship` then derives current states and splits them using the kind's gate policy. Sources: [claim insertion](../kernel/derive.py), [attempt recording](../kernel/runner.py), [state split](../kernel/state.py), [ship predicate](../kernel/lifecycle.py).

A report-only claim can escalate when the evaluated task has too many unresolved claims of the same kind, a claim is too old, or its distinct failing attempts exceed the configured limit. The comparisons are strict `>` checks. Defaults are 10, 14 days and 5 failures. **This is not a global background collector that makes every old report block every future task.** The split receives the current task's report. See [threshold logic](../kernel/state.py) and [task selection](../kernel/state.py).

Accepted risk follows the configured policy.

In particular, **test deletion detection is not an unconditional ship blocker by default**. See [the registry](../.v4/claim_kinds.json) and [the grouping predicate](../kernel/state.py).

## Review lenses and findings

`review lens` renders the questions and records that the brief was taken. `review done` records the reviewer's stated result (`completed`, `not_applicable`, `not_evaluable`, `failed`), its finding count, and the note/evidence when given; a result other than `completed` is refused (REFUSED, exit 2) unless a non-blank note or evidence reference carries the reason, on the run-less and run-bound writers alike; a `not_evaluable` or `failed` report is carried separately from a completed review in `sweep` and `ship`, so it is not counted as coverage. Sweep and ship classify one selected report snapshot and display its saved notes and evidence; sweep history retains those diagnostics. A failed lens keeps sweep cadence partial, while `not_evaluable` accounts for a context-limited lens without claiming review coverage. They are distinct events. Neither is a mechanical proof that the reviewer read the code correctly. Sources: [brief generation](../kernel/review.py), [brief event](../kernel/review.py), [completion event](../kernel/review.py).

`review add` creates the fixed `review-finding` kind at validated file/symbol coordinates. With `--task`, it belongs to that task. Without a task, the code creates/uses the standing `repo-review` task. **A finding on `repo-review` does not automatically block an unrelated feature task's ship.** See [finding creation](../kernel/review.py).

The repair-checker path can run the closing test at the supplied parent commit and the current tree, and observe target execution. Text-based closure and accepted-risk paths also exist, so do not describe every closed finding as a behavior test. See [review checker](../checkers/review_finding.py) and [red-green execution](../kernel/redgreen.py).

A committed same-file Python function/method rename can be offered with `review close --rename-commit`. The kernel verifies an unchanged function structure across each rename step, preserves the original claim, and rechecks execution of the resolved name with the normal red/green conditions. Renamed closure parameters participate in evidence staleness. See [rename validation](../kernel/review_renames.py).

## Language and runner support

The full process flow is exercised on macOS and Linux. It uses POSIX process-group handling; native Windows compatibility has not been established. Facts call-site discovery scans Python and Go; TS/JS structural checks and UI-path discovery are separate mechanisms.

| Mechanism | Current implementation |
|---|---|
| Ordinary suite command | Runs the configured command and checks exit status and recognizable output |
| Did the suite reach changed files? | Python `.py` files; excludes designated test/fixture/framework files. It fails when none were reached, not when any one was missed |
| Repair test fails before, passes after and executes the target | Python tracing, Go coverage, and Node/V8 coverage paths exist. Availability depends on language, runner and source attribution |
| Existing finding after a symbol rename | `review close --rename-commit` validates committed same-file Python function/method renames, preserves claim identity and still requires executable red/green proof |
| Python structural analysis | Standard-library AST |
| Go structural analysis | Go AST helper; requires a working Go toolchain |
| TS/JS structural analysis | Masked scanners; not a complete compiler or type checker |
| Rust | Limited scanning support; do not assume parity with Python |

The earlier README said the Go/Node tracing code was unreachable from the review checker. That was out of date: [review_finding.py](../checkers/review_finding.py) now dispatches on `redgreen.traceable`, and [redgreen.py](../kernel/redgreen.py) includes Go and Node paths. This does not establish compatibility with every runner. A plain Node example has been exercised; each target runner still needs verification.

## What a green result does not establish

- Importing a Python file is enough to mark the file as executed for the ordinary suite check. It does not prove that the changed function, branch or requirement was tested.
- The test-weakening check compares live-test counts with the base. Removing one meaningful test and replacing it with an unrelated nonempty test can preserve the count.
- Source-pattern checks can miss wrappers and cross-file behavior. They can also report a pattern that is safe for reasons outside the file.
- Runtime proof needs your real trigger, truth query and expected result. Surface proof requires fresh results from your declared runner, not printed counts; browser proof also requires declared cases and observed navigation. The optional [Playwright integration](../.v4/surface/INTEGRATION.md) uses existing worker/reviewer roles when tests are missing. It does not invent acceptance criteria or certify business assertions.
- A review finding can have a text-based closure. That proves text changed, not behavior.
- An engagement sentence passes mechanical checks; its acceptance is not proof of understanding.
- Prompts requesting independent or blind reviews are not proof that reviewers operated independently.

## Ledger and evidence lifetime

The SQLite ledger lives under the Git common directory, so worktrees of one repo share it. Claims and actual checker attempts are records; current state is derived from those records and present inputs. Sources: [ledger location](../kernel/ledger.py), [state calculation](../kernel/state.py).

Passes are checked against relevant subject content, config, checker implementation, facts, detector identity and, for repo-scoped questions, a relevant worktree digest. Accepted risks also cover an input-state key rather than remaining valid forever. Sources: [stale reasons](../kernel/state.py), [accepted-risk state](../kernel/state.py).

Successful ship events retain a versioned source observation: original task
base, actual and assigned worktree, observed HEAD/tree, a product-content
fingerprint and Git-reported dirty context. `status` exposes completion history
separately from current claim states, so later edits can make claims stale
without rewriting the historical observation. Legacy completion provenance is
unknown; it is never inferred from today's HEAD. The fingerprint reads tracked
files despite Git status flags and includes executable bits and symlink targets,
with explicit operational exclusions. Observation Git commands ignore inherited
Git environment overrides; an alternate index cannot substitute its inventory.
It does not prove every input was tested,
recover dirty bytes, or change existing proof staleness or attempt field meaning.
See [source observation](../kernel/hashing.py) and [completion lifecycle](../kernel/lifecycle.py).

A ship exports before appending its successful completion event. Use a later
explicit export to carry that event into portable history. Export failure before
the append records no new completion and can leave partial filesystem output.
An insertion or commit error returns non-success and preserves older history;
a fresh ledger read determines whether the append committed. This ordering is
not a database/filesystem transaction.
The final ship/abandon eligibility read and append share a SQLite write lock,
so a concurrent abandonment prevents a later completion and a completion
prevents later abandonment.

The ledger uses append-only SQLite triggers and hash chains to make ordinary writes controlled and recorded. It is not an immutable external trust service. The `task` table is not covered by the same chain as attempts and events. A process with sufficient file access can tamper with local state; do not market the ledger as impossible to edit. Some signer identities are supplied by the calling process; see [Hooks and accepted risk](#hooks-and-accepted-risk).

`v4 --repo <checkout> audit --events <export> --signatures` checks the exported
attempt/event chains and reconciles accepted-risk rows from all segments with
Git risk records, including available history when a record is absent on the
current branch. It creates no live ledger. Without `--signatures`, the command
reports a chain-only check. Accepted-risk rows are unchained and have no chained
acceptance event: agreement with a record does not authenticate those rows or a
human signer. Redacted reasons are compared as permitted views, without proving
their original values. Supply the full export set, projection sidecar if any,
and the Git history containing the records; missing or malformed evidence fails.

## Hooks and accepted risk

The shipped host adapters target Claude Code and Codex. Installation defaults to Claude; --hosts selects Claude, Codex or both. See [CODEX.md](CODEX.md) for activation, identity binding and permissions.

Write/Edit hooks examine supported tool payloads and the declared scope. They also require engagement on visible claims until the initial engagement gate is cleared. Both hosts select the task worktree first. Ambiguous local tasks are refused instead of selecting the newest. Explicit native agent bindings are used when the host supplies that identity. The shell guard examines command text. Shell scripts and other execution routes are not a security boundary. Hooks stand down when required state cannot be read. The stop hook blocks once per host stop continuation; later turns can be checked again.

The risk command permits an agent-signing path, including `--no-tty-check`. A stored name is not independently verified human approval. A risk signature must meet the kernel's acceptance conditions and expires against the inputs it covers. Read [risk.py](../kernel/risk.py) for attribution and [lifecycle.py](../kernel/lifecycle.py) for what ship actually accepts.

## Who checks the checkers?

Checker registration calls the fixture verifier for each claimed kind. It requires red cases to fail, green cases to pass and bypass cases to fail; byte-identical copies of red cases do not qualify as distinct bypass cases. The same fixture is run twice to compare its exit code and stdout. Failed verification prevents successful registration. Registration pins the entry and its transitive program fingerprint; changing either requires passing the fixtures again. `doctor` reports older entries whose dependencies have not yet been pinned. Sources: [fixture checks and registration](../kernel/register.py).

Detector verification has a different contract: the detector must exit 0 and emit claims on red/bypass cases, and emit no claims on green cases. A detector proposes questions; failing its process is not how it reports a detected defect. See [detector verification](../kernel/register.py).

This provides a useful check on custom rule code. It does not make fixtures exhaustive or turn a known blind spot into coverage.

## Maintenance and host coordination

Maintenance adds a shared Claude `/maintain` / Codex `$vibeproof-maintain` entrance for current attention, native job setup, versioned review and authorized repair handoffs. Its snapshots store source/configuration fingerprints and link review results; they do not archive the source tree. Monitor work concerns framework setup and criteria drift, rather than service uptime. Telegram transport and its macOS receiver keep delivery/acknowledgement separate from claim closure. A selected-lens review does not reset full-review cadence. See [USING](USING.md#periodic-maintenance-and-findings) and [SPEC](SPEC.md#maintenance-workflow-and-versioned-review).

## Evidence you can reproduce

The [public demo](../examples/first-proof/README.md) includes positive and negative controls. Its [saved transcript](launch/evidence/demo.txt) and [JSON](launch/evidence/demo.json) are captured command output with normalized local paths from 2026-09-14. They include a real review attempt becoming `ANSWERED`, then `STALE` after a source edit, without inventing another attempt. They are the current recording baseline, not proof of every later implementation; the 2026-09-05 recording remains historical. Rerun the demo to produce current evidence.

The framework's suite is run with:

```sh
python3 tests/run_without_silent_skips.py
```

A passing local run, fixture corpus size or author's development history does not measure how much time a new user saves. That needs independent adoption trials.
