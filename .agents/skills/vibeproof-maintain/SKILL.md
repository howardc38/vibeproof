---
name: vibeproof-maintain
description: Configure or run vibeproof maintenance, review cadence, authorized repairs and actionable notifications.
---

# Vibeproof maintenance

You are the orchestrator. Use the installed `./bin/v4 --repo <absolute-root> maintain` interface; the kernel owns claim state, run coverage and handoff records. Read `maintain --help` for flags and `maintain schema` for accepted JSON fields. This workflow does not grant permission for new repairs, risk acceptance, messages to new recipients or publication.

## Setup and host scheduling

Read `maintain status` first. Reuse the user's existing choices; ask only for missing cadence, host, authorized repair scope and notification destination. `maintain setup --data <json-file>` records `mode` (`review` or `repair`), concrete authorization in `repair_scope`, and declared `limits` (a positive-integer object; supported keys come from `maintain schema`, not prose or invented settings). Keep credentials out of the prompt and repository. Control JSON may be supplied with `--data -` on stdin or from an OS temporary file; do not write control payloads into the source tree after a review snapshot starts.

The CLI does not create native scheduled jobs. Use the host's actual scheduling tool to create/update the existing job, then read it back. Record that observation using `maintain schedule --data <json-file>` with `host`, `job_id`, `status`, `evidence` (the real host result reference), and `scheduled_prompt`. An observation is not a fresh host query. To pause/resume a schedule, act on that same host job and update its observation; do not merely flip a local setting.

Use a standalone fresh maintenance session for periodic review. The scheduled prompt must name this workflow, the absolute adopter root, the configured job ID, scope and limits. At execution use `--trigger scheduled --job <id>`; a manual `once` uses the default manual trigger. Distinguish desktop persistent scheduling from session-only CLI loops. If the required host tool is absent, report that capability gap; do not fabricate a job file or claim scheduling is active.

## Execute once or after a scheduled wakeup

1. Read status/attention. For a scheduled wakeup use the returned `cadence` decision: if not due, handle notifications and existing handoffs only; unknown cadence needs investigation. Manual once may request a review before the interval. OPEN may only mean not checked; STALE means evidence needs refreshing. Follow the reported check/derive/investigation route before deciding code is broken. Each task is evaluated in its recorded worktree. Missing worktrees are unknown, not clean.
2. Review a stable tree. If the chosen tree is being edited, use an authorized isolated Git worktree at a declared commit or wait. An unrelated worktree's activity does not prevent isolated read-only review. Before repairs/merge, recheck the target and existing authorization. Do not merge into the user's working checkout while it is changing.
3. Resume a matching partial run with `maintain resume --id <run>` only if its source is unchanged. Reconcile existing host assignments first. Otherwise start a run with `maintain start --host <host> --session <identity> [--id <stable-id>] [--task <active-task>] [--context-task <request-source>]`. A context task supplies the request/base without moving repo-wide findings onto an ended task. Inspect `expected` and `excluded` in the returned manifest; do not assume a lens count.
4. Dispatch a fresh reviewer per selected lens, queueing within available capacity. Pass only root, run ID, lens and its declared input coordinates. Each reviewer runs `review lens --run <id> --lens <slug>`, files findings using the printed coordinates, then reports `review done` for the same run. Missing inputs are `not_evaluable`, not zero findings or invented N/A. Keep worker rationale and prior conclusions out of the blind review input.
5. When status reports `monitor.due` or facts, checker/fixture criteria or framework behavior changed, dispatch a fresh general-purpose agent as monitor (Codex: `v4-monitor`), with both `.github/monitor/PROMPT.md` and `SCOPE.md`, the relevant diff/version and run ID. A changed criterion makes review due before the normal interval, within the configured time window. Reuse the lenses; do not paste a second copy of the corpus into its prompt. No relevant changes is a valid reason not to dispatch an extra monitor.
6. Wait for every requested result. In Claude headless sessions, use foreground agents when a blocking result is needed; for background agents use the available TaskOutput/wait mechanism and read completion before ending the session. A returned background task ID is still running. Record and inspect handoffs as below. Read the run's `coordination_checks` and compare the observed findings before finishing. When multiple distinct claims exist, supply `--data` with `coordination` keyed by those check IDs, each carrying `result: completed`, a concrete `note`, and all reviewed `claims`. Use existing `review group` when they share a fact; do not invent groups. Finish coverage using `maintain finish --id <run>`. A selected-lens run can complete its assignment without postponing the full review; inspect `advances_cadence`. Partial/stale/failed is not a complete sweep. Correct missing input or resume valid work; do not mark a waiting process complete.
7. Group confirmed findings by the same fact using existing `review group`, preserving independently different impacts. In review mode, notify and report the next decision. In repair mode, create real scoped tasks, select run/wave according to actual dependencies and independent acceptance, and link each original claim. Repairs do not imply authorization to sign risks, push or merge.
8. Read back the original claim's current proof after repairs, and revalidate after any authorized merge. Leave failed/unknown work recorded and actionable; do not lower checkers, tests or gates to finish the run.

