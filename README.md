**English** · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md)

# vibeproof

### Your agent says done. What proves it?

**Does it work? Is the fix proven? Does the proof still apply after another edit?**

vibeproof connects Claude Code and Codex tasks to executable checks, repair evidence and current results. Start with a deliberately broken discount: **100 − 20 returns 120. The tests still pass.**

[![Three moments: green tests with a wrong total; a repair verified before and after; another edit makes the old evidence STALE.](docs/launch/assets/v4/images/hero-en.png)](docs/launch/assets/v4/demo-en.mp4)

[Watch the narrated demo](docs/launch/assets/v4/demo-en.mp4) · [Run it yourself](#run-it-yourself) · [Use it in your repo](docs/GETTING_STARTED.md)

**Best first fit:** an existing Git repo with a real Python test suite, where you already use a coding agent and spend time checking its work. Claude Code and Codex adapters share the kernel; native host validation is on macOS. [Host setup and limits](docs/CODEX.md)

## One change. Three things worth checking.

### 1. Green tests. Wrong result.

The cart should total **80**, but returns **120**. An unrelated `2 + 2` test stays green. The ordinary Python checker reports:

```text
the suite passed and executed none of the 1 changed file(s):
  checkout.py
```

That exposes missing execution evidence. It does not establish that every changed line was tested: importing a file can pass this ordinary check.

### 2. A fix that earns its proof.

A new regression test calls `total(100, 20)` and expects `80`. It fails on the broken implementation. After the fix, the review checker verifies **the same test fails before the fix, passes after it, and executes the target function**. An unrelated green test is refused as repair evidence.

This is proof for the demonstrated repair. The test still needs a meaningful assertion; it does not prove every requirement or branch.

### 3. Another edit. The old proof expires.

The demo records a real checker attempt in a temporary ledger, then changes `checkout.py` again. The kernel reports:

```text
ANSWERED → STALE
its subject moved: checkout.py
```

The old successful attempt stays in the history. It no longer counts as current evidence. `STALE` means revalidation is needed, not that a new bug has already been found. Restoring the exact checked source makes that evidence applicable again.

## Run it yourself

You need **Git and Python 3.12+**, on macOS or Linux. Native Windows is not validated. No packages, API key or coding-agent subscription are needed for this demo.

```sh
git clone https://github.com/howardc38/vibeproof.git
cd vibeproof
python3 examples/first-proof/run.py
```

The script creates and removes a temporary repo. After cloning, it runs offline and does not install the framework into your project. **Expected FAIL output is part of the demonstration; success ends with `DEMO VERIFIED`.**

The images and videos replay measured output from the **2026-09-14 constructed example**, including real checker execution and kernel evidence-state queries. Narration is synthetic and the presenter portrait is fictional. These are not a captured AI conversation or a complete installation/ship run. [Source, transcript and controls](examples/first-proof/README.md)

## Why keep it for the next task?

| When the work gets messy | What stays connected |
|---|---|
| The agent changes code after a successful check | Current input hashes determine whether the earlier evidence still applies |
| A reviewer finds a defect | The finding can be tied to an executable repair test, instead of ending at “fixed” |
| A worker reports completion | Maintenance can run the checker in the linked repair worktree and read back the original finding's state |
| A finding can wait | Report-only claims and attempts remain in the ledger; task policy determines whether they block |
| A review is interrupted or the source changes | Maintenance distinguishes partial, complete and stale review results |

Recurring maintenance needs a real host-scheduled job; repairs need an authorized scope. A delivered or acknowledged Telegram notice does not close a finding. [Maintenance operation](docs/USING.md#periodic-maintenance-and-findings)

## Use it in your repo

[Follow the adoption guide →](docs/GETTING_STARTED.md)

You provide the requested outcome, allowed files, real test command and decisions about unresolved risks. The guide includes a pasteable agent prompt. Full installation adds checkers, detectors, fixtures, hooks and prompts, verifies fixtures, and asks you to confirm facts about your repo. It takes longer than the short demo.

**Choose your host:** installation defaults to Claude Code. Use `--hosts codex` or `--hosts both` for Codex. `--activate-hooks` merges framework handlers while preserving unrelated settings; Codex hooks also need review and trust in the host. [Task binding and permissions](docs/CODEX.md)

| Work to do | Claude Code | Codex |
|---|---|---|
| Make one scoped change | `/run` | `$vibeproof-run` |
| Coordinate tasks in separate worktrees | `/wave` | `$vibeproof-wave` |
| Review current code through applicable lenses | `/sweep` | `$vibeproof-sweep` |
| Inspect findings and coordinate maintenance | `/maintain` | `$vibeproof-maintain` |

## What else is included?

| Capability | What it adds |
|---|---|
| Scope checks | Early checks on supported edits, plus checks against the resulting Git diff |
| Test-change checks | Reports live-test count reductions and selected expectation/shape changes; not complete assertion-quality analysis |
| Runtime proof | Runs your declared trigger and queries your declared truth owner for this run's result |
| UI proof | Requires fresh runner case results; an optional Playwright adapter supports browser proof |
| Review lenses | Questions about request fidelity, design, security and test sufficiency; reviewer judgment is still required |
| Structural and credential checks | Selected error-handling, external-write, secret, signature and reference patterns; coverage varies by language and facts |
| Checker registration | Red/green/bypass fixtures and repeatability checks before accepting a checker |

Ordinary changed-file execution tracing is Python-only. Executable review repair paths include Python, Go and Node/V8, depending on the runner. Verified same-file Python function/method renames can preserve the original finding. Structural checks support Python, Go and TS/JS to different depths, with limited Rust support.

[Full feature map](docs/FEATURES.md) · [Technical reference](docs/REFERENCE.md) · [Facts format](docs/FACTS.md)

## Know what the result means

- `SHIP` is the configured task decision. It does not deploy code or guarantee that every requirement was met.
- Some findings initially report rather than block; test deletion is report-only by default. A finding on the standing `repo-review` task does not automatically block another task.
- Hooks cover supported host payloads and can stand down when state is unavailable. Stop checks interrupt once per stop continuation; later independent turns can be checked again. Hooks are not a sandbox.
- Local ledger records and hashes are not an immutable external trust service. Accepted-risk paths exist, including agent signatures.
- Prompts, review lenses and recorded completion do not prove independent judgment. Business correctness and security still need suitable tests and human decisions.

[Exact limits and exit codes](docs/REFERENCE.md) · [Full workflow](docs/USING.md)

## Try one real change

[Tell us what happened](https://github.com/howardc38/vibeproof/issues/new?template=first-run.yml): what it caught, what it got wrong, and whether you would keep it enabled for the next task. Sanitized logs are enough; private code and credentials are not needed.

Run the framework's own tests with `python3 tests/run_without_silent_skips.py`.

Maintaining vibeproof? Edit the canonical development repo; see [contributing](CONTRIBUTING.md) and [release synchronization](docs/SYNC.md).

MIT licensed. [License](LICENSE) · [Implementation specification](docs/SPEC.md)
