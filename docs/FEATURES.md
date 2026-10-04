# What is included in vibeproof

This derived map is grounded in the implementation and shipped assets. Its command and registry inventory was rechecked on 2026-10-04. It describes a workflow with distinct mechanical and judgment-based parts. It does not turn the existence of a prompt into proof that an agent followed it.

## The whole path

| Stage | Mechanism and boundary | Implementation |
|---|---|---|
| 1. Define the change | Record the request and allowed paths, confirm repo facts, engage with selected rule kinds and account for request clauses. Facts provide detector vocabulary; `cover` records accounting rather than judging delivery. | [Task lifecycle](../kernel/lifecycle.py), [facts](../kernel/facts.py), [engagement](../kernel/engagement.py), [request accounting](../kernel/request_cover.py) |
| 2. Derive and check | Detectors inspect scoped subjects and propose claims; registered checkers record attempts. Supported hooks check edits/stops. Task policy and risk acceptance determine which unresolved claims hold ship. | [Derivation](../kernel/derive.py), [runner](../kernel/runner.py), [state/policy](../kernel/state.py), [hooks](../hooks/) |
| 3. Prove behavior | Use the repo's test command, executable repair tests, configured surface runner receipts and runtime truth queries where applicable. Meaningful assertions and repo-specific setup determine what is proven. | [Test checker](../checkers/test.py), [repair checker](../checkers/review_finding.py), [surface](../kernel/surface.py), [runtime](../checkers/runtime_proof.py) |
| 4. Keep evidence current | Relevant input hashes determine whether prior attempts apply. Versioned reviews link source/configuration fingerprints to review results and distinguish partial, complete and stale work. | [Evidence state](../kernel/state.py), [local ledger](../kernel/ledger.py), [maintenance snapshots/results](../kernel/maintenance.py) |
| 5. Coordinate and maintain | The coding-agent host launches parallel work in worktrees and revalidates merged code. Maintenance coordinates authorized repair handoffs; monitor reviews inspect framework criteria, fixtures, facts and review activity. | [Host workflows](CODEX.md), [merge accounting](../kernel/remerge.py), [handoffs](../kernel/maintenance.py), [monitor brief](../.github/monitor/PROMPT.md) |
| 6. Extend and inspect | Fixture verification supports custom checkers/detectors. `doctor` inspects wiring; coverage maps declared risk classes to kinds. Trends, recorded cost, ledger export and optional notices help inspect and route work. | [Registration](../kernel/register.py), [doctor](../kernel/doctor.py), [coverage](../kernel/coverage.py), [trend](../kernel/trend.py), [cost/export CLI](../kernel/cli.py), [notifications](../kernel/notifications.py) |

Claims and actual checker attempts use the local ledger. Report-only is a gate classification; emitted report claims and their attempts remain recorded. Write hooks operate before supported edits; the scope checker also inspects the resulting diff. Agent launch and recurring scheduling belong to the host. The CLI's due reports do not perform a review.

## Components and their practical purpose

| Component | What it does | What it does not establish |
|---|---|---|
| Task and scope | Records the request, starting commit and allowed paths; records widening/narrowing decisions | That the original request was understood correctly |
| Detectors | Raise applicable claims from scoped code/subjects and declarations; some need language tools | A verdict that the code is correct |
| Checkers | Execute already registered checks, report exit codes and preserve actual attempt output | Automatic checker authorship or complete semantic correctness |
| Review lenses | Supply structured questions for human/agent review, including design fit, request fidelity and test sufficiency | That a reviewer actually read everything or reviewed independently |
| Agent and slash-command templates | Help Claude and Codex split, implement, review, author checkers and coordinate work | An autonomous Python scheduler that starts or isolates every agent |
| Hooks | Check supported Write/Edit/Bash/Stop interactions and record observed activity where possible | An unbypassable security boundary |
| Ledger and current state | Store claims, attempts and events; calculate whether previous evidence still applies | An immutable external audit service |
| Ship policy | Separates immediate blockers from reports and evaluates the task's ship predicate | A deployment command or an all-requirements correctness guarantee |
| Runtime / surface proof | Execute declared triggers/truth queries; require fresh surface case results, with an optional Playwright adapter | Automatic comprehensive tests without repo-specific setup |
| Registry validation | Exercise checkers/detectors against fixtures before registration; check installed identities | That the fixture corpus covers every possible failure |
| Maintenance tools | Expose review cadence, versioned results, authorized repair handoffs and debt/state summaries | That a due review was performed or a worker's completion report proves a repair |

