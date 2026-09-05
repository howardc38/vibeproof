# What is included in vibeproof

This map is grounded in the implementation and shipped assets, checked on 2026-09-05. It describes a workflow with distinct mechanical and judgment-based parts. It does not turn the existence of a prompt into proof that an agent followed it.

## The whole path

```text
Request + allowed paths
          |
     task / scope       <- Claude /run and agent prompts help operate the loop
          |
       derive <--------- detectors inspect the applicable subject
          |                          |
          |                       claims
          |                          |
review lens -> reviewer -> review add +----> ledger
                                     |
                    engagement -> checkers -> attempts / current state
                                     |
                 ship reads this task's claims + configured policy
                        /                         \
                   blocking                 report-only
                 holds this ship       visible without necessarily holding it
```

All claim/attempt paths above use the ledger. Report-only is a gate classification, not a separate destination for otherwise discarded findings. Write hooks operate before supported edits; the scope checker also inspects the resulting diff.

## Components and their practical purpose

| Component | What it does | What it does not establish |
|---|---|---|
| Task and scope | Records the request, starting commit and allowed paths; records widening/narrowing decisions | That the original request was understood correctly |
| Detectors | Raise applicable questions from code/subjects and declarations | A verdict that the code is correct |
| Checkers | Execute particular checks, report exit codes and preserve actual attempt output | Complete semantic correctness |
| Review lenses | Supply structured questions for human/agent review, including design fit, request fidelity and test sufficiency | That a reviewer actually read everything or reviewed independently |
| Agent and slash-command templates | Help Claude split, implement, review, author checkers and coordinate work | An autonomous Python scheduler that starts or isolates every agent |
| Hooks | Check supported Write/Edit/Bash/Stop interactions and record observed activity where possible | An unbypassable security boundary |
| Ledger and current state | Store claims, attempts and events; calculate whether previous evidence still applies | An immutable external audit service |
| Ship policy | Separates immediate blockers from reports and evaluates the task's ship predicate | A deployment command or an all-requirements correctness guarantee |
| Runtime / surface proof | Execute declared triggers, truth queries or UI tests | Automatic comprehensive tests without repo-specific setup |
| Registry validation | Exercise checkers/detectors against fixtures before registration; check installed identities | That the fixture corpus covers every possible failure |
| Maintenance tools | Expose review cadence, debt/state summaries, cost, coverage and handoff-related information | That every scheduled review has been performed |

## Claude assets versus CLI commands

