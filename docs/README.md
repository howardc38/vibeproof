# vibeproof — which document to read

**8 documents, each owning a scope that does not overlap another's.** One fact is
authoritative in exactly one place; everywhere else only points at it.

| Document | What it owns | When to read it | What catches it when it goes stale |
|---|---|---|---|
| **[EVIDENCE.md](EVIDENCE.md)** | Every experiment that was actually run: method, numbers, and how firmly each conclusion stands (✅ measured / 📊 agent / ⚠️ judgement / ❌ retracted) | **Before citing any number** | No mechanical gate — it is a record, not a contract |
| **[USING.md](USING.md)** | Operating order and judgement: a request from opening a task through to ship, and the trap at each step | **To actually run a task** | No mechanical gate — it deliberately restates no contract; flags and constraints all stay in SPEC |
| **[SPEC.md](SPEC.md)** | The contract: every mechanism, every command, every constraint | **To build a kernel / checker / detector / hook** | `design_pins` (197 design pins) + `spec-coverage` (commands / hooks / agents / command rows / engaged kinds in both directions, a pin per section, dead cross-document references, numeric claims, the unbuilt list in reverse, command flags and their values) |
| **[RATIONALE.md](RATIONALE.md)** | Why: measured numbers, proposals that were refuted, retractions one by one | **A human who wants to understand the design → start here** | `design_pins` (6 design pins) + `spec-coverage` dead references |
| [DOGFOOD_LOG.md](DOGFOOD_LOG.md) | What building it forced out (the part no document could tell you) | To judge how firm a decision is | `spec-coverage` dead references |
| [FACTS.md](FACTS.md) | The format, provenance and update procedure of `.v4/facts.*.json` | To build a facts table for a new repo | CI runs `./bin/v4 facts verify --gone-only` on the table itself; the prose has no gate beyond `spec-coverage`'s dead-reference and count checks |
| [../CLAUDE.md](../CLAUDE.md) | **Generated** — layer ①'s standing rules (`SPEC.md` §14) | Never on purpose; it is already in your context | `registry-consistency` regenerates it and compares |
| [../.github/monitor/](../.github/monitor/) | The monitor role: `PROMPT.md` is what gets pasted into a separate session, `SCOPE.md` says what it may and may not do | To run a lens sweep | `spec-coverage` reads it (commands / flags / whether `v4` runs). **Whether a sweep is overdue has no mechanical gate** — the kind that judged it was removed in `a9ae5fb` (2026-08-24), leaving `v4 sweep` and CI's cron to answer whether it is due, and neither stops anything |

**The code is the implementation authority.** Every `<!-- pinned: -->` in SPEC is
verified by `checkers/design_pins.py` — rename a symbol and SPEC fails a check
rather than quietly becoming a lie.

```sh
./bin/v4 --repo . run-checker --checker checkers/design_pins.py --subject <subject.json>
./bin/v4 --repo . doctrine --check          # is CLAUDE.md still the generated artefact
./bin/v4 --repo . audit --events .v4/ledger_export.jsonl   # the export exists only once something has shipped
```

## Which part is the contract and which is not

Stated plainly, because "build from SPEC" is its only purpose:

- **The four working roles are in `.claude/agents/`, and `/run` strings them
  together.** The contract is `SPEC.md` §12.5 — that table is the contract, and
  the files under `.claude/` (four agents, three commands, and the hook
  settings) are prompts and configuration, which change.
- **Prompts are not in SPEC, and that is deliberate.** They belong to
  `.claude/agents/` and they change; the four things SPEC does state are
  constraints a kernel or a hook can enforce.

## Why SPEC and RATIONALE are separate

Because mixing them was tried and did not work: an agent built a checker from the
older document and 16 fixtures **all exited 2** — the document declared one CLI
flag and the kernel passes three. Prose describing a contract is not a contract.

And separating them has its own risk, which has already been paid once: the
four-layer structure and the rule-placement table were left in RATIONALE, and
engagement **shipped carrying zero rules**. So `spec-coverage` now requires every
section describing a mechanism to pin at least one thing — a section with no pin
is a section nobody wired.