## Claude assets versus CLI commands

The installer copies **four agent prompt files**: `task-splitter`, `worker`, `reviewer` and `checker-author`. It copies **four Claude slash-command workflows**: `/run`, `/sweep`, `/wave` and `/maintain`. These are Markdown assets consumed by the coding-agent host. The Python installer copies them; it does not itself launch a model team. See [the copy loop](../kernel/install.py), [agents](../.claude/agents/) and [commands](../.claude/commands/).

Codex adds generated roles for those responsibilities and the independent monitor, plus run/sweep/wave/maintain skills and native lifecycle hooks. These use the same kernel. See [CODEX.md](CODEX.md) for the host-specific mechanics.

The **34 `v4` CLI commands** are a separate executable interface, defined by [the CLI parser](../kernel/cli.py):

| Purpose | Commands |
|---|---|
| Adopt and inspect | `init`, `install`, `doctor`, `facts`, `doctrine`, `explain`, `host` |
| Operate a task | `task`, `scope`, `derive`, `engage`, `check`, `status`, `ship`, `abandon`, `cover` |
| Review and unresolved work | `review`, `sweep`, `maintain`, `risk` |
| Validate and register checks | `verify`, `register`, `verify-detector`, `register-detector`, `run-checker`, `accept` |
| Inspect history and measurement | `round`, `coverage`, `trend`, `foresee`, `remerge`, `cost`, `export`, `audit` |

## Detectors, checkers and gate modes

The shipped tree has 20 detector files, of which 11 have conditional-detector registrations, and 21 registered checker/kind entries. Installation filters framework-only and inapplicable components; another repo does not necessarily receive every entry.

Current immediate ship kinds:

`test`, `scope`, `secret`, `fail-closed`, `external-write`, `review-finding`, `runtime-proof`, `surface-proof`.

Current initially report-only kinds:

`test-shape`, `test-weakened`, `test-expectation`, `test-token-shape`, `signature-change`, `dangling-ref`, `lint`, `layer-boundary`, `design-pins`, `control-plane-budget`, `dead-wiring`, `registry-consistency`, `spec-coverage`.

The last four framework self-checks are not ordinary adopter checks. `layer-boundary` also needs a layer declaration. The actual table is [claim_kinds.json](../.v4/claim_kinds.json).

`derive` stores a claim before any checker verdict. `check` records attempts regardless of whether the kind is a blocker or a report. `ship` then derives current states and splits them using the kind's gate policy. Sources: [claim insertion](../kernel/derive.py), [attempt recording](../kernel/runner.py), [state split](../kernel/state.py), [ship predicate](../kernel/lifecycle.py).

A report-only claim can escalate when the evaluated task has too many unresolved claims of the same kind, a claim is too old, or its distinct failing attempts exceed the configured limit. The comparisons are strict `>` checks. Defaults are 10, 14 days and 5 failures. **This is not a global background collector that makes every old report block every future task.** The split receives the current task's report. See [threshold logic](../kernel/state.py) and [task selection](../kernel/state.py).

## Review lenses and findings

The current 13 lens files cover architecture fit, configuration/hardcoded-secret placement, DevX, runtime electrification, general rules with false instances, implementation boundaries, LLM-agent action surfaces, near misses, observability, runtime reliability/performance, request fidelity, permissions/security and test sufficiency.

`review lens` renders the questions and records that the brief was taken. `review done` records the reviewer's stated completion/count. They are distinct events. Neither is a mechanical proof that the reviewer read the code correctly. Sources: [brief generation](../kernel/review.py), [brief event](../kernel/review.py), [completion event](../kernel/review.py).

`review add` creates the fixed `review-finding` kind at validated file/symbol coordinates. With `--task`, it belongs to that task. Without a task, the code creates/uses the standing `repo-review` task. **A finding on `repo-review` does not automatically block an unrelated feature task's ship.** See [finding creation](../kernel/review.py).

