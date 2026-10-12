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

Detectors propose claims; checkers answer them. Installation selects applicable components for the adopter. The [registry inventory, gate modes and escalation limits](REFERENCE.md#which-checks-hold-ship) explain which unresolved claims hold a task's ship.

## Review lenses and findings

The current 13 lens files cover architecture fit, configuration/hardcoded-secret placement, DevX, runtime electrification, general rules with false instances, implementation boundaries, LLM-agent action surfaces, near misses, observability, runtime reliability/performance, request fidelity, permissions/security and test sufficiency.

Use the [review and closure reference](REFERENCE.md#review-lenses-and-findings) for brief/completion events, finding ownership, repair tests and supported renames. A lens or completion record does not prove review quality.

## Ledger and evidence lifetime

The local ledger records claims, attempts and events; current state depends on present inputs. See [evidence lifetime and trust limits](REFERENCE.md#ledger-and-evidence-lifetime) for worktree sharing, staleness and the boundaries of local storage.

## Who checks the checkers?

Registration verifies custom checkers and detectors against fixtures and pins their program identity. The [fixture contracts and limits](REFERENCE.md#who-checks-the-checkers) distinguish the two mechanisms; passing fixtures does not establish exhaustive coverage.

## Other useful mechanisms

- Scope narrowing refuses to hide paths already changed by the task: [scope.py](../kernel/scope.py).
- Runtime proof runs a configured trigger and asks the declared truth owner: [runtime_proof.py](../checkers/runtime_proof.py).
- The stop hook interrupts once and then permits a further stop: [stop_gate.py](../hooks/stop_gate.py).
- Sweep timing and review execution remain distinct. The CLI reports when work is due; an agent/automation must actually perform the review: [sweep.py](../kernel/sweep.py) and [the slash-command workflow](../.claude/commands/sweep.md).
- Request coverage records how clauses are accounted for; it is not an intent-understanding oracle: [request_cover.py](../kernel/request_cover.py).
- `doctor` inspects configuration, facts, registries, installed fingerprints, hooks and recorded activity. Its results concern framework wiring and history, not generic environment/dependency readiness: [doctor.py](../kernel/doctor.py).
- Coverage maps the declared risk rubric to registered kinds; review completion is recorded separately. Neither measures code coverage or exhaustive correctness. Trend reports summarize ledger history; cost reports summarize recorded observations rather than complete vendor billing. Export writes portable ledger records: [coverage.py](../kernel/coverage.py), [trend.py](../kernel/trend.py), [cost/export commands](../kernel/cli.py).

For an adoption path use [GETTING_STARTED.md](GETTING_STARTED.md); for a market comparison use [the code-grounded positioning analysis](launch/POSITIONING.zh-TW.md).

For ongoing review and repair, use the shared Claude `/maintain` / Codex `$vibeproof-maintain` entrance. [Maintenance evidence limits](REFERENCE.md#maintenance-and-host-coordination) and the [operating guide](USING.md#periodic-maintenance-and-findings) distinguish review, authorized repair and notification delivery.