The installer copies **four agent prompt files**: `task-splitter`, `worker`, `reviewer` and `checker-author`. It copies **three Claude slash-command workflows**: `/run`, `/sweep` and `/wave`. These are Markdown assets consumed by the coding-agent host. The Python installer copies them; it does not itself launch a model team. See [the copy loop](../kernel/install.py#L454), [agents](../.claude/agents/) and [commands](../.claude/commands/).

The **32 `v4` CLI commands** are a separate executable interface, defined by [the CLI parser](../kernel/cli.py#L2460):

| Purpose | Commands |
|---|---|
| Adopt and inspect | `init`, `install`, `doctor`, `facts`, `doctrine`, `explain` |
| Operate a task | `task`, `scope`, `derive`, `engage`, `check`, `status`, `ship`, `abandon`, `cover` |
| Review and unresolved work | `review`, `sweep`, `risk` |
| Validate and register checks | `verify`, `register`, `verify-detector`, `register-detector`, `run-checker`, `accept` |
| Inspect history and measurement | `round`, `coverage`, `trend`, `foresee`, `remerge`, `cost`, `export`, `audit` |

## Detectors, checkers and gate modes

The shipped tree has 20 detector files, of which 11 have conditional-detector registrations, and 21 registered checker/kind entries. Installation filters framework-only and inapplicable components; another repo does not necessarily receive every entry.

Current immediate ship kinds:

`test`, `scope`, `secret`, `fail-closed`, `external-write`, `review-finding`, `runtime-proof`, `surface-proof`.

Current initially report-only kinds:

`test-shape`, `test-weakened`, `test-expectation`, `test-token-shape`, `signature-change`, `dangling-ref`, `lint`, `layer-boundary`, `design-pins`, `control-plane-budget`, `dead-wiring`, `registry-consistency`, `spec-coverage`.

The last four framework self-checks are not ordinary adopter checks. `layer-boundary` also needs a layer declaration. The actual table is [claim_kinds.json](../.v4/claim_kinds.json).

`derive` stores a claim before any checker verdict. `check` records attempts regardless of whether the kind is a blocker or a report. `ship` then derives current states and splits them using the kind's gate policy. Sources: [claim insertion](../kernel/derive.py#L583), [attempt recording](../kernel/runner.py#L302), [state split](../kernel/state.py#L447), [ship predicate](../kernel/lifecycle.py#L650).

A report-only claim can escalate when the evaluated task has too many unresolved claims of the same kind, a claim is too old, or its distinct failing attempts exceed the configured limit. The comparisons are strict `>` checks. Defaults are 10, 14 days and 5 failures. **This is not a global background collector that makes every old report block every future task.** The split receives the current task's report. See [threshold logic](../kernel/state.py#L413) and [task selection](../kernel/state.py#L479).

## Review lenses and findings

The current 13 lens files cover architecture fit, configuration/hardcoded-secret placement, DevX, runtime electrification, general rules with false instances, implementation boundaries, LLM-agent action surfaces, near misses, observability, prevention, request fidelity, permissions/security and test sufficiency.

`review lens` renders the questions and records that the brief was taken. `review done` records the reviewer's stated completion/count. They are distinct events. Neither is a mechanical proof that the reviewer read the code correctly. Sources: [brief generation](../kernel/review.py#L668), [brief event](../kernel/review.py#L772), [completion event](../kernel/review.py#L793).

`review add` creates the fixed `review-finding` kind at validated file/symbol coordinates. With `--task`, it belongs to that task. Without a task, the code creates/uses the standing `repo-review` task. **A finding on `repo-review` does not automatically block an unrelated feature task's ship.** See [finding creation](../kernel/review.py#L216).

The repair-checker path can run the closing test at the supplied parent commit and the current tree, and observe target execution. Text-based closure and accepted-risk paths also exist, so do not describe every closed finding as a behavior test. See [review checker](../checkers/review_finding.py#L151) and [red-green execution](../kernel/redgreen.py#L414).

## Ledger and evidence lifetime

The SQLite ledger lives under the Git common directory, so worktrees of one repo share it. Claims and actual checker attempts are records; current state is derived from those records and present inputs. Sources: [ledger location](../kernel/ledger.py#L273), [state calculation](../kernel/state.py#L64).

Passes are checked against relevant subject content, config, checker implementation, facts, detector identity and, for repo-scoped questions, a relevant worktree digest. Accepted risks also cover an input-state key rather than remaining valid forever. Sources: [stale reasons](../kernel/state.py#L149), [accepted-risk state](../kernel/state.py#L84).

The ledger has append-only triggers and chained records, but is local storage. Not every table has the same hash coverage; task text is not covered like attempts/events. Some signer identities are supplied by the calling process. Keep the [trust limitations](REFERENCE.md) visible.

## Who checks the checkers?

Registration calls the fixture verifier for each claimed kind. It requires red cases to fail, green cases to pass and bypass cases to fail; byte-identical copies of red cases do not qualify as distinct bypass cases. The same fixture is run twice to compare its exit code and stdout. Failed verification prevents successful registration. Sources: [fixture checks](../kernel/register.py#L259), [repeatability check](../kernel/register.py#L342), [register](../kernel/register.py#L489).

This provides a useful check on custom rule code. It does not make fixtures exhaustive or turn a known blind spot into coverage.

## Other useful mechanisms

- Scope narrowing refuses to hide paths already changed by the task: [scope.py](../kernel/scope.py#L294).
- Runtime proof runs a configured trigger and asks the declared truth owner: [runtime_proof.py](../checkers/runtime_proof.py#L240).
- The stop hook interrupts once and then permits a further stop: [stop_gate.py](../hooks/stop_gate.py#L385).
- Sweep timing and review execution remain distinct. The CLI reports when work is due; an agent/automation must actually perform the review: [sweep.py](../kernel/sweep.py) and [the slash-command workflow](../.claude/commands/sweep.md).
- Request coverage records how clauses are accounted for; it is not an intent-understanding oracle: [request_cover.py](../kernel/request_cover.py).

For an adoption path use [GETTING_STARTED.md](GETTING_STARTED.md); for a market comparison use [the code-grounded positioning analysis](launch/POSITIONING.zh-TW.md).