The repair-checker path can run the closing test at the supplied parent commit and the current tree, and observe target execution. Text-based closure and accepted-risk paths also exist, so do not describe every closed finding as a behavior test. See [review checker](../checkers/review_finding.py) and [red-green execution](../kernel/redgreen.py).

A committed same-file Python function/method rename can be offered with `review close --rename-commit`. The kernel verifies an unchanged function structure across each rename step, preserves the original claim, and rechecks execution of the resolved name with the normal red/green conditions. Renamed closure parameters participate in evidence staleness. See [rename validation](../kernel/review_renames.py).

## Ledger and evidence lifetime

The SQLite ledger lives under the Git common directory, so worktrees of one repo share it. Claims and actual checker attempts are records; current state is derived from those records and present inputs. Sources: [ledger location](../kernel/ledger.py), [state calculation](../kernel/state.py).

Passes are checked against relevant subject content, config, checker implementation, facts, detector identity and, for repo-scoped questions, a relevant worktree digest. Accepted risks also cover an input-state key rather than remaining valid forever. Sources: [stale reasons](../kernel/state.py), [accepted-risk state](../kernel/state.py).

The ledger has append-only triggers and chained records, but is local storage. Not every table has the same hash coverage; task text is not covered like attempts/events. Some signer identities are supplied by the calling process. Keep the [trust limitations](REFERENCE.md) visible.

## Who checks the checkers?

Checker registration calls the fixture verifier for each claimed kind. It requires red cases to fail, green cases to pass and bypass cases to fail; byte-identical copies of red cases do not qualify as distinct bypass cases. The same fixture is run twice to compare its exit code and stdout. Failed verification prevents successful registration. Registration pins the entry and its transitive program fingerprint; changing either requires passing the fixtures again. `doctor` reports older entries whose dependencies have not yet been pinned. Sources: [fixture checks and registration](../kernel/register.py).

Detector verification has a different contract: the detector must exit 0 and emit claims on red/bypass cases, and emit no claims on green cases. A detector proposes questions; failing its process is not how it reports a detected defect. See [detector verification](../kernel/register.py).

This provides a useful check on custom rule code. It does not make fixtures exhaustive or turn a known blind spot into coverage.

## Other useful mechanisms

- Scope narrowing refuses to hide paths already changed by the task: [scope.py](../kernel/scope.py).
- Runtime proof runs a configured trigger and asks the declared truth owner: [runtime_proof.py](../checkers/runtime_proof.py).
- The stop hook interrupts once and then permits a further stop: [stop_gate.py](../hooks/stop_gate.py).
- Sweep timing and review execution remain distinct. The CLI reports when work is due; an agent/automation must actually perform the review: [sweep.py](../kernel/sweep.py) and [the slash-command workflow](../.claude/commands/sweep.md).
- Request coverage records how clauses are accounted for; it is not an intent-understanding oracle: [request_cover.py](../kernel/request_cover.py).
- `doctor` inspects configuration, facts, registries, installed fingerprints, hooks and recorded activity. Its results concern framework wiring and history, not generic environment/dependency readiness: [doctor.py](../kernel/doctor.py).
- Coverage maps the declared risk rubric to registered kinds; review completion is recorded separately. Neither measures code coverage or exhaustive correctness. Trend reports summarize ledger history; cost reports summarize recorded observations rather than complete vendor billing. Export writes portable ledger records: [coverage.py](../kernel/coverage.py), [trend.py](../kernel/trend.py), [cost/export commands](../kernel/cli.py).

For an adoption path use [GETTING_STARTED.md](GETTING_STARTED.md); for a market comparison use [the code-grounded positioning analysis](launch/POSITIONING.zh-TW.md).

Maintenance adds a shared Claude `/maintain` / Codex `$vibeproof-maintain` entrance for current attention, native job setup, versioned review and authorized repair handoffs. Its snapshots store source/configuration fingerprints and link review results; they do not archive the source tree. Monitor work concerns framework setup and criteria drift, rather than service uptime. Telegram transport and its macOS receiver keep delivery/acknowledgement separate from claim closure. A selected-lens review does not reset full-review cadence. See [USING](USING.md#periodic-maintenance-and-findings) and [SPEC](SPEC.md#maintenance-workflow-and-versioned-review).
