# Which document to read

The code and executable tests establish current implementation behavior. SPEC
states the intended mechanisms and invariants with code links; a pin confirms a
symbol exists, not that every sentence is correct. When they disagree, inspect
the behavior and record whether code or explanation needs repair.

## Choose a reading route

| Reader | Route |
|---|---|
| First use | Run the [standalone demo](../examples/first-proof/README.md), then follow [GETTING_STARTED](GETTING_STARTED.md) to adopt it and complete one real task. Use [CODEX](CODEX.md) for Codex or dual-host setup. |
| Daily use | Follow [USING](USING.md) for tasks, updates and maintenance. Use [FEATURES](FEATURES.md) to find a capability or command and [REFERENCE](REFERENCE.md) to interpret its evidence and limits. |
| Evolving requests and project design | Follow [USING](USING.md#the-seven-steps-of-a-task) for intake/reconciliation, existing-document reuse and independent review; [SPEC](SPEC.md#request-evolution-and-project-design-documents) owns the mechanism and executable schemas own JSON fields. |
| Framework development | Start with [AGENTS](../AGENTS.md) and the map below, read [SPEC](SPEC.md) alongside implementation/tests, and use [SYNC](SYNC.md) for the canonical-source/public-release boundary. Consult the historical evidence below for dated rationale. |

## Repository map

| Location | Responsibility |
|---|---|
| [bin/v4](../bin/v4), [kernel/](../kernel/) | CLI entry and shared task, evidence and policy mechanisms |
| [detectors/](../detectors/), [checkers/](../checkers/) | Claim discovery and executable checks |
| [hooks/](../hooks/), [.claude/](../.claude/), [.codex/](../.codex/), [.agents/](../.agents/) | Host adapters, prompts, roles and skills |
| [.v4/](../.v4/) | Tracked configuration, registries, facts and declared proof integration; public selection is governed by [the release policy](../publishing/public-files.json) |
| `<git-common-dir>/v4/`, `.v4/ledger_export*`, `.v4/risks/<claim>.json`, `.v4/risks/repo/<kind>.json`, `.v4/deferred/<claim>.json` | Local runtime/history and private operational records, generated when used and excluded from public distribution; see [ledger storage and trust limits](REFERENCE.md#ledger-and-evidence-lifetime) |
| [tests/](../tests/), [examples/](../examples/) | Regression tests, checker/detector fixtures and runnable examples |
| [.github/](../.github/) | CI/release workflows, issue templates and monitor instructions |
| [tools/](../tools/), [publishing/](../publishing/) | Maintenance/release utilities and public-file selection policy |
| [docs/](.) | Operating guides, mechanism references and dated evidence; private launch planning is distinguished below |

## Current reference and operating instructions

| Document | Purpose | Verification |
|---|---|---|
| [SPEC.md](SPEC.md) | Mechanism/contract reference, with dated historical examples distinguished from current behavior | Implementation pins, command/flag checks, registry/count checks and semantic source review |
| [CODEX.md](CODEX.md) | Claude/Codex host installation, identity, hooks and permission boundaries | Native disposable-adopter tests and generated-asset checks |
| [USING.md](USING.md) | Operating sequence for a task | CLI examples and source review; examples do not become a second contract |
| [FACTS.md](FACTS.md) | Facts format, provenance and updates | Executable grammar, citation checks and review of vocabulary completeness |
| [REFERENCE.md](REFERENCE.md) | Detailed derived explanation of mechanisms, evidence and limits | Recheck against code/SPEC when relevant sources change |
| [FEATURES.md](FEATURES.md) | Derived feature and command map | Source links and registry/CLI comparison |
| [GETTING_STARTED.md](GETTING_STARTED.md), [繁體](GETTING_STARTED.zh-TW.md), [简体](GETTING_STARTED.zh-CN.md) | First adoption and one real task | Run the instructions in an isolated repo; preserve existing host settings |
| [SYNC.md](SYNC.md) | Canonical private source and public release procedure | Export policy, distribution tests and public manifest checks |
| [../README.md](../README.md), [繁體](../README.zh-TW.md), [简体](../README.zh-CN.md) | Derived introduction, supported fit, demo and limitations | Included in CLI documentation checks; claims still need semantic review |
| [../CONTRIBUTING.md](../CONTRIBUTING.md) | How public contributions reach canonical development | Release policy and maintainer practice |

README, FEATURES and REFERENCE are derived summaries. They do not override the
implementation or introduce separate schema/ship contracts. Update them in the
same canonical source change when behavior moves.

## Generated and host-facing instructions

| Source | Purpose |
|---|---|
| [../CLAUDE.md](../CLAUDE.md) | Generated doctrine from `kernel/doctrine.py` and this repo's registries; `registry-consistency` compares the generated block |
| [../.claude/agents/](../.claude/agents/), [../.claude/commands/](../.claude/commands/) | Host workflow prompts for the roles described in SPEC; their presence is not proof of execution or independence |
| [../.github/monitor/](../.github/monitor/) | Monitor instructions and host permissions, with the specific enforced CLI guards distinguished from discipline |
| [../.v4/surface/INTEGRATION.md](../.v4/surface/INTEGRATION.md) | Surface runner receipt contract and optional Playwright adapter; shipped to adopters, exercised by the dedicated browser acceptance lane |
| [../AGENTS.md](../AGENTS.md) | Where maintainers edit and how they preserve the source/release boundary |

## Historical evidence and design rationale

[EVIDENCE.md](EVIDENCE.md), [RATIONALE.md](RATIONALE.md) and
[DOGFOOD_LOG.md](DOGFOOD_LOG.md) retain the observations and decisions of their
recorded revisions. They are not fresh measurements of the current checkout.
Read the stated method, date, sample and caveats before reusing a number.
Some cited development history is not present in the public distribution;
current executable evidence must not require that private history.

The runnable [first-proof example](../examples/first-proof/README.md) gives current
behavioral checks. Saved demo/video evidence is versioned history; source hashes
must match the declared snapshot before it is used as evidence for that version.
Campaign plans and account handoff documents under `docs/launch/` are private
operating material unless the publication policy explicitly selects a file.

```sh
./bin/v4 --repo . accept --here --docs
./bin/v4 --repo . doctrine --check
```

A successful mechanical check does not replace reading a changed explanation
against the implementation. The document-review record for a maintenance pass
states its actual coverage and distinguishes fixtures, historical records and
current instructions.