## Monitor ↔ orchestrator ↔ worker

Use native agent messages for dispatch/results and `maintain handoff --data <json-file>` for durable correlation. Each payload carries `id`, `run_id`, `action`. Reuse the same handoff ID when retrying the same operation.

- Before dispatch, `action: prepare` names `role`, original `claim` if applicable, and repair `task` when applicable. Unattended repair tasks use concrete file paths inside the configured grant. Immediately before calling the host, run `action: check_dispatch`; a changed target or revoked grant refuses new dispatch. Then dispatch with the host tool and record `action: dispatched` plus the actual `host_ref`. If the receipt reports `dispatch_unknown`, reconcile the already-sent host task before any retry.
- If dispatch may have succeeded but its result is unknown, record `dispatch_unknown`. Query the host; do not start another worker merely because a reply is absent.
- A result carries `action: result`, `reviewed_head` from the assignment, `decision`, `reason`, `evidence` references and, when requesting work, `acceptance` conditions. Missing/contradictory fields require clarification, not an inferred success. Foreground agents may report before the parent obtains their host ID; `result_pending_receipt` preserves that result until the orchestrator records the actual dispatch receipt. Do not redispatch to resolve that ordering.
- Route `needs_fix` to a scoped worker; `needs_evidence` to the missing observation/test; `needs_checker` to checker-author only after independently confirming a checker defect and obtaining the relevant protected-path authorization. `finding_disputed` is a premise to verify/clarify, not an automatic PASS or risk waiver. `needs_user_decision` goes to the user. `risk_recommended` is only a recommendation until the existing authorization and signing rules apply.
- Workers return `work_completed` or `work_failed` with evidence, never self-sign or ship. Record results through `action: result` even if they arrive after settlement; do not smuggle result fields through link_repair. Reviewers may report `review_completed`/`review_failed`; the existing `task-splitter` role reports `plan_completed`/`plan_failed` with its actual plan evidence. Inspect any late-result conflict. Link a repair using `action: link_repair` and its task. Bind applicable repair proof through the normal `review close` interface, then `action: settle` runs the real checker in the linked worktree and records the original claim's actual result. A worker's message alone never settles it.
- `cancel` requires a recorded reason and an ended repair task. Cancellation does not close the original finding.

Role labels and IDs correlate work; they do not authenticate a human or create an unbypassable permission boundary.

## Telegram notifications

Only after the user supplies the destination and safely stored token, configure using `maintain notify --data <json-file>` with `operation: configure`, `token_file`, numeric `chat_id`, and allowed `user_ids` for groups. Never print the token. A recipient change revokes older queued deliveries.

Enqueue a concise message using `operation: enqueue`, a stable `subject` (claim/run ID), current `revision`, and `message`; then deliver its returned ID with `operation: deliver`. Repeat failures are bounded. A sending/unknown result may already exist remotely and must not be blindly resent. `operation: flush` handles eligible pending messages/reminders. `operation: resolve` can stop reminders only after the current kernel claim is terminal.

Use one pure-I/O `maintain listen` process per bot, independently of the lens schedule. On macOS, `maintain receiver --data <json-file>` with `operation: start` installs/starts its launchd service; `status`, `stop` and `remove` manage that same service. Read both the native service state and `maintain notifications` for successful polling; loaded does not mean Telegram is reachable. The service uses the installed adopter launcher and restarts on failure; keep that adopter/framework path available. Other operating systems need a supervised service supplied by their host. Do not replace an existing webhook or compete with another consumer. The phone button acknowledges that exact notification revision; it does not repair a claim or approve an action. Incoming updates expire after Telegram's retention window, so a stopped receiver cannot promise to reconstruct lost acknowledgements. Local `maintain ack --id <notice>` is used only when the user actually asks to acknowledge it.

For setup acceptance, observe a real scheduled fire and real Telegram receipt/button callback. Mock transport tests or a manual invocation are not those proofs. Keep unavailable external tests explicitly pending.

Codex host contract:
- Run CLI commands with explicit --repo; supply --task only on commands that accept it. Use --help for action-specific flags. A shell export does not bind hooks.
- Bind the assigned V4 task using the session/agent identity supplied by SessionStart/SubagentStart.
- For another worktree use an explicit exec working directory. Patch tools may restrict writes to the host project; dispatch the worker in its authorized worktree or use a permitted tool there.
- Never infer a worker identity from the parent session alone.
- Reviewer source access is read-only in intent; review add/done still write evidence through v4.


Codex orchestration:
- Use the native v4-task-splitter, v4-worker, v4-reviewer and v4-checker-author agents.
- Start every native role with a fresh context and explicit inputs; inherited-context forks are unavailable in some ephemeral Codex runs. For reviewers pass only repo, lens and necessary diff coordinates.
- Dispatch up to the available agent capacity, queue the rest, and wait for every requested result. This replaces the Claude source's all-at-once scheduling and single-shell export instructions.
- One worker task per worktree. Bind the task before writing; use explicit --repo and supported --task flags.
- The orchestrator owns ship and risk decisions; never attribute an agent signature to a person.
