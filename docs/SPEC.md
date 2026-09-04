<!-- IMPLEMENTATION SPEC.  Imperative, terse, no history.

Why this document is written and why not to write it as prose:
an agent built a checker from the earlier design document and all sixteen of
its fixtures exited 2, because that document declared one CLI flag where the
kernel passes three. Prose about a contract is not a contract.

So: every mechanism that exists in code is pinned to it, and
checkers/design_pins.py resolves each pin. Rename a symbol and this file fails
a check rather than quietly becoming wrong.

Reasons live in docs/RATIONALE.md.  What building it exposed lives in
docs/DOGFOOD_LOG.md.  Neither is needed to implement this file.
-->

# vibeproof — implementation spec

**A task is a set of questions. Each question is answered by a separate program. The kernel runs that program itself, reads its exit code itself, and records the answer. When they are all answered, it ships.**

No steps, no cycles, no prose contracts, no phase documents.

---

## 0. Four things to know before reading this

1. **Where code already exists, this document states the contract and the invariants; the code is the implementation.**
2. **Where it says "not a guarantee", do not upgrade it.** The predecessor to this document made three "structurally impossible" claims and all three were false.
3. **What is not built is in §10.** Anything that appears here and not in §10 is built and pinned by a test.
4. **This document is governed by two checkers.** Nobody has to be believed when they say they checked:
<!-- pinned: checkers/design_pins.py::main -->
<!-- pinned: checkers/spec_coverage.py::main -->

| Checker | What it asks |
|---|---|
| `design-pins` | Does the symbol each `<!-- pinned: -->` names exist |
| **`spec-coverage`** | (a "section" is every `##` **or** `###` heading, not only `##`) ① does every `v4 <command>`, every path and every claim kind exist — **and the other direction too**: does the document mention the commands, hooks, agents, commands and engaged kinds that do exist; ② does every section describing a mechanism pin anything at all; ③ references to things that have been deleted (`dead_references`); ④ numbers in prose the repo can settle (`counted_claims`); ⑤ whether a flag shown here is one that command takes, and whether one taking a path was given a path value (`flags_resolve`); ⑥ whether anything on the "not built" list is in fact built (`unbuilt_list_is_honest`) |

**The second of those — "does it pin anything" — is the most important and the weakest.** It cannot answer "is this passage correct"; it answers only "did anybody wire this up". And **three times the answer was no** — once a whole section had been left in another document, once a command this file described in detail was never written, and once a table this file named had nothing in the code writing to it.

---

## 1. The data model

```
task    the unit of one change. Carries the request verbatim, scope globs, base commit
 └ claim   one question, what it is about, and which program answers it
    └ attempt  the kernel ran that program once. The exit code comes from the OS
```

**The primary key is `(task, claim, attempt)`.** No phases, no cycles, no steps.

### Claim identity
<!-- pinned: kernel/hashing.py::claim_id -->

```
claim_id = sha256(task_id ‖ kind ‖ file ‖ symbol ‖ variant)[:16]
```

| In | Why |
|---|---|
| `task_id` | The ledger is shared across worktrees. Without it, one site touched by two tasks collapses into a single claim, and one worker's PASS answers the other's — over two files whose contents differ |
| `symbol` (the enclosing function or class name) | **Not the line number.** A line number in the id means adding an import creates a fresh batch of claims and a batch of orphans |

| Out | Why |
|---|---|
| `line` | Display only |
| `subject_refs` | `v4 scope widen` changes it, and one defect has to keep one identity across a widen |

### Subject: what a claim is about
<!-- pinned: kernel/hashing.py::subject_digest -->

```json
[{"kind": "file",    "path": "app/chat/notifier.py"},
 {"kind": "attempt", "claim": "<the id of another claim>"}]
```

**`subject_refs` is written by the kernel — not by a detector, not by an LLM.** Choosing the subject is choosing which bytes verify your own answer.

The `attempt` ref exists because a `surface` claim depends on a `runtime` observation, and a file hash cannot express that: re-running the runtime check changes no file. The kernel derives it from `depends_on_kind` in `claim_kinds.json`.

⚠️ **It takes the first claim of that kind, not all of them.** When a task has more than one claim to depend on,
<!-- awaiting-first-user: depends_on_kind -->

re-running the others does not make it STALE. **No kind uses `depends_on_kind` today**, so this has never been exercised — but the question has to be answered before one does, not after it is discovered.
<!-- pinned: kernel/derive.py::subject_refs_for -->

---

## 2. The detector contract

**A detector has the same shape as a checker. One mechanism, two uses, and one less thing for the kernel to know.**

### In

```
--subject <path>    always
--facts   <path>    when there is a facts file
--out     <path>    always
```

The `--subject` JSON **has the same shape as the checker's** (§3), but the
claim-related fields are empty — when a detector runs, the claim does not exist
yet:
<!-- pinned: kernel/derive.py::run_detector -->

```json
{"claim_id": "", "claim_kind": "", "task_id": "", "diff_base": "",
 "symbol": "", "variant": "", "params": {},
 "repo_root": "/abs/path/to/worktree",
 "subject_refs": [{"kind": "file", "path": "app/x.py"}]}
```

**Only `repo_root` and `subject_refs` carry anything** — `subject_refs` is the
set of files to scan this round. A detector that needs a diff base writes its
own `or "HEAD"`. **This passage did not exist before**, and somebody building a
detector from "the same three flags as a checker" would read fields that are
permanently empty strings.

**All three must be accepted by `argparse`.** An undeclared flag makes argparse
`exit 2`, and 2 is not in the exit table → the kernel reads ERROR → not one
claim gets answered.

### Out: one line at a time on stdout
<!-- pinned: kernel/derive.py::parse_claim_lines -->

```
V4-CLAIM: kind=<enum> file=<path> symbol=<name> variant=<enum> line=<int> note="<free text>"
```

| Field | Rule |
|---|---|
| `kind` | Must be in `.v4/claim_kinds.json`. If not, the kernel **rejects the whole line** rather than ignoring the field |
| `file` | Must be inside the task's scope. If not, rejected |
| `symbol` | The enclosing function or class. `<module>` at top level |
| `variant` | An enum the kind defines for itself |
| `line` | Display only, not part of identity |
| `note` | Free text. **Not part of identity, not part of checker selection, display only** |

**Any field beyond those six is rejected.** Not ignored — a field the kernel
does not know is the emitter deciding something it does not get to decide.

**Only `kind` is required; the other five may be absent.** An unconditional
detector (`always_*.py`) emits exactly one field: `V4-CLAIM: kind=test` — a
repo-scoped claim has no file and no symbol, because it is about the whole repo.
Treating all six as required means these claims are **rejected by the kernel's
own parser**, so every task derives zero claims, and the measured run in §11 can
never be reproduced. "Reject unknown fields" and "require known fields" are two
different things.

### Detector exit
<!-- pinned: kernel/derive.py::_did_not_run -->

**Only `0` means it finished (finding nothing is also 0). Any other value means
the detector itself broke.**

⚠️ **It is not a ship gate, and that has to be said plainly.** The kernel writes
a `detector_run` event with `ran=False` and `ship` prints it on the "which
detectors did not run" line — but ship's predicate is "nothing blocked and the
chain verifies", and a detector that crashed raised no claims at all, so it does
**not** hold a ship. This is the same sentence as §4's "always printed, not a
gate". **Do not read it as a protection** — a protection would have to be built,
not written here in a form §4 contradicts.

### Detectors have a gate too
<!-- pinned: kernel/register.py::verify_detector -->

```
v4 --repo . verify-detector  --detector <path> --fixtures <dir>   ← reports only
v4 --repo . register-detector --detector <path> --fixtures <dir>   ← reports, then records

red/   ≥3 cases, each must emit at least one V4-CLAIM
green/ ≥3 cases, each must emit none
```
<!-- pinned: kernel/register.py::register_detector -->
<!-- pinned: kernel/register.py::UNCONDITIONAL -->

**Passing a gate has to leave a trace, or nothing can require it.**
`verify-detector` existed all along, and its verdict **was printed and gone** —
so this section's "a conditional detector must pass its gate" was enforced by
nothing at all: `derive` ran every file in `detectors/`, `registry-consistency`
matched names and not hashes, and `claim.detector_sha` was read at derive time
from **the file that was about to run**, with nothing to compare it to.

`register-detector` writes `.v4/detectors.json` (§8), and `derive` reads it:
<!-- pinned: kernel/layout.py::DETECTORS -->

```
not registered   → does not run, records refused=unregistered, ship lists it as not run
disk sha differs → does not run, records refused=detector-changed
always_*         → exempt, reason below
```

**The same rule as a checker's exit 6: the bytes that run are the bytes the gate
passed.** It matters more on this side, because this side is quiet: a checker
that does not run leaves a claim unanswered (loud), a detector that does not run
means the claim never existed (silent).

⚠️ **A detector may not borrow its checker's fixtures — unless the two share one
analysis.** They ask different questions, so a checker's green case is often a
detector's red case: the since-removed `bundle-secret` checker's greens were all
front-end files with no leak in them, and every one of them should have made the
detector fire.
Measured: four of five greens "passed" only because they were not mini-repos, so
path resolution landed outside `ui_globs` — the criterion never ran.
<!-- pinned: kernel/register.py::_shares_logic -->

But **this rule used to be written too absolutely**, and the code has two places
that by design do not follow it: the `external-write` and `fail-closed`
detectors and checkers both call the same `kernel/analysis/` module, and its
docstring says they can never disagree. Under that shape, sharing fixtures is
not a shortcut — **it is the guarantee**. So the gate refuses only the ones that
do **not** share the analysis.

### The self-trigger test
<!-- pinned: kernel/register.py::self_trigger -->

```
tests/fixtures/<name>/self_trigger/<case>/before/   code that raises the claim
tests/fixtures/<name>/self_trigger/<case>/after/    the same code, claim answered
```

**`before` must fire, `after` must not.**

This is not hypothetical: the first `external-write` criterion matched any
outbound symbol, GET included — so **the read-back added to answer a readback
claim raised a readback claim of its own. Endlessly, and visible only by trying
it.**

`after` still firing means the fix is not a fix, and the task can never ship.

⚠️ **A detector needs its own fixtures and cannot borrow the checker's.** They
ask different questions: a checker asks "is there a problem in this code", a
detector asks "does this change need this checked". The since-removed
`bundle-secret` checker's green fixtures were front-end files with no leak, and
the same files should all have made the detector fire. Sharing makes half the
cases wrong by definition.

**A gate on the checker and none on the detector is backwards** — the detector
decides what is available to be checked at all. A detector that emits nothing
and a detector that scanned and found it clean look identical to the kernel:
change the suffix set from `.py` to `.pyx` and every task passes, while the ship
report goes on saying it ran.

⚠️ **An unconditional detector (`always_*.py`) cannot be gated and must not
pretend to be.** They emit the same line every time, so the "must not fire" case
cannot be expressed. They are three lines long and their behaviour is decided by
`claim_kinds.json` — the gate belongs on that kind's checker. **A conditional
detector must pass its gate.**

Do not leave 1 and 2 undefined: a detector that crashed but exited 1 reads as
"scanned, found nothing" — fail-open, and silent, at the one step that decides
what gets checked.

---

## 3. The checker contract

### In

The same three flags as a detector. The `--subject` JSON:
<!-- pinned: kernel/runner.py::run_checker -->

```json
{
  "claim_id": "…", "claim_kind": "external-write", "task_id": "…",
  "repo_root": "/abs/path/to/worktree", "diff_base": "<commit>",
  "subject_refs": [{"kind": "file", "path": "app/x.py"}],
  "symbol": "notify", "variant": "readback",
  "params": {"scope_globs": ["app/**"]}
}
```

**`--facts`** — read-only facts the kernel dumps beforehand (the outbound symbol
list, entrypoint patterns). **A checker cannot read the ledger.**
<!-- pinned: kernel/config.py::RepoConfig -->

| | |
|---|---|
| Where it comes from | `<repo>/.v4/facts.<repo-name>.json` (glob `.v4/facts*.json`), run through `kernel.facts.validate`. **A malformed file is an error, not an empty one** — reading it as empty silently disarms every detector that depends on it |
| **May be absent** | A checker **must still work with no facts** (carrying a generic vocabulary of its own). **But absence has to make it say so** |
| How absence is expressed | An empty or malformed table → **exit 4, not exit 0**. With no vocabulary to scan for, every repo reports clean |
| How "there genuinely are none" is said | `absent: {"<table>": "<how it was checked>"}`, at least 20 characters, and the table must really be empty. <!-- pinned: kernel/analysis/facts_grammar.py::ABSENT_KEY --> A bare `[]` is still refused. The distinction matters because refusing empty tables outright stopped the wrong thing: a library with genuinely no outbound writes **could not adopt at all**, and the message told it to invent rows. Silence is still refused, and absence became a sentence with an author, in the diff, that the next person can re-run |
| Who confirms it | Absences written by `v4 install` carry an `AUTO:` prefix, `doctor` keeps reporting them, and **`v4 ship` is HELD while one remains**. The gate moved from adopt to ship — the gate that stopped the wrong thing could not stop it, the one that stops the right thing can |
| Fixtures | A fixture directory may carry its own `facts.json`. Without one, the repo's is used. **A fixture should use its own** — otherwise it tests one repo's vocabulary rather than your rule |

> 【measured】This was once a silent failure: `config.py` looked for a hardcoded `facts.json` while the repo emitted a filename carrying its own repo name, so `cfg.facts` was permanently `{}`. The same checker against the same real defect: **exit 1 with the table, exit 0 without**, and production had been running the without side the whole time.

**`--out`** — structured results are written here.
<!-- pinned: kernel/runner.py::record -->

| | |
|---|---|
| **Must be valid JSON** | Otherwise the kernel **overwrites the exit code with `5` (ERROR)** — a checker that writes plain text and exits 0 is recorded as broken. The kernel only verifies that it parses; **it does not read the content** |
| Where it goes | Stored as a `checker_out` event bound to the claim. `v4 status --detail` reads it back |
| Schema | Yours. The kernel does not parse the content. **Do not rely on the kernel to read your prose** |

**Empty `subject_refs` means this is a repo-scoped claim**: work out which files
to scan from `repo_root` and `diff_base` yourself (`git diff --name-only <base>`
plus `git ls-files --others --exclude-standard`). Returning exit 4 blocks the
claim forever.

### Out: the exit code
<!-- pinned: kernel/runner.py::PASS -->

| exit | Meaning | What the kernel does |
|---:|---|---|
| **0** | PASS (including "scanned, nothing to verify") | claim answered |
| **1** | FAIL | claim unanswered, the whole of stdout goes into the ledger |
| **4** | **UNSUPPORTED — I cannot verify this**. Five checkers print this as `CANNOT VERIFY` (`external-write`, `fail-closed`, `runtime-proof`, `scope`, `secret`; `dependency` did too, until it was removed); same meaning, same exit code | **Not an answer.** Listed in the ship report |
| **≥5** | ERROR / timeout | Neither PASS nor FAIL |

> Two names for one thing, because the two sets of checkers were written on
> different days. Both are kept rather than one renamed, because these numbers
> are an OS contract — they do not drift; what drifts is whether a reader finds
> them. Seeing `CANNOT VERIFY:` is seeing exit 4.

**A definite finding and some unreadable files at once: the definite one wins.**
Report `1` when something was caught; do not return `4` because a few other
files did not parse. The other way round lets one syntax error silence a real
finding.

**6, 7 and 8 are assigned by the kernel, not returned by a checker.**
`CHECKER_TAMPERED = 6` (disk sha differs from the registered one),
`SUBJECT_MOVED = 7` (the two hashes around the exec differ), `TIMEOUT = 8`. All
three are written into `attempt.exit_code` by `runner` as diagnosis — **and
`state` keeps them apart: 5 derives `CHECKER_ERROR`, 6 `CHECKER_TAMPERED`, 7
`SUBJECT_MOVED`, 8 `TIMEOUT` (`_EXIT_STATE`), and only a code the kernel does
not name falls back to `CHECKER_ERROR`.** (This sentence used to say `state`
treated all of them alike. It did, and `v4 status` printed `CHECKER_ERROR` for
four different failures.) Diagnosis and state derivation are two things; this
table is the first, the state table in §4 is the second.

**`1` and `≥5` must stay apart.** Merged, "the checker broke" and "the code has
a problem" look the same, and an agent goes off editing code to fix a tool bug.

**There is no `NOT_APPLICABLE`.** Applicability is expressed by the detector —
no site found, no claim. A "not applicable" exit code becomes another spelling
of `return {status:'unsupported'}`.

### Registration: the red fixture gate
<!-- pinned: kernel/register.py::verify_checker -->

```
v4 register --id <id> --checker <path> --fixtures <dir> --kinds <a,b> [--timeout <seconds>]
```

The kernel runs it itself, and all four must hold:
<!-- pinned: kernel/register.py::MIN_BYPASS -->
<!-- pinned: kernel/register.py::BYPASS -->

```
red/    ≥5 cases, each must exit 1
green/  ≥5 cases, each must exit 0
bypass/ ≥3 cases, each must exit 1     ← the third colour, required, not a copy of red
the same input twice → identical exit code and byte-identical stdout
```

**`bypass/` does not ask what red asks.** Red asks "can this checker tell two
states apart"; bypass asks "can an author who knows the rule get around it" —
the same defect, rewritten to look as though it were avoided. The criteria and
how to write them are in §12. **This colour was not in this section before**,
and somebody building `register` from this section would build a gate missing
the colour that found thirteen real evasions on its first run (§10).

**Fixtures come in two shapes:**

| Shape | When |
|---|---|
| A single file | The checker reads the subject file (`fail-closed`, `design-pins`) |
| A directory containing `.v4/config.json` | The checker judges a whole repo (`test`, `scope`). **The directory becomes its own repo_root** |

⚠️ **For a directory fixture, if your checker calls git, that case's own
`<case>/.v4/fixture.json` is required, not optional.** The kernel only runs `git
init` and commits when `fixture.json` is present; without it your repo_root is
not a git repo, and the next passage of §3 tells a repo-scoped checker to work
out its subject with `git diff`. **The failure is a baffling exit 4 or 5, not a
clear message.**

⚠️ **The registration gate structurally cannot reach the "empty `subject_refs`"
branch.** `_subject_for` always fills in every file for a directory case. So a
repo-scoped checker's fallback path **cannot be covered by the fixture tests** —
either do not use that branch, or cover it with a separate unit test, knowing the
gate does not.

A directory fixture may carry a `<case>/.v4/fixture.json` (one per case, not the
one at the repo root):

```json
{"params": {"scope_globs": ["app/**"]},
 "committed": ["base.py", ".v4/config.json", ".v4/fixture.json"]}
```

The files `committed` lists are `git init`-ed and committed in a **temp copy**;
the rest stay as working-tree changes. **Do not create nested git repos inside
the repo** — git neither tracks them nor cleans them up.

**This gate is not a boundary.** An author who knows the fixtures can write a
checker that passes them exactly and catches nothing. It raises the cost; the
anchor is a person reading the checker's diff.

---

## 4. Kernel

### Running one claim
<!-- pinned: kernel/runner.py::run_checker -->

```
① verify the disk checker sha == the registry sha   differs → exit 6, does not run
② hash subject_refs
③ exec the checker itself
④ read the exit code
⑤ hash subject_refs again               differs → exit 7 (changed underneath)
⑥ write to the ledger
```

**① closes the cheapest exit: edit the checker until it agrees.** The checker's
program sha (itself **plus what it imports**) is also in the staleness key —
closing a hole in a checker should expire the old PASSes.

**③ must use the kernel's own interpreter (`sys.executable`), never
`"python3"`.** The latter resolves at run time against a PATH a worker can
control, so a shim placed in front decides **every** checker's exit code.
Measured: a two-line `#!/bin/sh\nexit 0` turned a FAIL into a PASS. Verifying the
checker's bytes in ① and then handing them to an interpreter somebody else
chooses is verifying half of it.

### State: a query, not a column
<!-- pinned: kernel/state.py::claim_state -->

**There is no `status` column.** Every question recomputes it from the attempts,
the current hashes, and whether anybody signed.

| State | How it is derived | Terminal |
|---|---|---|
| `OPEN` | No attempt, or the latest exited 1 | ✗ |
| `STALE` | The latest exited 0, but the key no longer matches | ✗ |
| `ANSWERED` | The latest exited 0 and everything matches | ✓ |
| `UNSUPPORTED` | The latest exited 4 | ✗ |
| `CHECKER_ERROR` | The latest exited 5 — the checker crashed (or exited a code the kernel does not name) | ✗ |
| `CHECKER_TAMPERED` | The latest exited 6 — its registered bytes are not its bytes | ✗ |
| `SUBJECT_MOVED` | The latest exited 7 — the tree changed while it ran | ✗ |
| `TIMEOUT` | The latest exited 8 — it did not finish | ✗ |
| `RISK_ACCEPTED` | Signed, and the key has not moved | ✓ |
| `RETRACTED` | A full scan no longer raises it, and the files it was about are gone | ✓ |

**Besides claim states, the ship report always prints:** which detectors ran and
which did not · **whether the hooks have ever actually fired** (never =
`DEGRADED`) · how many widens and what percentage of the repo they reach · how
many signatures per kind.
<!-- pinned: kernel/lifecycle.py::ship -->

**`DEGRADED` measures whether a hook fired, not whether it is installed.** A hook
installed and never fired is the same as no hook, and only the events tell them
apart.

**`NEEDS_ENGAGEMENT` is not in this table.** It is not a claim state — it is
what `v4 check` returns before running a checker, so `v4 status` never shows it.
It blocks the work, not the ship, and "blocks the work" is implemented as
`check` skipping it.
<!-- pinned: kernel/lifecycle.py::check -->

**Why `RETRACTED` exists:** without it, `git mv` on an in-scope file is fatal —
the old claim's subject disappears → the checker returns exit 4 → `UNSUPPORTED`
is deliberately non-terminal → **HELD forever, with no command that clears it**.
Renaming a file should not require a signature.
<!-- pinned: kernel/derive.py::_retract_orphans -->

Two guards, and the second is the point:

| | |
|---|---|
| The claim's detector must have **run to completion** this round | A crashed detector emits nothing. Reading silence as "no longer applies" lets a broken detector retract all of its own claims and ship |
| The files it is about must **all be gone** | Did the detector merely narrow its rule and stop raising it? The claim stays — the code it is about is still there, waiting for a verdict |

**When the kind itself is removed, the claim stays too, and `check` reports it as
exit 4.**
<!-- pinned: kernel/lifecycle.py::check -->

`cfg.checker_for` refuses an unregistered kind, and what it says is right: **a
detector emitting an unregistered kind must not be guessed at.** But `check` is
looking at rows that already exist in the ledger — nothing is emitting, and a
kind can be removed after its claims were raised. Measured: four of them on
`repo-review` (`dep-provenance`, `dependency`, `request-coverage`,
`secret-chain`) made **all 257 of its review findings unverifiable**, because
that `ConfigError` was thrown out of the per-claim loop, through `cmd_check`, to
the top level.

So it is caught per claim, reported as exit 4, and the loop continues. **Not
retracted and not answered**: the second guard above refuses to retract a claim
that FAILed, precisely so that "delete a rule" does not mean "delete a finding" —
and answering it here is the same act through a different door. Exit 4 already
means "nobody can verify this", and it is deliberately non-terminal, so it keeps
holding, and `v4 doctor` lists it on the `unanswerable kinds` line.

⚠️ What is printed has to say what happened *here*. `cfg.kind`'s sentence is
about a detector emitting, which is true where it is raised and false where it is
caught — copying it across is a sentence larger than its own situation.

### Staleness key
<!-- pinned: kernel/state.py::_staleness_key -->

**Six things**, and any one of them changing makes the claim STALE:

```
subject    the digest of subject_refs (file → sha256; attempt ref → that claim's latest attempt id)
config     the sha of .v4/config.json      ← test_command is the sole oracle for a test claim
checker    the sha of that checker **and of everything it transitively imports inside this repo**
detector   the sha of the detector that raised it
worktree   only for kinds with staleness=repo
facts      the sha of the facts table, for a checker that scans against it ("" for the rest, so an unrelated edit costs those nothing)
```

**`checker` is not a file, it is a program.**
<!-- pinned: kernel/hashing.py::program_sha -->
There are 7 main-only checkers — the entry file defines `main` and nothing else,
so the judgement lives outside it.
(An earlier version said 13 "thin CLIs", and "thin" has no reproducible
definition — only-defines-`main` gives 7, has-a-same-named-analysis-module gives
9, under a hundred lines inline gives 14, imports `kernel.analysis` gives 17.
Four answers to one number, which is exactly the shape this checker exists to
end, so this is the one measure that needs no judgement, and `counted_claims`
settles it from today.)
Hashing only the entry file makes "a different program answered" true of the
argument parser and false of the judgement. Measured: after one class of false
positive was fixed in `kernel/analysis/dangling_ref.py`, the verdicts went from
42 to zero, and **not one recorded PASS expired**.

`disk_sha` (the entry file's own bytes) still exists separately and answers a
different question — "are the bytes executing the ones that were registered"
(exit 6 `CHECKER_TAMPERED`). Two questions, two answers, not to be merged.

**`detector` is the same argument as `checker`:** narrowing a detector should
expire what it raised under the old rule, exactly as changing a checker should
expire what it passed. Without this, `claim.detector_sha` is a column that is
written and never read.

**`worktree` is not HEAD, it is content:**
<!-- pinned: kernel/hashing.py::worktree_digest -->

```
sha256(HEAD ‖ git diff HEAD --binary ‖ path+sha of every untracked file)
        excluding 7 kernel-written paths (see KERNEL_WRITTEN): .v4/risks/**  ·
              .v4/chain_head.json  ·  .v4/ledger_export.jsonl  ·  .v4/installed.json
              ·  .gitignore  ·  .v4/home  ·  .v4/deferred/
              plus whatever derive_exclude names in config.json
```
<!-- pinned: kernel/hashing.py::KERNEL_WRITTEN -->

**`.v4/checkers.json` is deliberately not in that list.** The by-name branch
exempts a path whatever its content, and that file maps each kind to the program
that judges it — so a hand edit repointing a kind at a more permissive checker
was never reported by the `scope` claim while the file sat there. `install`
answers for it by hash (`stamp_generated`); a hand edit does not. `.gitignore` is
in it because `install.write_launcher` appends a block there, and nothing else
could explain that line in the next task's diff.

**`derive_exclude` has to be here.** It says "these are not the code under
judgement", and running a test suite produces untracked files: a repo with no
`.gitignore` writes `__pycache__/*.pyc` on the first run, so the print before and
the print after differ. Measured: every `test` claim was SUBJECT_MOVED on its
first run and PASS on its second, and the second only worked because the
artefacts already existed. **A key that the act of answering moves is not a
staleness key.**

**The exclusion set is one rule, not three special cases: the kernel may not
write into the tree it is judging.**

Without it, each of these four makes **the act of recording an answer expire that
answer**: signing one risk makes every other repo-scoped claim STALE, signing two
means neither can be terminal at the same time — and a signature is the only exit
from `UNSUPPORTED`, from having no checker, and from registering a new one. **The
first three bugs really happened, and the third appeared on the day the first two
were fixed.**

The fourth (`.v4/ledger_export.jsonl`) is the same shape from the other side:
`ship` writes it on both paths (§13), so counting it means **every `ship` makes
the claims it has just answered STALE** — a ship that structurally can never
pass, and cannot because of the file it writes itself.

⚠️ **Hand-edited config is not excluded.** `config.json` is the oracle for a test
claim, and `claim_kinds.json` decides what gets raised at all.

A worker's edits in the worktree are **uncommitted**; HEAD does not move a byte.
Binding to HEAD is: write a stub that passes, collect the greens, write the real
thing without committing it, ship. **The bytes that ship never ran the tests, and
the ledger is clean and the chain verifies.**

> ⚠️ **A corollary: build artefacts must not be in git.** A tracked `.pyc` is
> rewritten by every test run → the digest changes → every repo-scoped claim
> breaks every other one → no state has two of them true at once → **it can never
> ship**.

### Ledger
<!-- pinned: kernel/ledger.py::SCHEMA -->

`$(git rev-parse --git-common-dir)/v4/ledger.db`, shared by every worktree.

**Six tables:** `task` · `claim` · `attempt` · `event` · `accepted_risk` · `cost_observation`

**Three invariants:**

| | How it is held |
|---|---|
| No UPDATE, no DELETE | Three triggers per append-only table, each `RAISE(ABORT)`: `no_update_<t>`, `no_delete_<t>`, and `gate_insert_<t>` (the write gate below) |
| No status column | It is not in the schema at all |
| Writes from outside are detectable | The hash chain |

**Tokens are not in `attempt`.** The kernel exec-ing a subprocess costs zero
tokens; what costs tokens is the worker's reasoning, which only the platform
knows — so it is **self-reported by the agent**. Putting that in the table
labelled "an LLM cannot write here" would be wrong. It lives in
`cost_observation`, with a `source` column. **Experiments use only wall-clock and
transcript-derived figures.**

### The hash chain
**A chain needs an anchor outside the ledger, or it verifies nothing.**
<!-- pinned: kernel/ledger.py::chain_head_path -->
<!-- pinned: kernel/ledger.py::write_chain_head -->

```
.v4/chain_head.json    {attempts, last_id, head_hash, scheme}    committed to git
```

`audit_chain` walks from genesis to the last row, so **cutting rows off the end
leaves a shorter chain that still verifies** — and an **empty ledger verifies
too**. Measured: zero attempts, `chain: intact`.

**A check that cannot fail, sitting inside the one command responsible for
catching tampering.** With the anchor, truncation has to fight git, and
rebuilding has to fight it louder. Without this file, "the anchor is CI running
audit" was a permanently green job from its first day.

<!-- pinned: kernel/ledger.py::CHAIN_SCHEME=v4-chain-4 -->
<!-- pinned: kernel/ledger.py::EVENT_SCHEME=v4-event-1 -->
<!-- pinned: kernel/ledger.py::SCHEMES -->
<!-- pinned: kernel/ledger.py::claim_digest -->
<!-- pinned: kernel/ledger.py::_row_hash -->

```
row_hash = sha256(CHAIN_SCHEME ‖ prev_hash ‖ every column a state derivation reads)
```

Five rules:

1. **What it covers has to match what decides.** Leave out `config_sha` or `head_commit` and one UPDATE makes a re-run after a merge unnecessary, with audit saying nothing.
2. **It needs a scheme version.** Without one, changing the formula turns every old row permanently red → the ship gate FAILs forever.
3. **Reading prev_hash and the INSERT are one transaction** (`BEGIN IMMEDIATE`). Several worktrees sharing a ledger is a design requirement, and two kernels appending at once fork the chain — after which audit is permanently red and **indistinguishable from tampering**.
4. **Every field has to delimit itself; a separator is not enough.** Under `"\x1f".join(...)`, `stdout` and `stderr` are adjacent, a checker controls both, and it can print `\x1f`: `("A\x1fB", "C")` and `("A", "B\x1fC")` **hash identically**. Anyone who can do that could already write to the DB — what they gain is that **this class of change escapes `v4 audit`**, and catching exactly that is audit's whole job. `v4-chain-3` moved to length prefixes. **`v4-chain-4` brought `worktree` and `facts_sha` inside the hash** — `worktree` was recorded on every attempt with nothing covering it, so the record of which worktree answered was editable while `v4 audit` still called the chain intact. Old rows still verify: `audit_chain` hashes each row under the scheme it was written with, so the tag is stored per row. The event table has its own chain, `v4-event-1`.
5. **A column added to the schema has to reach the old database.** `CREATE TABLE IF NOT EXISTS` is a no-op against a table that already exists, so adding a column only changes new databases; old ones sit unchanged and raise nothing. Measured: this repo's own ledger had 48 attempts and no `claim_digest` column, so **every `v4 check` blew up at the insert** — and the reason `claim_digest` exists (change a claim's checker and its answers stop verifying) **had never once been active in that ledger**. The whole suite stayed green throughout, because every test opens a fresh database. `connect` has to migrate, and it may only add — never drop, never retype: changing a column's type in an append-only ledger rewrites the history it exists to protect.

### The write gate: a layer that stops things without a daemon
<!-- pinned: kernel/ledger.py::writing -->
<!-- pinned: kernel/ledger.py::_TRIGGERS -->

Besides `no_update` and `no_delete`, every append-only table also carries:

```sql
CREATE TRIGGER gate_insert_<t> BEFORE INSERT ON <t>
WHEN v4_kernel_can_write() != 1
BEGIN SELECT RAISE(ABORT, 'inserts into <t> go through the kernel'); END;
```

`v4_kernel_can_write` is registered by `connect()`, **per connection**, and
returns 1 only inside the `writing()` context manager. The depth counter has to
be re-entrant and must not be process-global — **the permission belongs to the
connection doing the insert**, and several worktrees share one file. It is
registered `deterministic=False`, or SQLite caches an answer that changes.

Measured:

| | |
|---|---|
| kernel, inside `writing()` | writes |
| kernel, outside `writing()` | `ABORT: inserts into attempt go through the kernel` |
| external `python sqlite3` | `no such function: v4_kernel_can_write` |
| external `sqlite3` CLI | `no such function: v4_kernel_can_write` |
| after `DROP TRIGGER` | writes |

**What it buys: a worker with a shell goes from "forge it in one command" to
"drop the trigger first", and a dropped trigger is restored by the next
`connect()` — leaving the forged rows in a database whose schema says they could
not have been written. It is not a boundary; a real one needs a daemon or a
separate uid (§10).**

**What the chain buys: `v4 audit` tells you a row was tampered with.** Both are
needed: the gate stops the cheap route, the chain catches the expensive one. The
anchor is CI running audit.

### Ship
<!-- pinned: kernel/lifecycle.py::ship -->

```
passes ⟺ ① the pre-ship re-scan converges (≤3 rounds)
          ② nothing is blocked: every claim of a `gate: ship` kind ∈ {ANSWERED, RISK_ACCEPTED, RETRACTED},
             and no unanswered `gate: report` claim has crossed an escalation threshold (§10.4, `split_open`)
          ③ the hash chain verifies
          ④ the facts table has no unconfirmed `AUTO:` absence (see §8.8)
always printed (not a gate): which detectors ran, which did not, how many widens, accepted_risk per kind,
          and every unanswered claim of a `gate: report` kind, beside the reason that kind defers
```

**"Which detectors did not run" has to have a reader.** An honest record with no
consumer is the same false move as V3's severity column that nothing branched
on.

**There is no `--json`, deliberately.** The machine-readable half is the exit
code (§13); the report is for a person. A structured output immediately invites
a program to branch on it, and no program here should decide its next step from
"how many detectors ran" — that decision belongs to the person reading. `/run` is
an LLM reading prose, not a parser.

### A ship's retry budget belongs to the task
<!-- pinned: kernel/lifecycle.py::ship -->

`ship_rederive_max` counts **from the start of the task to now**, not afresh on
each `v4 ship`. Reissuing it each time is no limit at all, and every round adds
claims to an append-only ledger — **retrying becomes a way to accumulate
permanently unanswerable claims, with nothing that clears them.**

### Derivation runs three times
<!-- pinned: kernel/lifecycle.py::derive -->

| When | Over what |
|---|---|
| The task opens | base commit + declared scope |
| After `scope widen` | **a full scan** |
| **Before `v4 ship`** | **the actual diff** ← the important one |

**All three are the same full scan.** Claim ids are idempotent, so rescanning is
free; an incremental scan is a second code path buying what idempotency already
gives away.

**The third cannot be skipped:** code a worker writes can create an external
write that did not exist when the task opened.

**Convergence:**

```
① identity is immune to line shifts                                (root cause)
② a detector may not raise a claim whose answer raises another like it   (root cause, **and checkable — see below**)
③ a hard limit of 3 rounds → the task is marked ship_not_converging (symptom)
```

③ firing means ① or ② has a hole. **It is a detector bug report, not a risk for
somebody to sign.**

`derive_exclude` (in `.v4/config.json`) excludes `tests/fixtures/**` — red
fixtures are **deliberately broken**, and a detector firing there is not a
finding.

<!-- pinned: kernel/analysis/subject_files.py::keep -->
**It follows the subject, and is not only used at derive.** Nine checkers work
out their own files from git — they have to: a repo-scoped claim has no
`subject_refs`, and answering "nothing to scan" is UNSUPPORTED, which blocks that
claim forever. All nine fallbacks had never read this rule. The measured cost:
`v4 install` copies every checker's bypass fixtures into the adopter repo, and
the first `v4 check` reported **13 committed credentials**, every one of them a
fixture doing its job. So it lives in `params.derive_exclude` now, read from one
place.

### A conditional detector: facts may widen it, never narrow it
<!-- pinned: kernel/derive.py::_filters_matching_nothing -->
<!-- pinned: kernel/derive.py::FILTER_KEYS -->
<!-- pinned: kernel/derive.py::reads_facts -->

For a detector that reads the facts table, the table is its off switch. **At the
time**, the `bundle_secret` detector (since removed) wrote `facts.get("ui_globs")
or [default]` — point `ui_globs` at a directory that does not exist and it can
never raise anything, while the ship report goes on saying it ran. **And what
actually happened was not somebody pointing at the wrong directory:
`kernel/facts.py` hardcoded `ui_globs` to `[]`, so it ran 665 times across 113
questions and raised 0. That kind is gone and the hardcode is still there —
`kernel/doctor.py` uses the same field to decide whether to warn "you have a UI
and no `surface_command`", so what it silenced was more than one kind.** **This
is §2's "change the suffix set to `.pyx`", arriving through the table instead of
through the code, and structurally invisible to the fixture gate — because a
fixture case brings its own facts.**

**What is judged is the table itself, not running the detector twice.** If any
glob under the three `FILTER_KEYS` (`entrypoint_globs`, `ui_globs`, `dal_globs`)
matches not one tracked file under `git ls-files`, that glob is an off switch:

```
rc == 0 and the repo has facts and the detector really reads the table  ← all three before asking
some glob in FILTER_KEYS matches no tracked file
                          → write a facts_narrowed_detection event
                          → this detector does not count as having run this round (not healthy)
                          → so its claims cannot be retracted (the two guards above)
                          → the ship report lists it as not run
```

**"Does not count as having run" is the whole mechanism:** derive neither aborts
nor quietly counts it as run — both would let a broken table go on shipping.

⚠️ **Running it twice — once with the table, once without, then diffing the claim
sets — was tried and lost.** It read every reasonable narrowing as an attack:
measured, the claims it called "hidden" were the detector scanning `tests/`,
which is `entrypoint_globs` doing exactly its job; `external_write` was refused
on every round of two entire runs of the three-arm experiment. It also punished
every repo that had customised its vocabulary, because a customised table is
usually shorter than the 26 built-in default patterns. **A glob that matches
nothing is the disease, and that question is asked of the table, not of what the
detector emitted.**

**How "reads the table" is decided:** by AST, not substring — `reads_facts` walks
the detector's module looking for a subscript, attribute or `in` test on the name
`facts`, and **does not count the `add_argument` line**. The first version
recognised it by "does it declare a `--facts` flag", which made **all 11
detectors at the time** conditional — the contract requires every detector to
declare that flag, so declaring it and reading it are two different things.

---

### 4.6 Can two claims hold at once  <!-- count-exempt: "two claims" is what each pair is asked about, not how many things the block below holds -->
<!-- pinned: kernel/composition.py::collisions -->
<!-- pinned: kernel/composition.py::report -->

```
v4 --repo . audit --compositions
```

For each pair of repo-scoped claims it asks: does answering one immediately
leave the other looking at a tree that no longer exists. **Both directions →
livelock, and the task can never ship.**

**Why it exists: it happened twice, and both times two individually correct
mechanisms produced it together.**

| Rule A (correct) | Rule B (correct) | Together |
|---|---|---|
| `test` binds to worktree content | `.pyc` used to be committed | Running the tests breaks `scope`, running `scope` breaks the tests |
| A signature has to leave a record in git | A repo-scoped claim binds to worktree content | The file the signature writes expires the signature |

**The collision is in neither rule, so a reader of the rules cannot find it.**
Thirteen agents read the whole design and caught neither. **A reviewer reading a
diff is structurally unable to see this class.**

Its limit: it reasons from what the checkers actually wrote, so a collision has
to have happened once to be visible. **It catches the second one, not the
first.**

### 4.7 Accounting for the request itself

<!-- pinned: kernel/request_cover.py::record -->
<!-- pinned: kernel/request_cover.py::measure -->

Every mechanism here asks "how did you do it". None of them asks "did the thing
you wanted arrive".

Measured (the X/Y experiment, 2026-08-09): three of eight briefs came back
short — no local file upload, no reading of what the local Bot API server
actually answers, a bug report answered by "make it say so" — and **no mechanism
raised a claim**. `task.request` was written in two places and read in zero,
which is precisely the shape `dead-wiring` exists to catch, sitting inside the
kernel.

And the doctrine leaned the same way **at the time**: 34 of its then 88 rules
said "do not do more", and none said "do enough" (it holds 96 today — §14).

**The obvious repair is the one that must not be built.** V3 derived obligations
from prose, so editing a sentence edited the obligation set and produced the next
failure — nine rounds in one phase, two and a half hours, zero lines of code. A
checker that reads the request and judges whether you did it is that machine
again.

So this one **does not judge**. It only makes the accounting exist:

> Quote your request back, span by span and word for word, until the quotes cover
> most of it — and say what each span got.

Quotes are verified with `str.__contains__` against the request stored in the
ledger. They cannot be paraphrased, so the account cannot drift. A span that got
nothing is declared `not done` with a reason, and that reason is a sentence a
person can argue with in the diff. **Narrowing is still allowed — it just stops
being silent.**

```
v4 cover --task T --quote '<a verbatim span>' --symbol <name>
v4 cover --task T --quote '<a verbatim span>' --not-done --why '…'
v4 cover --task T --show
```

`kernel/request_cover.py::measure` computes coverage by characters (punctuation
excluded), counting overlapping quotes once, and **carries no floor of its own**
— it returns a ratio. 75% was that checker's floor at the time, and the checker
is gone, so the number now lives only in a historical comment above `clauses`.
(`MIN_CHARS` is not that — it is the minimum length of the `--not-done --why`
text, 40.)

**It is a report, not a gate.** The `request-coverage` checker that read it was
removed on 2026-08-24 (commit a9ae5fb). The figures behind that decision — 113
questions measured, a marginal cost of 0.181 CNY each, 78 corrections all of them
quote formatting — came from the request on task `trim-detector-layer`, and this
document has not re-measured them.

⚠️ One sentence in that reasoning was false, and the `request-fidelity` lens
found it on 2026-08-26: **"it never opened a source file" is wrong.**
`checkers/request_coverage.py:81` passed `root=root` into `measure`, and `measure`
→ `fault` → `unresolved` → `symbols.names_in` → `ast.parse`. Measured:
`request_cover.unresolved('.', 'kernel/request_cover.py::no_such_function_here')`
says that symbol does not exist, which means it parsed the file. The sentence at
the end of §8.7 below — "`request-coverage` walked the same path through
`symbols.py` one step earlier" — was right all along, and **the two sentences
contradicted each other inside one document.** It **read** source; it never
**judged** source, and that is the reason it was removed.

The accounting still exists (`v4 cover --show`), and whether it was done is asked
by the `request-fidelity` lens — which asks "for every verb in the request, which
symbol in the diff did it", not the question it opened with about whether you
still hold the requester's words. A person, not an exit code.

**But lowering the floor does not stop the move it was there for.** Measured: one
entry quoting the entire request is 100% by characters and PASSes — exactly the
"silence in the shape of an account" the passage above exists to remove, reached
in one command. How high the floor sits and "one quote swallowing the sentence"
are two different things.

So there are two more rules, and both are mechanical:

<!-- pinned: kernel/request_cover.py::spans_a_boundary -->
<!-- pinned: kernel/request_cover.py::clauses -->

| | |
|---|---|
| **A quote may not straddle two separately stated things** | The boundaries are `，。；?！` and newlines. **`、` is not a boundary** — in Chinese it separates items inside one clause, and the registration gate refused the first version on four green fixtures built from `我要佢識send相、片同檔案`. A one-sentence request is still accounted for by a single entry |
| **Every clause needs somebody to claim it** | A character ratio lets a short clause vanish quietly: two clauses weighing 20 and 2 read as 91% while the second was never mentioned at all. The measured failure ("no way to send a file from disk") is exactly this shape — a small span of the brief, and the whole of what was not delivered |

Deliberately not doing something is still available: `--not-done --why`, a
sentence a person can argue back against in the diff.


### 4.7.1 `task.request` is evidence now, so it has to be the requester's words

<!-- pinned: kernel/cli.py::cmd_cover -->
<!-- pinned: kernel/lifecycle.py::carry_forward -->

The passage above saying "written in two places, read in zero" was true then. It
has two readers now:

| Reader | What it is used for |
|---|---|
| `cmd_cover` | The denominator of coverage. `--quote` is verified against it with `str.__contains__`, so quotes cannot be paraphrased |
| `carry_forward` | The hand-off brief prints "its request: …" for the next worker |

A third reader is not in the code: **all seven checks of the `request-fidelity`
lens judge against it.**

So it was promoted from a label to evidence — and its contract was not tightened
when it was. Measured across 42 tasks: the median `task.request` is **42
characters**, **27 of them are under 80**, and the shortest is `驗 forbid` (8
characters). Those are labels, not requests. `fw-rust` stores "Rust from zero:
starting from the 5 rust tasks (4%) in the 113-question corpus, measure before
building…" — "Rust from zero" is the requester's; the other 120 characters are
the worker's chosen method. And the lens states in as many words that it may not
read the worker's rationale.

**Nobody wrote it wrong, because nobody said what it should be.** In `v4 task
--help`, `--base`, `--forbid` and `--after` each carried a paragraph of
explanation; `--request` carried none. It does now: the help text says "the
requester's own words, copied", names the two readers that treat it as evidence,
and says a restatement passes both and is worth neither.

**This cannot be verified.** No mechanism has any access to where the request
originally came from, so "is this the original wording" is not mechanically
decidable. A self-reported "this is verbatim" field with no verifier is the same
shape as `restored_why` — not added. Two places catch it instead, one preventing
and one detecting:

- **The writing side** — `--request` says what it is. The CLI is the one point
  every task-creation path goes through; the `task-splitter` agent's contract
  already says `<verbatim>`, but it is optional
- **The reading side** — the lens's first check asks exactly this. Unanswerable
  is the first finding, because otherwise it reports zero findings against a
  request it never received, and that is a false green


## 5. Scope

Globs are declared when the task opens. The write hook refuses writes outside the
scope; the `scope` checker catches them at ship (the fallback for a platform with
no hooks, and the task is marked `degraded`).

### The exit for having declared it wrong
<!-- pinned: kernel/lifecycle.py::open_task -->

```
v4 scope widen --task T --add <path> --why "<reason>"
   ↓ kernel: ① write an event ② re-derive the whole repo ③ trigger engagement (exempt from the duplicate check)
the worker carries on. No re-plan, no re-split, no re-running answered claims.
```

**"Declare in advance, enforce afterwards" is the structure both of the
predecessor's loops shared. The only way to keep it is to make correcting the
declaration cheap, not to make the declaration harder to get wrong.**

The duplicate exemption: a second widen inside one task will necessarily read
like the first, and without the exemption it would necessarily be refused.

### What can never be widened into
<!-- pinned: kernel/scope.py::protected_for -->

```
.v4/**  checkers/**  detectors/**  .github/**
```

`.v4/config.json` holds `test_command` — the sole oracle for a `test` claim. One
`pytest -k nothing` makes every test claim permanently green. To change these
paths → `ACCEPTED_RISK kind=scope_widen_protected`.

**No gate stops an ordinary widen (a gate there is what the predecessor was). The
control is measurement, and the measurement has to be visible:** `v4 status` and
the ship report both list the number of widens and what percentage of the repo
they reach.

### Narrow — the mirror of widen, and the one gate it has
<!-- pinned: kernel/scope.py::narrow -->

```
v4 scope narrow --task T --drop '<glob>' --why "<reason, ≥40 characters>"
```

**Why it exists:** scope is declared before the work, and `derive` raises claims
against the whole scope, not against what was touched. Work in `src/workers/**`
declared as `src/**` drags in a whole-file claim for every other file — and the
only correction used to be abandoning it and reopening.

One gate, which `widen` does not need: **a file that has already been changed
cannot fall out of scope.** That is what stops narrow being used to hide what was
done, and it is decided by the diff — no judgement in it, no exception to argue
for.

⚠️ **It is "falls out of scope", not "matches the dropped glob".** A scope can
name the same file twice (`kernel/**` and `kernel/cli.py`), and dropping the wide
one changes nothing — the narrow one still puts every claim in that file in front
of this task. The first version asked `matches(p, drop)`, which is a proxy, and
it parts company with the correction described above the moment work begins.
Measured twice on 2026-08-26: once it made a `tests/**` narrow impossible, at the
cost of a signature on a file the task had never touched; once it made a
`kernel/**` narrow impossible, at the cost of abandoning the task — which is the
"only correction" named above, and the one this function was written to replace.

**`--add` does not belong here.** It is widen's, and `--add` / `--drop` / `--why`
share one subparser, so `scope narrow --drop a/** --add a/b.py` used to be
accepted, drop, throw the add on the floor, and say nothing. To swap in a
narrower glob: widen to the narrow one, then narrow away the wide one — **two
events, so the ledger can say which was which.**

Narrow un-asks nothing: claims already raised stay in the ledger; a re-derive
just stops raising new ones there.

---

### Forbid — the other side of scope
<!-- pinned: kernel/scope.py::forbidden -->

```
v4 task --id T --request "..." --scope "app/**" --forbid "core/retry.py,vendor/**"
```

`scope_globs` says where the work may go. **Nothing said where it may not** — and
`task.request` was written and never read anywhere in the repo, so "do not touch
the retry logic" in a request had no way of being expressed at all.

An event rather than a column: no schema change, the same route as
`scope_widen`, and the forbidden set becomes part of the append-only record
instead of an editable field.

**Forbid is not a narrow scope.** No widen reaches it — the task said at the
outset that this is not to be touched. Getting it wrong means reopening the task.

⚠️ **What it cannot hold is in §10.** A violation of "a report may not become an
authority" is not in any file or any token; it is in the relationship between two
things — and those are not narrower globs, they are a different kind of
sentence.

---

## 6. Signatures — the one place a person touches this
<!-- pinned: kernel/risk.py::accept -->

```
v4 risk accept --claim <id> --kind <kind> --why "<reason, ≥40 characters>"
```

**Four kinds:** `unprovable` · `no_checker` · `baseline_raise` · `scope_widen_protected`

Without the kinds, the person signing **cannot see what they are signing** — the
mirror image of the predecessor's P0/P1/P2 pantomime.

| | |
|---|---|
| `stdin` is not a TTY → refused | **Friction, not a boundary.** `pty.spawn` is one line. The identity comes from `git config user.email`, which an agent can change |
| **The real anchor** | Every signature also writes a record to `.v4/risks/<claim>.json`, committed. **Going around it leaves a commit with somebody's name on it**, and the anchor is a person reading that diff |
| Expiry | **The same as a PASS, against the same key.** Otherwise a signature is permanent and a PASS is not, which makes getting a signature cheaper than getting a PASS |

> ⚠️ The key must be computed **after the record is written**. The record lives in
> the repo, and a repo-scoped claim binds to worktree content — computing it
> first means the act of signing invalidates the signature.

**Two policies, pick one:** `allow_accepted_risk` (occasionally interrupts you to
sign) or `no_accepted_risk` (never interrupts, but an unprovable task is stuck).
**There is no third — "risk may be accepted with nobody signing" is no risk
control at all.**

⚠️ **The frequency of `ACCEPTED_RISK` is an undeclared unknown, and it is the
single point of the promise that you are not needed in the middle.** Record it
per task through phases 1–3; **more than once per task means the design has gone
wrong**.

---


### 6.1 `--scope repo` — one signature covering the whole repo

<!-- pinned: kernel/risk.py::REPO_SCOPABLE -->
<!-- pinned: kernel/state.py::cover_key -->

Some claims are not "this was done wrong"; they are "this repo structurally
cannot answer it" — `surface-proof` in a repo with no second suite,
`control-plane-budget` in a repo that has declared no ceiling. Signing the same
fact once per task is an act that has to be ignored every time, and ignoring is
contagious.

```
v4 risk accept --claim <id> --kind unprovable --scope repo --why '…'
```

| | |
|---|---|
| Which kinds | `unprovable` and `no_checker` (`REPO_SCOPABLE`). **Not `scope_widen_protected`** — that is about one specific crossing, not a repo-level fact |
| It has to be earned | The latest attempt must be exit 4. **A claim that has never run cannot be signed** — that is the difference between claiming it cannot be answered and having tried |
| When it lapses | `cover_key`: kind ＋ the checker's program sha ＋ the detector sha ＋ the config sha. **Worktree is deliberately excluded** — a signature saying "this repo has no lockfile" should not expire because somebody edited a line of code |
| It ends itself | The day the checker stops returning exit 4 (the day the thing it has been looking for appears), the signature covers nothing |
| Recorded at | `.v4/risks/repo/<kind>.json`, committed |

⚠️ **It expires under different rules from a per-claim signature, deliberately.**
The per-claim one expires against subject bytes; this one expires against which
program is asking. Two different questions, two keys.


### 6.2 Eleven signatures in one place is not eleven risks

<!-- pinned: kernel/risk.py::accept -->

Measured (2026-08-25, at `fw-tax`'s base): five places standing there, signed
again every time somebody touched the file —

| Place | Signed | kind |
|---|---:|---|
| `kernel/cli.py::cmd_derive` | 11 | `unprovable` |
| `kernel/analysis/gosource.py::_build` | 9 | `unprovable` |
| `kernel/analysis/gosource.py::shape_source` | 9 | `unprovable` |
| `kernel/analysis/secret_patterns.py::_encoded_matches` | 9 | `unprovable` |
| `tests/test_rust_without_a_rust_parser.py` | 8 | `baseline_raise` |

**Both exits were open the whole time and neither was taken.** The `cmd_derive`
one was fixable (the guard wrapped a call with no degraded state); the
`test_rust_...` one had `.v4/test-shape_baseline.json` waiting for it, and the
rule printed that path on every failure. `fw-tax` retired those two; the other
three are still there.

**Why it happens.** A signature expires against subject bytes, and bytes move
because of edits beside them — so a question that can never be answered is asked
again every time somebody touches the file, and **signing once is always cheaper
than fixing once**. §6's "more than once per task means the design has gone
wrong" is about this, and these five rows are its measurement.

⚠️ **Sign last.** `fw-tax` itself signed the same claim (`f2f62bacf4ff8e41`)
twice — `16:38:12` and `16:44:07` — because `kernel/cli.py` changed again after
the first signature, the bytes moved, and the signature expired. **That task's
commit message says "Nineteen recurring signatures become one", and its own claim
has two rows in the ledger.** §6 already says the key is computed after the
record is written; this is the same ordering one level up, in the procedure — sign
when the diff has settled, not the moment it passes.


## 7. A measurement round freezes the ruler
<!-- pinned: kernel/register.py::assert_ruler_unmoved -->
<!-- pinned: kernel/register.py::RulerMoved -->

```
v4 round open  --label <name>     ← pins the ruler (.v4/acceptance.json)
   … measure …
v4 round close --label <name>
```

**Inside a round the ruler does not move. No judgement calls, no exceptions.**
The tool refuses to measure rather than asking you.

**Which commands refuse: `v4 verify` and `v4 register`, both exit 2.** Those two
are the measuring — whether a checker passes against frozen criteria. `check`,
`ship` and `status` do not consult the round: they measure a task, not a ruler.
**This passage did not exist before, and "the tool refuses" read as though it
meant every command.**

**It may change between rounds, but that voids every measurement of the previous
round and they have to be taken again.** That gives moving the ruler a real cost.

Why not "never move it": a frozen wrong ruler does not stop the work, it starts
**being informally ignored** — at which point it is not frozen, only unrecorded.

---

## 8. Config
<!-- pinned: kernel/config.py::RepoConfig -->

Five stores, **in git and not in the ledger** (`config.json`,
`claim_kinds.json`, `checkers.json`, `detectors.json`, plus
`facts.<repo>.json`). Putting hand-edited things in an append-only DB means
faking UPDATE with "the newest row wins".

### `.v4/config.json`
<!-- pinned: kernel/config.py::DEFAULT_THRESHOLDS -->
<!-- pinned: kernel/layout.py::CONFIG -->

```json
{
  "test_command": ".venv/bin/pytest -m 'not integration' -q",
  "test_timeout_sec": 1800,
  "policy": "allow_accepted_risk",
  "protected_paths": [".v4/**", "checkers/**", "detectors/**", ".github/**"],
  "derive_exclude": ["tests/fixtures/**", "**/__pycache__/**"],
  "thresholds": {"min_chars": 40, "dup_threshold": 0.8,
                 "ship_rederive_max": 3, "widen_warn_pct": 5,
                 "report_max_open": 10, "report_max_days": 14,
                 "report_max_repeat": 5}
}
```

**There are two timeouts, and the one that kills a checker is not
`test_timeout_sec`.** What decides whether a claim becomes CHECKER_ERROR is each
checker's own `timeout_sec` in `.v4/checkers.json` (`v4 register --timeout`,
default 300; this repo's `test` is 900). `.v4/config.json`'s
`test_timeout_sec` only bounds the execution-trace sub-run inside
`checkers/test.py`. **Set both, and the outer one must be larger than the inner**
— with a registry at 300 seconds and a local 1800, everything in between is
killed by the kernel, recorded as CHECKER_ERROR, and the worker is looking at a
failure that has nothing to do with them.

**Both must be a multiple of the escalation line.** With both on the same line,
the day the test suite reaches that number the system does not signal "time to
think about mapping" — **every task's test claim times out together → ERROR →
every ship blocked**, and the worker is looking at a failure that is not theirs
and cannot be fixed.

**The whole suite runs; there is no file→test mapping.** Measured: 4,418 tests in
72 seconds. At 72 seconds no mapping saves anything, and it brings a new failure
mode (wrong mapping → tests skipped → false PASS). **Reconsider when the whole
suite passes 5 minutes.**

### `.v4/claim_kinds.json`
<!-- pinned: kernel/layout.py::CLAIM_KINDS -->

```json
{"<kind>": {"question_template": "…{file}…{symbol}…",
            "detector": "<file>.py | null", "checker": "<checker id>",
            "staleness": "subject | repo",
            "gate": "ship | report (omitted = ship)",
            "gate_why": "<why it can, or cannot, wait>            (§10.4)",
            "engagement": true | false,
            "rule": [{"text": "…", "source": "…", "from": "R-<id> | omitted"}, …]
                                                          (required when engagement is true; §9)
            "engagement_why": "<why no sentence is owed> | omitted   (when engagement is false)",
            "baseline": true | omitted                              (carries .v4/<kind>_baseline.json; §12)
            "applies_to": "framework | declared | omitted", "applies_to_why": "…",
            "needs": "<path> | omitted                              (with applies_to: declared; §8.7)",
            "depends_on_kind": "<kind> | omitted                     (no kind uses it today; §1)"}}
```

Thirteen fields, counted from `.v4/claim_kinds.json`. The first five and the
last are the schema as first written; the rest arrived with the gate tier, the
engagement rules, the baselines and `install`'s applicability test, and this
sketch had not followed.

**The question sentence is generated from the template. No LLM can write one.**
This is what "the claim set is the contract, and there is no prose contract"
means.

### `.v4/checkers.json`

Written by `v4 register`, **not by hand**. Each entry: `path` · `sha256` ·
`timeout_sec` · `kinds` · `fixtures` · `reads` (the globs it can read, §8.1).

### `.v4/detectors.json`
<!-- pinned: kernel/layout.py::DETECTORS -->

Written by `v4 register-detector`, **not by hand**. Each entry: `path` · `sha256` ·
`fixtures` · `cases`.
**In a repo without this file, no conditional detector runs at all** — deliberately
fail-closed: a "run it anyway when there is no registry" default is the door this
gate exists to close.

---


## 8.5 How a reviewer finding gets in
<!-- pinned: kernel/review.py::raise_finding -->
<!-- pinned: kernel/review.py::amend_note -->
<!-- pinned: kernel/review.py::bind_closing_test -->

A reviewer is the only source of claims no detector could raise, so it needs a
route of its own.

```
v4 review add   --task T --file F --symbol S --note "…"     → opens a review-finding claim
v4 review amend --claim C --note "…"                        → changes what a finding says
v4 review close --claim C --test <path> --command "…{path}…" --parent <commit>
v4 review defer --claim C --why "…" --target "…"            → real, not now, and where it went
```

**`add` opens a finding, `amend` changes one, and the difference is stated by the
person typing rather than guessed by the kernel.** A claim's id is `(task, kind,
file, symbol, variant)` and has nothing to do with what it says — which is what
lets one defect stay one claim across a `scope widen`. The cost: filing at the
same coordinates again with a different note looks identical to `raise_finding`
whether it means "I am correcting my own sentence" or "I found a second thing
here". It used to guess, and it guessed the destructive way — overwriting the old
sentence, printing `note amended on <id>`, exit 0.

**Measured 2026-08-27: one lens sweep rewrote 10 notes across 9 claims, and 6 of
those had exit 0 as their last attempt** — findings opened on the 18th and
answered on the 26th, now carrying somebody else's sentence. A reviewer noticed
and pasted the original back through `review add` to restore it, hit `claim.note`
exactly, and got `already open`, zero writes, exit 0. **This defect made even "I
fixed it" false.**

So it no longer guesses. A note not seen at these coordinates is a finding and
opens its own claim: `variant` goes from `<lens>` to `<lens>#2`, `#3`, the same
move `analysis/external_write.py` makes to separate two shapes, and the id is
still hashed from the row it stores. **One document is no longer one finding per
lens** — a finding on a non-Python file carries no symbol (`resolve_symbol` used
to force `symbol=""` there; today it refuses a `--symbol` on such a file outright
with `BadCoordinates`, and the finding is raised without one), which used to make
those five values identical for that lens on that document forever.

Changing a sentence is `amend`, which names a claim. Also an event and not a
column: `claim.note` is untouched, both readings stay in the ledger, and
`current_note` returns the last one — **and it is what `raise_finding` asks when
deciding whether it has seen this before**, not `claim.note`. One source for the
write path and the read path, which is the root of the failed restore above.

**`defer` is not a third verdict; it is the record of a decision not to fix.**
`--target` may not be empty: a deferral that points nowhere is the silence it
exists to replace. The record is written in two places — a `finding_deferred`
event in the ledger, and `.v4/deferred/<claim>.json`, which is committed so it
travels with a clone and shows up in a diff (the ledger lives inside `.git/`).
`actor` is decided by the tty, exactly as in `risk accept`: `human` with one,
`agent` without.

> ⚠️ The two are reconciled by different commands, and the difference is
> deliberate. `reconcile_signatures` checks `.v4/risks/` in both directions and
> `audit_chain` runs it, so a forged signature holds a ship.
> `reconcile_deferrals` (`kernel/ledger.py`) asks the same two questions of
> `.v4/deferred/`, but only `v4 doctor` runs it (the `deferrals` row);
> `audit_chain` still calls `reconcile_signatures` alone, so hand-write or delete
> a deferral file and `v4 audit` will not see it. A deferral makes nothing
> terminal, so a stale file loses the record of a decision and not its effect —
> which is why it is a `doctor` row and not a held ship.

**Everything it decides is an enum or a path the kernel can verify.** What it
wants to say goes in `note` — **which selects no checker and is not part of
identity**.

**Opening and closing are separate, and both are events rather than columns** —
the ledger takes no updates, and the test that closes a finding is chosen after
the claim exists.

## 8.7 How a new repo adopts this

<!-- pinned: kernel/install.py::installable_kinds -->
<!-- pinned: kernel/install.py::copy_files -->
<!-- pinned: kernel/install.py::write_launcher -->

Two commands:

```
v4 init         # creates .v4/ and CLAUDE.md, leaving test_command for you to answer
v4 install      # everything else
```

**It does not ask you to choose.** Choosing among 21 checkers means knowing which
of them has a subject in your repo — which is exactly the question the detector
layer answers automatically on every task. A person looking at filenames cannot
answer it and should not be asked to. So everything is installed, and the
detectors decide which fire.

Five things it will **not** do:

| | Why |
|---|---|
| It does not skip the gate | Every checker still has to FAIL on its own red fixtures and PASS on its greens before entering the registry. Installing in bulk must not become a back door for a checker that would be refused on its own |
| It does not overwrite | A repo with a modified checker is saying something; restoring it quietly answers that repo's claims with a program its author never wrote |
| It does not install the framework's own | A kind can declare `applies_to: framework` in `claim_kinds.json`. `dead-wiring` reads `kernel/ledger.py`; `spec-coverage` resolves SPEC against `kernel/cli.py` — in a second repo they exit 4 or blow up |
| It does not install a kind that has nothing to read | Every checker declares `reads: [glob…]` in `.v4/checkers.json`. <!-- pinned: kernel/analysis/subject_files.py::readable --> If not one file in the repo matches, it is not installed and its kind is not written into `claim_kinds.json` either. **This is the cross-stack safety net** — a Go tree has no `**/*.py`, so a Python-only checker is held back by the same machinery instead of being installed and returning PASS over source it never parsed. (Holding back is the default in absence; thirteen checkers later actually learned to read Go — see the end of §8.7.) Measured at the time on a Go repo with four real defects planted in it: **at the time** 15 of 27 checkers honestly returned exit 4, **8 returned 0 without having read one Go file**, and among them `dependency` said `PASS: no package manifest declares a dependency` over an unpinned `go.mod` with no `go.sum` |
| It does not install a kind that cannot be answered | `applies_to: declared` plus `needs: <path>`. `layer-boundary` waits for `.v4/layers.json`, `control-plane-budget` waits for a ceiling. Measured: two of the twelve claims on a brand-new repo's first task were these, both permanently unanswerable, and the only way out was a signature per kind with nothing prompting for it. **A missing rule says so in `doctor`; a rule that blocks every task reads as a broken framework.** The moment the file appears, another `v4 install` installs it — and since `write_layers`, `layer-boundary`'s file does not have to be written from nothing: `install` drafts `<repo>/.v4/layers.json.draft`, and renaming it turns it on (see the `layer-boundary` section above) |

After installing, `v4 doctor` should say wired. It also warns about checkers that
are **registered with nothing in this repo for them to read** (the §6 `--scope
repo` signature).

**Absence is the default, not the destination. A rule learning another language
is bought with another extractor and another set of fixtures.**
<!-- pinned: kernel/analysis/symbols.py::names_in -->
<!-- pinned: kernel/analysis/gosource.py::shape -->
The passage above is about what a checker should do when it does **not** know a
language: `reads` holds it back, it is honestly absent in that tree, and it does
not return a PASS over source it never read. That is the safety net, not the
goal.

As of 2026-08-21, thirteen checkers that had only read Python AST added
`**/*.go`, each with its own Go red / green / bypass fixtures through the same
registration gate, and `request-coverage` had walked the same path through
`symbols.py` one step earlier. **Eight of those fourteen are still in the
registry**, and the count depends on how `reads` is read: seven name `**/*.go`
outright — `dangling-ref`, `fail-closed`, `layer-boundary`, `test-expectation`,
`test-shape`, `test-token-shape`, `test-weakened` — and `external-write` declares
`**/*`, which covers Go without naming it (counted from `.v4/checkers.json`).

The other six — `secret-chain`, `webhook-replay`, `dal-write`, `route-auth`,
`facts-coverage`, `request-coverage` — were removed on 2026-08-24 in the trim
derived from the 113-question measurement. **Reading another language is not a
reason to keep a rule**; whether what it catches is worth anything is.

**The bar for a language is a real parser**, not "a set of regexes": Python uses
`ast`, Go uses Go's own `go/ast` (`kernel/analysis/_go/`, a subprocess, zero
Python-side dependencies, because a Go repo necessarily has a Go toolchain), and
TS/JS for now uses regexes over text with comments and strings stripped — a
compromise watched by the bypass fixtures, and the day they stop holding is the
trigger for bringing in a parser.

Since 2026-08-24 these carry TS/JS in their `reads` — nine of them, not all,
counted the same way: eight name `**/*.ts` and its siblings — `dangling-ref`,
`fail-closed`, `layer-boundary`, `signature-change`, `test-expectation`,
`test-shape`, `test-token-shape`, `test-weakened` — and `external-write` again
reads them through `**/*`. Each has its own TS red / green / bypass fixtures
through the same registration gate.
<!-- pinned: kernel/analysis/subject_files.py::is_test -->

**This time it was not verified against fixtures alone.** The Go passage above
left something unsaid: fixtures pin behaviour, not vocabulary, and a language's
vocabulary is a fact about a corpus. So TS's filename conventions and declaration
forms were measured against five real repos in the eval corpus — KaTeX, csstree,
ts-pattern, valibot, yjs, 406 test files. The measurement corrected three
guesses:

| The guess | What the corpus said |
|---|---|
| Test directories are called `__tests__` | `csstree` uses `lib/__tests/`, with no second pair of underscores |
| Test files are called `*.test.ts` | `yjs` uses `*.tests.js` |
| A test is a call to `it()` | `yjs` writes `export const testXxx = tc => {}` (lib0/testing). Counting only `it()` finds **1** test across its 17 test files, which makes anything wrong in that repo entirely invisible; counting both forms finds 296 |

Acceptance readings, outside the fixtures: `is_test` selects 406 of 406 test
files; `ts_count` finds 4,842 live tests, matching a hand count; `ts_resolve`
resolves 4,578 of 4,600 relative imports (the 22 it misses are `.json`, `.css`,
`?raw`).

**Stated limits of coverage**, the same way `GO_ASSERT_CALLS` treats a matcher it
has not seen: Rust is §8.7.1; tsconfig aliases (`@/x`) are not resolved — 906 of
6,948 imports, 898 of them in one repo, so it is a per-repo gap; a live `it()`
inside `describe.skip(…)` is still counted, because knowing it sits in a skipped
block would need bracket matching; class methods are not counted, because
resolving `obj.method()` needs type information.

⚠️ **The sentence above about "watched by the bypass fixtures" did not hold on
the detector side for a while.** `register.verify_detector`'s loop was `((RED,
True), (GREEN, False))` — `BYPASS` was not in it, so a detector's bypass fixtures
had never executed since the day they were written, while the checker side ran
them all along (`verify` prints "including bypasses"). Measured before wiring it,
across those 33 bypass cases: 32 were already caught by the raising side and one
was not (`external_write`'s `go_readback_in_dead_code.go`), and that one turned
out to be a shipped-table gap rather than a detector one — `client.Post` was
missing from the default outbound vocabulary. The loop is `((RED, True), (GREEN,
False), (BYPASS, True))` now: a bypass wants a claim exactly as a red case does.
What the detector side still lacks, stated in the code rather than left to be
rediscovered: no `MIN_BYPASS` floor, and no refusal of a bypass byte-identical to
a red case — both of which `verify_checker` has.

The Go emitter emits **structure, not judgement**: whether a fan-out is bounded,
whether a path is a source file, which pairing counts as an expectation are all
decisions, and they stay in Python, beside the same decisions for the Python
side. One rule in one place, two extractors.

Porting had three outcomes, and all three happened:

| Outcome | Example |
|---|---|
| The same question, a new extractor | `dal-write`'s three regexes are unchanged — the verb has to come from a string literal on both sides |
| Go answers it better | `route-auth` and `webhook-replay` need no `DEFAULT_RECEIVERS`: `func(w http.ResponseWriter, r *http.Request)` is the language saying "handler" itself. `secret-chain` does not have to guess a client from its name, because `binds` carries the declared type |
| Go has shapes Python does not | A dangling reference under `//go:build ignore` (build and vet both exit 0); `return nil, nil` handing back an empty value with a nil error; `_ = resp`, the language's own "I am ignoring this" |

**A language it does not know still returns `None`, and `None` means "no
verdict", not "no names".** The 8 false greens above are that distinction not
being made. The same line explains why `dependency` and `dep-provenance` adding
`**/go.mod` and `**/go.sum` is not an exception: they ask whether this manifest
is pinned and confirmed by a lockfile, which is a fact rather than semantics.

**The facts layer moved the same day.** `v4 install` used to propose a facts table
from Python only, so a Go repo adopted with an empty vocabulary and the four
checkers reading that table all answered UNSUPPORTED — quietly.
`tracked_source_files(suffixes=…)` and `scan_source(lang=…)` are the repair: the
table is the rule, and only the extractor varies by language.
<!-- pinned: kernel/facts.py::tracked_source_files -->
<!-- pinned: kernel/analysis/facts_grammar.py::go_dotted_names -->

### 8.7.1 Rust: two kinds read it, seven do not, and every one has a number

Rust is **5 of the 113 questions (4%)** in the corpus — boa, fd, oxvg, pest,
wasmi. All were cloned, 1,316 `.rs` files, and every number below is counted from
those 1,316 files rather than taken from a document or an impression.

**No parser, and this time not "for now".** Python uses `ast` because the
interpreter running this has one; Go uses `go/ast` because a Go repo necessarily
has a Go toolchain. Neither holds for Rust: every Rust parser is a crate, and
this framework takes no third-party dependencies; `rustc` cannot be assumed
present in an adopting repo either — this machine does not have it. So
`kernel/analysis/rssource.py` is a scanner, beside `pysource` and `gosource`,
**and it imports nothing from this package**. That property was measured:
`rs_masked` started inside `symbols`, and `symbols` reaches into `gosource` to
answer Go, so `secret_scan` — a checker that has never read a line of Go — had
its `program_sha` moving with the Go emitter, which is what
`test_a_checker_that_does_not_read_go_is_untouched` says.
<!-- pinned: kernel/analysis/rssource.py::rs_masked -->

The mask is a scanner rather than three regexes, and the reason is also numbers:

| Rust has, TS does not | In the corpus | What copying TS would do |
|---|---:|---|
| lifetimes `&'a str` · `<'a,` | 8,138 | the char-literal pattern reads it as an opening quote and eats to the next apostrophe |
| raw strings `r"…"` · `r#"…"#` | 1,910 | the terminator is chosen by the author, so no fixed pattern stops at it |
| nested block comments `/* /* */ */` | 3 | the first `*/` is not the end |

Acceptance readings, outside the fixtures: all 1,316 files preserve their length;
`fn` drops from 19,486 to 19,483 — and the 3 that go are inside comments and
strings.

**`test-weakened` and `test-expectation` are built.**
<!-- pinned: kernel/analysis/rssource.py::rs_functions -->

Rust has a structure the other three languages do not: **tests live inside the
implementation file**. Of the 455 files carrying tests, only 78 sit under
`tests/` or `benches/`; the other 377 are `#[cfg(test)] mod tests`. Answering by
name and directory misses four fifths of them, so the `.rs` branch has to read
content — and being the first branch that does is what broke an old defect open:
`subject_files.is_test` took a repo-relative path and resolved it against the
process's working directory. Python judged by name first and answered `True` when
it could not read, so it was never visible; Rust reading nothing means "not a
test", and five fixtures returned exit 0 — a green meaning nobody looked. The fix
is in the owner: `is_test(path, source=None, root=None)`.

`rs_count` finds **2,902** live tests. The raw attribute count is 2,914, and all
12 of the difference are named: 7 are `#[ignore]` (deducted on purpose) and 5 are
`#[test] fn $name()` templates inside `macro_rules!`. **A `#[test_case(…)]` line
is one test**: the corpus has 99 such attributes on 12 functions, and counting a
function once means 45 of the 46 cases on one function in
`boa/core/engine/src/module/loader/mod.rs` could be deleted without moving the
number — which is precisely the move this kind exists to catch.

`rs_expectations` takes the second argument, and which argument holds the
expectation was measured: of 2,367 `assert_eq!` calls, 1,219 have a literal on
exactly one side, and it is **1,177 on the right against 42 on the left** — that
is `assert_eq!(actual, expected)`, the same position the Go table gives
`assert.Equal` and Python gives `assertEqual`. The corpus has 492 tests carrying
1,284 expectations. A constructor (`Position::new(10, 50)`) counts as an
expectation, but only when every argument inside it is a literal;
`Position::new(line, col)` does not, because refusing is the safe side — something
wrongly read as an expectation becomes a FAIL on a test nobody weakened.

**Seven do not read Rust, each with a reason and a number:**

| kind | Measured in the corpus | Why it is not built |
|---|---|---|
| `fail-closed` | `.unwrap_or*` / `.ok()` 805; empty `Err(_) => {}` arms **1**; empty `if let Err {…}` bodies **0** | Without types there is no telling `Option` (entirely normal) from `Result` (an error being eaten). Narrowed with the shared vocabulary it produced 120 findings, and every one in the sample was a false positive — the vocabulary's `get` is an HTTP verb, and Rust's `args.get(1)` is a collection access. Switched to Rust's own IO vocabulary it leaves 15, and most of those are `env::var(x).unwrap_or(default)`, a deliberate default. **A gate producing 120 wrong or 1 right on this corpus is worse than no gate.** The same judgement Go made about `_ = f()` |
| `signature-change` | `pub fn` 5,840 | Rust has no default arguments, so adding one breaks every caller and the compiler says so. Not built, as for Go. ⚠️ This sentence is not the same grade as Go's: that one was measured against `go build`, and this machine has no `rustc`, so this is a definition of the language rather than an observation |
| `dangling-ref` | `use crate::` 1,363; `#[cfg(feature …)]` 1,138 | What the compiler resolves is not this rule's business. Go's real gap was files excluded by a build constraint; Rust's equivalent is `#[cfg(feature)]` — 1,138 of them, real, but knowing which features are on needs `cargo` to resolve them |
| `layer-boundary` | — | `applies_to: declared`, and **0** claims across the 113 questions — **at the time** no repo had declared a `.v4/layers.json`, and writing a declaration from nothing is why. `install` drafts one now, so whether that zero stays zero is an unmeasured question. Adding a language gives it nothing to do |
| `test-shape` | fan-outs total **22** (`par_iter` 8 · `thread::spawn` 9 · `join_all` 4 · `tokio::spawn` 1); source reads 145 (`include_str!` 80 · `.contains("…")` 65) | 22 fan-outs across 1,316 files is too thin. And among the 145 source reads, `include_str!` is mostly loading fixture data (`.wat`, `.snap`), which is a different thing from a test asserting on its own source text — and separating them is a judgement, not a pattern |
| `test-token-shape` | 99 (`assert_eq!(format!(…))` 25 · `.to_string()` 74) | Measured, not built. This is the next best-numbered candidate, not a question of principle |
| `external-write` | 70 (`fs::` writes 28 · `File::create` 29 · outbound 3 · `Command::new` 10) | Two of the five repos are CLI tools, where writing files is the product. Telling an unobserved outbound write from a program doing what it exists to do needs the facts table, and no Rust repo has one |

**A Rust file does not leave any kind stuck at exit 4 today.** A detector meeting a
suffix it does not know does `continue` and raises nothing, so the seven above are
silence rather than "cannot verify". The silence is written down here because on
disk a kind measured and then declined looks exactly like a kind nobody thought
of.


### 8.8 What lives where

<!-- pinned: kernel/ledger.py::ledger_path -->
<!-- pinned: kernel/install.py::MANIFEST=.v4/installed.json -->
<!-- pinned: kernel/install.py::HOME_FILE=.v4/home -->
<!-- pinned: kernel/hashing.py::program_sha -->

An adopter has **no `kernel/` of its own**. `bin/v4` puts the framework on
`PYTHONPATH` and then says which repo is being judged with `--repo $(git
rev-parse --show-toplevel)`. Two separate things: where the program comes from,
and which tree it judges.

| | Where it lives | Shared? |
|---|---|---|
| `kernel/` | The framework repo | **Shared** — every adopter points at the same copy |
| `checkers/` `detectors/` `hooks/` | Per adopter (copied by `copy_files`) | Not shared |
| the ledger: claim · attempt · event · chain | Per repo, at `<git-common-dir>/v4/ledger.db` | Not shared |
| `.v4/`: config · facts · baselines · risks · checkers.json · detectors.json · installed.json · home | Per adopter | Not shared |
| `.v4/fixtures/` | Per adopter (copied by `copy_files`, paths rewritten by `fixture_dest`) | Not shared |
| `.v4/lenses/*.json` | Per adopter (copied by `copy_files`) | Not shared |
| `.github/monitor/*.md` | Per adopter (copied by `copy_files`) — **at the time** one kind's FAIL text pointed at it; since removed | Not shared |
| `.claude/settings.template.json` · `.claude/commands/` · `.claude/agents/` | Per adopter (hooks take effect only once you `cp` it to `settings.json` yourself) | Not shared |
| `.v4/ledger_export.jsonl` | Per adopter, **committed** | Not shared |

**The ledger sits under `.git/`, not under `.v4/`.** So it is never committed,
never appears in a diff, and never produces a merge conflict. What CI reads is
the `ledger_export.jsonl` that `ship` exports.

**The one deliberate sharing:** `ledger_path` uses `--git-common-dir` rather than
`--git-dir`, so **every worktree of one repo shares one ledger**. That is the
premise of running in parallel — `git worktree add` gives several shells a task
each, and the claims do not split into separate copies.

#### Why checkers are copied and the kernel is not

<!-- pinned: kernel/runner.py::CHECKER_TAMPERED=6 -->
<!-- pinned: kernel/install.py::copy_files -->

A checker arriving in an adopter runs all of its own fixtures, and only after
passing is it written into `.v4/checkers.json` **with its sha** — compared on
every run afterwards, and exit 6 `CHECKER_TAMPERED` when it differs. Without the
copy this mechanism has no subject: one framework update silently replaces the
program judging you, and none of your answered claims knows.

**The kernel has no such layer, so it is shared** — and `program_sha` charges
for the sharing: it follows imports across into the framework, so editing a
`kernel/analysis/` module expires an adopter's answered claims.

⚠️ That sentence was false for a while. `program_sha` followed imports only
within one repo, and an adopter's `kernel/analysis/` is not in their repo, so
**editing the module that does the judging expired nothing** — which is the one
reason this function exists. Inside the framework's own repo it was fine, so no
test written in the framework could see it. Fixed 2026-08-10, with two tests
pinning both directions.

### 8.1 `reads` — "say so when you cannot read it", from discipline to mechanism

<!-- pinned: kernel/analysis/subject_files.py::readable -->

The principle is written in the docstring of the symbol pinned above:
**answering "nothing wrong here" without having looked at anything is exactly
the failure this layer exists to refuse.** **At the time** 15 checkers followed
it and 8 did not, and the difference was purely which author had thought about
it. **A rule that holds only if 27 authors each remember it is not a rule, it is
a list of who was careful.** (This sentence used to quote the `route-auth`
checker's docstring, and that file was removed in `a9ae5fb` — a sentence citing
a file that does not exist is worth as much as a sentence nobody wrote.
`kernel/analysis/subject_files.py` and `docs/EVIDENCE.md` each still carry a
citation of the same docstring, and neither was in the scope of that cut.)

So a checker no longer decides for itself:

| | |
|---|---|
| It declares | `reads: ["**/*.py"]` / `["**/*.md"]` / `[".v4/*.json"]` / `["**"]` |
| The kernel judges | whether this **repo** (not this task's scope — scope says where work may go, not what a checker may read) has a file that matches |
| None → | it does not run, and records `UNSUPPORTED`. **A program that never executed cannot possibly report a clean repo** |
| Nothing declared → | `registry-consistency` reports the registry as wrong. `**` is a true statement; blank is not |

`v4 install` uses the same test: a checker with nothing to read is not
installed, and **the census has to be taken before `copy_files`**. Install
copies 83 `.py` files in, so asking "does this repo have Python" afterwards is
asking about the framework, and every Python-AST checker installs itself into a
Go repo on the strength of its own source.

Measured (**at the time**, 2026-08-09):

```
Go repo     23 installable kinds at the time → 6 installed, 17 skipped (each naming what it reads)
            after check: false greens 0 (previously 8)
Python repo 23 installable kinds at the time → 22 installed, 1 skipped (bundle-secret, no JS/TS)
```

Those numbers are **that day's**. Thirteen checkers added `**/*.go` after
2026-08-21 and the same Go repo now installs a great deal more — the mechanism
did not change, what changed is how many checkers can read that tree. For
today's numbers, run `v4 install --check` in the repo: it says, kind by kind,
installed or not and why, and writes nothing.

## 9. Where engagement lands
<!-- pinned: kernel/engagement.py::judge -->
<!-- pinned: kernel/engagement.py::rule_for -->

### Its place among the four layers (build it wrong without this)  <!-- count-exempt: besides the four layers the diagram carries two lines on how a rule moves between them, so 6 lines are not 6 things -->
<!-- pinned: kernel/engagement.py::rule_for -->
<!-- pinned: kernel/engagement.py::required_for -->

```
① general doctrine   always in context   no gate      honest that it has no mechanism
       ↓ the mechanisable part sinks
② checker            runs automatically  it fires     ← most rules belong here
       ↑ a reviewer finding rises (§8.5, closed by red-green)
③ reviewer lens      raises claims, gives no verdict
④ engagement         conditional, no default   blocks the work, not the ship
```

**Layer ④ is not "make a person type something". It is "a rule that cannot be
mechanised, appearing once at the moment it applies".**

So **every kind with `engagement: true` in `claim_kinds.json` must carry a
`rule`**:

```json
"external-write": {
  "engagement": true,
  "rule": [
    {"text": "…a sentence or two, not a document…",
     "source": "PERFORMANCE_OPTIMIZATION_GUIDELINE.md §5.2 (MUST)"},
    {"text": "…", "source": "…",
     "from": "R-266ad43f"}
  ]
}
```

`rule` is a **list** — one object per rule, each carrying `text` · `source` ·
optionally `from`, the id of the row in `.v4/rule_dispositions.json` it landed
from. (This example showed a single object for as long as the schema took only
one; the registry has stored a list since a kind first carried two.)

`v4 engage --claim <id>` with no `--text` prints **all** of that kind's rules
(numbered when there is more than one).

**The unit is one claim, one sentence.** Not one per kind and not one per rule —
`required_for` asks per claim. **So a task touching 19 send sites needs 19
sentences**, each ≥40 characters, each naming its own file or symbol, and each
non-overlapping with everything in the history.
<!-- pinned: kernel/engagement.py::required_for -->

⚠️ **That cost is real, and the "1–3 sentences per task" the duplicate check
above leans on is an assumption rather than a guarantee.** One kind raising 19
claims on one task is entirely possible. Nothing collapses them — collapsing
would return to one sentence per kind, and that sentence cannot say which code it
is about. **This is a known trade, not an overlooked hole; if phase 3 measures it
costing more than it buys, it goes.**

### Which kinds carry engagement, and why those
<!-- pinned: kernel/engagement.py::rule_for -->

There is one condition for a rule belonging in this layer: **a checker covers
part of it, and the remainder has to be judged by a person at that moment.** A
rule that is fully mechanisable belongs to layer ②; one that cannot be mechanised
at all and hangs off no claim belongs to layer ①.

The ones that carry engagement (this line is checked in both directions by
`spec-coverage` against `.v4/claim_kinds.json`):

<!-- engaged-kinds -->
`external-write` · `fail-closed` · `review-finding` · `scope` · `secret` · `signature-change` · `test` · `test-expectation` · `test-shape` · `test-weakened`

**10 engaged kinds, carrying 29 engagement rules in total.**

**At the time** this line said 17 kinds and 37 rules. The seven that went
(`bundle-secret` · `dal-write` · `dep-provenance` · `dependency` · `route-auth` ·
`secret-chain` · `webhook-replay`) were removed together with their checkers and
detectors, **and their rule text moved into layer ①** — the question is still
asked once per session, it just no longer has a claim machine following it.

`lint` used to be on this list, carrying `engagement: true` and **no rules at
all** — a checkpoint on every task that stopped you and then showed you nothing
but the framework saying "this kind carries no rule; that is a gap, not a
design". Its checker's three criteria (private import, private module access,
import cycle) are all structural facts an AST settles — the analysis layer's
fourth, `LINT-CONFIG-DUAL-TRUTH`, is reachable and the checker does not enforce
it, for the reason in §13.5 — and it is a delta, so it never asks you to pay
somebody else's debt. Nothing is left for a person to say, so it joined the group
that already had nine members: `engagement: false` (11 kinds carry it today, `lint`
among them), with the reason written in `engagement_why` and read out by
`v4 engage`. A kind can carry several rules — the schema took only one at first,
and reading the predecessor's doctrine line by line gave `external-write` 6, and
`secret` and `review-finding` 5 each, which is that shape dropping things by
itself. (Today those three carry 5, 4 and 5: `external-write`'s audit-trail rule
went to a lens and `secret`'s fake-token rule to a checker — §9.1 and §14.)

**This table has to be exactly as long as the line above it.** A kind that
carries engagement and has no row here is one whose reason for stopping you was
never written — and the table really was short by one (`bundle-secret`), because
adding the kind did not add the row and nothing checked. `spec-coverage` now
checks both directions.

| kind (all `engagement: true`) | The half the checker cannot cover | Source |
|---|---|---|
| `external-write` | Whether an effect repeats depends on the provider's semantics | PERFORMANCE_OPTIMIZATION.md §5.2 |
| `fail-closed` | What a catch block actually let through | SECURITY_AUDIT (OWASP A10) |
| `review-finding` | Red-green proves the one place that test covers, not the other places the same fact appears | baseline/step-1 L206 |
| `scope` | "Is this file necessary to the approved outcome" has no mechanical answer. The checker only answers "did it leave the declared globs" | claude_baseline.md L145 · baseline/git-checkpoint.md L25 |
| `secret` | The checker sees only changed files, not the cleartext already gitignored in the tree | CONFIG_BEST_PRACTICES L114 |
| `signature-change` | "Is this caller's default right" needs to know what that caller is for | IMPLEMENTATION_ARCHITECTURE_GUIDELINE.md L75-76 |
| `test` | `test_command` carries a marker filter, and a network test with no marker slips quietly into the only oracle there is | UNIT_TEST_GENERATION §marker |
| `test-expectation` | "Where was the old expectation wrong" has no mechanical answer. The checker only answers "it moved, and the code it judges did not" | CLAUDE.md layer ① Verification |
| `test-shape` | "What would this test assert if it actually ran" and "how large can this set get" have no mechanical answers | UNIT_TEST_GENERATION · PERFORMANCE_OPTIMIZATION |
| `test-weakened` | "Is deleting this test right, or a shortcut" has no mechanical answer. The checker only answers "is there less here than at the base" | V3 c3scrutiny |

⚠️ **This table used to carry seven rows about kinds that do not exist**
(`bundle-secret` · `dal-write` · `dep-provenance` · `dependency` · `route-auth` ·
`secret-chain` · `webhook-replay`), while missing `scope`. The paragraph above
had said all along that those seven went with their checkers and detectors, so
one document contradicted itself two paragraphs apart. `engaged_kinds_explained`
only checked one direction (every engaged kind has a row), so the seven extra
rows were asked about by nothing.

⚠️ **Layer ④ cannot take rules like `stopgap` or `hardcode`.** They have no
detector, so no claim, so nowhere to surface. They belong to layer ①, which
holds 96 doctrine rules — see §14 — and **those two rules themselves** were not there for
some time after this sentence was written: the document assigned them a home and
the home did not have them. `spec-coverage` checks that direction now. **This
sentence used to say "layer ① is empty today", and that "today" went stale with
nobody catching it**: a reader following it would build from scratch something
that already existed.

> ⚠️ **This section was not in SPEC at all, and the consequence was measured:** I
> built from the older SPEC and the engagement I built **carried no rules** — it
> stopped you without telling you what to engage with. The four-layer structure
> and the table placing the guidelines were in `RATIONALE.md` only at the time.
> **Some of the "why" is load-bearing and cannot be split into another
> document.**

### Seven mechanical criteria, no reviewer
<!-- pinned: kernel/engagement.py::judge -->
<!-- pinned: kernel/engagement.py::overlap -->

| Refused | |
|---|---|
| Empty | |
| Shorter than `min_chars` (default 40) | |
| Identical to the `question_template` | Restating the question is not having read it |
| Naming neither the claim's file nor its symbol | |
| Token overlap **against the whole history** above `dup_threshold` | V3 died of one sentence appearing 38,392 times, **across tasks**. Comparing only within this task is empty at 1–3 sentences per task |
| **The `rule.text`'s own words coming back** above `dup_threshold` | Reciting the rule does not count. The rule is already on the screen; what is wanted is what it means for this code — and that half nobody can write for you |
| **Anything in the sentence the secret scanner reads as a real credential** | `v4 ship` writes engagement text into `.v4/ledger_export.jsonl`, which is committed and scanned. Measured: `secret-chain` asked a worker why a handler did not wrap a token, and a good answer describes the `https://user:token@` shape — answering well is what made the ledger uncommittable |

⚠️ **This one is containment, not Jaccard.**
<!-- pinned: kernel/engagement.py::recited -->
It was written as `overlap` (intersection ÷ union) at first, and Jaccard falls as
a sentence gets longer — so pasting the rule and adding words to the end passes.
Measured: a verbatim copy scores 1.00 and is refused; the same copy plus nine
words of "this is important and needs care" scores 0.78 and passes. **The test
written to stop padding was defeated by padding.** What it asks now is what
percentage of the rule's own words came back, with the rule as the denominator,
so no length of tail reduces it. Measured against this repo's own rules: copies
score 1.00, and sentences that really talk about the code score 0.08–0.19.


**The sentence `v4 scope widen` triggers is exempt from the duplicate check** —
a second widen inside one task necessarily reads like the first, and without the
exemption it is necessarily refused, which makes the cheap exit expensive and
therefore unused.

**It blocks the work, not the ship.** Refusing before any work has been done
costs nothing; refusing after twenty minutes is a different mechanism at a
different price.

⚠️ **Enforcement is in `check`, and notification has to be in `derive`** — two
different points, and they were once conflated. A claim exists from the moment
the task opens, so which ones need a sentence is knowable as soon as derive
finishes; but `check` is where a claim is answered, so the gate can only live
there. **`derive` used to say nothing at all**, and a worker saw
`NEEDS_ENGAGEMENT` only after writing the code and running `check` — which makes
"before any work has been done" false in the implementation, and it leaked
through the report rather than through the rule. `derive` lists them now.
<!-- pinned: kernel/cli.py::cmd_derive -->

**No limit and no judge** — all seven criteria are objective and satisfiable, so
it cannot loop.

> ⚠️ **All seven can be satisfied to the letter** (paste the symbol name and pad
> to 40 characters; the seventh needs a credential-shaped string avoided, and
> describing the shape does not require quoting one). This is known: the aim is
> to make the moment happen, not to prove it happened. **If phase 3 finds no
> external signal that it changed anything, it goes.**

### 9.1 A rule landing in a program

<!-- pinned: kernel/analysis/test_token_shape.py::scan -->

A lesson measured in the X/Y experiment, written here because it changed how
rules are assigned.

The base handed to both arms carried a test file with
`bot_token="123:SECRET"` in it. One arm changed it to
`123:TEST-NOT-A-REAL-TOKEN` while writing the `secret` engagement sentence; the
other carried it through all eight tasks, with the `secret` checker exiting 0
every time — **correctly**, because that is not a real credential.

The rule lived in both gates at the time: the front gate had it as `secret`'s
engagement rule, the back gate had one saying "do not commit secrets". **Neither
reached the arm that did not do it.**

Because this is not a question of timing and not a question of perspective:

| The failure | Which mechanism it belongs to |
|---|---|
| You know the rule and did not think of it at the right second | The front gate (engagement) |
| You cannot see what you wrote | The back gate (a lens) |
| **You will forget** | **A program (a checker)** |

No amount of prose in front of a person fixes a failure of the "will forget"
kind. The new `test-token-shape` checker caught **21 of them** in that arm on its
first run. The rule was retired from the front gate afterwards — nobody needs to
write a sentence about it on every `secret` claim any more.

The same lesson is what let `.v4/rule_dispositions.json` gain `landed:
checkers/<name>.py`: `registry-consistency` used to look for rule ids only in
lenses and kind rules, which made **the cheapest destination the whole placement
argument points at the one destination that could not be recorded**.


## 10. What exists, and what is deliberately not built

### `test-weakened` — nobody was guarding the ruler itself
<!-- pinned: detectors/test_weakened.py -->
<!-- pinned: checkers/test_weakened.py -->

`test_command` is the sole oracle for a `test` claim, and `.v4/**` is protected,
so nobody can change that command without leaving a name. **But the tests
themselves are not protected, and nothing was asking about them.**

So the cheapest way out of a red `test` claim was never editing the checker (its
hash guards that) — it is **deleting the failing test**. The suite goes green,
the claim is ANSWERED, and thirteen checkers have nothing to say, because every
one of them looks at the code and none looks at the thing judging the code.

`detectors/test_weakened.py` counts test functions by AST against the diff base
(not lines — a reformatted file is not a weakened one). Fewer of them raises a
claim.

**It does not judge whether the deletion was right.** Removing a duplicate,
merging two cases, renaming a file are all normal. Judging is the signature's
job — removing a test is one of the few things that really should have a name on
it in the commit.

### Lens files come in two shapes, and the brief knew only one
<!-- pinned: kernel/review.py::check_text -->
<!-- pinned: kernel/review.py::lens_brief -->

Across 13 lens files and 303 checks, 185 dict-shaped checks are objects and 118
string-shaped checks are plain strings — because a rule that sank from the
checker layer has to carry the reason it **could not stay there**. The two shapes
do not divide by file: 6 files are entirely objects (`prevention`, `devx`,
`electrification`, `general-rule-one-false-instance`, `request-fidelity`,
`near-miss`), and the other 7 mix both inside one file, with no file being all
strings.

`lens_brief` prints them with an f-string, so those 185 **reach the reviewer
looking like a Python dict literal** — and inside that literal is
`why_not_a_checker`, meaning "no instance of this was found in the target repo
today". **Handing a reviewer the argument for not looking is worse than handing
them nothing.**

The same function also told reviewers to print `V4-CLAIM:` lines. **That is the
defect `.claude/agents/reviewer.md` has already fixed** — only a detector's
stdout and fixture runs are parsed, so what a reviewer printed never left its own
transcript. `dead-wiring` could not catch this one because it **skips
`kernel/`** — and the kernel is where those lines are legitimately parsed. The
fix is a direct test: the brief must say `v4 review add` and must not contain
`V4-CLAIM:`.

**The schema for `.v4/lenses/*.json` now lives in `kernel/review.py::unusable`.**
<!-- pinned: kernel/review.py::unusable -->
<!-- pinned: kernel/review.py::LENS_KEYS -->

The two shapes coexisted unnoticed for a while precisely because nowhere said
which it should be. `LENS_KEYS` is now derived from use (which keys `lens_brief`
indexes), and `unusable` judges each lens: are the four keys present, is `checks`
a non-empty list, does `anti_patterns` contain an empty string — that last one
because an empty string becomes a wordless bullet at the end of a reviewer brief,
which reads as a truncated list. **The schema is not in a document, it is in a
function that fires**, and the two shapes coexisting cannot recur today.

### `layer-boundary` — a module stated its own contract and nothing read it
<!-- pinned: kernel/analysis/layers.py::scan -->
<!-- pinned: kernel/analysis/facts_grammar.py::scan_source -->

Many files under `kernel/analysis/` have docstrings saying "**pure analysis, no
I/O**", and the sentence is wrong: **at the time** (counted 2026-08-27) 12 of the
25 modules read files and 2 run git — `kernel/analysis/subject_files.py` and
`kernel/analysis/test_expectation_diff.py`, with no third. Nothing verified that
sentence — `checkers/layer_boundary.py` compares import edges only and cannot see
I/O — and **nothing verified those two numbers either**: the previous version said
22 modules and 3 running git, and the first name it gave was `dep_provenance`, a
module that has not been in the repo since `a9ae5fb`, which a table earlier in
this same document already said had been removed. So this layer's real contract
is "the judgement lives here, argv and exit codes do not", not "no I/O".

Measured first: of the eleven cross-layer edges in the repo, **only this one runs
the wrong way**.

The repair is not to exempt it but to split `kernel/facts.py`'s two
responsibilities — the grammar (what a pattern is, when it matches, how a table
validates) moved to `kernel/analysis/facts_grammar.py`, while the loader (reading
files, running git, `propose`) stayed and re-exports it. **`kernel.facts.scan_source`
is still the same thing**, so no old path has to be kept — this is not a
compatibility layer, it is one module composing the one below it.

The rule itself lives in `.v4/layers.json`: layers ordered from most specific to
widest, plus a **whitelist of edges**. That direction is deliberate — the legal
edges are few, named and arguable; the illegal ones are every pairing nobody
thought about.

#### `v4 install` drafts a `<repo>/.v4/layers.json.draft`

<!-- pinned: kernel/analysis/layers.py::propose -->
<!-- pinned: kernel/install.py::write_layers -->

`layer-boundary` is `applies_to: declared` plus `needs: .v4/layers.json`, and
**nothing used to propose that file** — so vibeproof's one mechanical
architectural rule required an adopter to write a declaration from nothing before
it turned on at all. `write_facts` had the same shape, and this is the same
repair: after drafting facts, `v4 install` drafts a
`<repo>/.v4/layers.json.draft` beside it.

**What it drafts:**

| Layer | Where it comes from |
|---|---|
| `entry` | facts' `entrypoint_globs` — decorators, `func main`, HTTP handler signatures, all structurally recognisable |
| `dal` | **Only when facts declares `dal_globs`.** Unrecognised means undrafted, not guessed |
| `core` | Everything else |

Fewer than two layers writes no file: one layer is not a boundary.

**`allow` is drafted from every cross-layer import that exists today, so the
first run is necessarily green.** That is deliberate, and it decides where the
work sits:

> An edge listed in `allow` is answered by **deleting a line**; an edge not
> listed is answered by **adding one**.

The same edit, with the burden on opposite sides. A declaration written from
nothing asks you to state every edge that should exist — which nobody knows; one
drafted from the present asks you to state which should not — and that you can
answer while looking at the codebase.

**The edges come from `scan(root, {..., "allow": []})`**, which is asking the
same walk that will later judge them. A draft produced by different logic can
disagree with the program judging it; this one cannot.

The draft overwrites nothing: if `.v4/layers.json` already exists, or the draft
does, it returns untouched — **a draft somebody has already trimmed is precisely
the thing that must not be overwritten.**

Tests and the framework's own files stay out of the draft. Test directories reach
into every layer by design, and drafting them produces a `core -> entry` edge
made entirely of `tests/` importing `checkers/` — flagged as suspicious while
pointing at nothing anybody should change. The framework's own files
(`checkers/`, `detectors/`, `hooks/`, copied in by `v4 install` minutes earlier)
are recognised and excluded by `facts.not_this_framework` — passed in from
outside rather than imported, because `kernel/analysis/` may not import `kernel`,
and that edge is exactly the one `layer-boundary` caught before this rule was
written.

**Not ArchUnit.**
[ArchUnitPython](https://github.com/LukasNiessen/ArchUnitPython) expresses the
same rules and is a pytest library; a checker here is stdlib-only, three flags,
four classes of exit code. **The rule language is worth copying; the dependency
is not.**

All three bypasses are caught, and all three for the same reason: the criterion
walks the AST rather than the text — an import inside a function, an `import x as
_y`, a comment claiming to be permitted, all equally visible.

### `test-expectation` — moving the expectation to match the result is cheaper than deleting a test
<!-- pinned: kernel/analysis/test_expectation.py::changed -->
<!-- pinned: kernel/analysis/test_expectation_diff.py::scan -->

Layer ① has said "**Never edit the expectation to match the result**" since day
one, and nothing ever fired. `test-weakened` counts test functions, so
**deleting** a test is caught and **changing what it expects** is not — and the
second is cheaper: the suite keeps its size, the diff reads as maintenance, and
the claim is ANSWERED.

There is one criterion, and the conjunction is the whole rule: **an assertion's
expected literal moved, and no non-test source changed in the same diff.**
Without the conjunction, every ordinary behaviour change raises a claim — and
that noise is how a checker gets switched off. With it, what is left is a diff
saying "the code did not change and the test now agrees with it".

Compared per test function and per literal rather than per line: swapping the
order of two assertions is not changing an expectation, and a checker that reads
reformatting as a changed expectation spends its credit on reformatting.

**All three bypasses are caught, and two of them are one finding:** moving the
literal into a constant (`EXPECTED = 4`), or replacing `== 3` with
`isinstance(..., int)` — both leave the assertion **carrying no expectation at
all**, and that is itself the change. The third hides behind a `.md` edit, and
the conjunction counts only `.py`.

### Two tables sat there a long time with nobody asking the question between them — asked, then removed
<!-- pinned: kernel/analysis/route_auth.py::is_route -->
<!-- pinned: kernel/coverage.py::report -->

**Built, measured, removed.** This section used to describe how that kind worked
in the present tense, and the kind has not existed since `a9ae5fb`
(2026-08-24) — together with its checker, its detector, and the half of
`kernel/analysis/` that produced the verdict. What is left (`is_route`,
`receivers`) recognises what a route is and judges no auth, and the only thing
importing it is `kernel/analysis/webhook_replay.py`.

The reasoning at the time: the facts table has carried `auth_decision` since the
day it was built (16 patterns, each with a `file:line` somebody read to confirm
it really decides something), and `entrypoint_globs` says where the routes live —
**both on disk, and nobody asking the question between them**. Measured on the
reference repo: **61 route handlers, 3 of them reaching no `auth_decision`
pattern in either the decorator or the body.**

The reason for removing it was measured too: across the 113 questions it raised
703 claims on one task while running only 8 times — a rule scanning the whole
tree per task, charging out of proportion to what it answered.

**What holds that path now.** None of these is a gate, and that has to be said:

| What holds it | What it does |
|---|---|
| Layer ① (emitted by `kernel/doctrine.py`, printed in `CLAUDE.md`) | Asks once per session whether an entry point with no authentication call is a decision or an omission |
| The `security-permission` lens | A reviewer reads it during a sweep, raising claims and giving no verdict |
| `v4 coverage` | Reports operational-risk class #7 (Authorization guard tiering) as having **no mechanism**. That is the durable record of the gap, and it is a command rather than a paragraph |

The exempting half lapsed with it: `facts.public_routes` exempts nothing now,
because there is no rule to exempt from. `docs/FACTS.md`'s Optional fields table
says so. `auth_decision` still refuses to be empty even with no reader for its
verdict — what it buys is forcing you to enumerate who may do what in this
system, and that does not depend on a checker reading it.

### A `test` claim is bound to the change now
<!-- pinned: kernel/redgreen.py::executed_files -->

`review-finding` has refused a test that does not execute the symbol it claims to
close since the day it was built. **Nothing asked the same question of the whole
suite.** A `test` claim passed when `test_command` exited 0, whether or not a
single changed line had run.

Now: if **not one** of the `.py` files the diff touched was executed, a green
suite does not count. The tracer is injected through the same `sitecustomize`, so
pytest, unittest or whatever the repo uses all work.

**A suite that goes green without ever touching the change has proved the suite
works and said nothing about the change.**

⚠️ Only when there is a diff base and the diff touches Python. Reporting "nothing
ran" for a documentation-only change is a checker inventing a finding.

⚠️ This is "at least one", not "all". Requiring every changed file to have been
executed turns into noise immediately in any real repo — and telling "this file
should have been exercised" from "this file having no test is correct" is the
`test-sufficiency` lens's job.

### Two rules recorded as owed now have checkers
<!-- pinned: kernel/analysis/signature_change.py::scan -->
<!-- pinned: kernel/analysis/webhook_replay.py::scan -->
<!-- pinned: kernel/analysis/pysource.py::reachable_nodes -->

Both had been judged "owed", and both reasons needed correcting.

**`signature-change`** — the reason given was "the loose form produces 248 false
matches on the reference repo". All 248 were name collisions: two unrelated
functions both called `process`, and the criterion had no way to know which one a
call site meant. **The answer is not a better heuristic, it is resolution.** Both
`from mod import f` and `import mod; mod.f(...)` say which `f` is meant, and the
repo-module resolution built for `dangling-ref` already answers where `mod`
lives. **Measured on the same repo: 248 → 0.**

**`webhook-replay`** — the reason given was "neither repo has a webhook handler,
so it cannot go red". **That criterion is wrong for a preventive rule, and it is
the same Wald error** — a repo with no webhooks having no webhook defects is
exactly what it should look like.

It asks two questions, and the first without the second is the failure it exists
to catch: **a signature proves the body came from the sender and proves nothing
about when.** A recorded request replays forever with a valid signature —
because it was valid once.

### Something learned four times before it was extracted
<!-- pinned: kernel/analysis/pysource.py::docstring_ids -->

"Prose about a write is not a write" was learned by `dead_wiring` about itself,
and then again by `dal_write`, `test_shape` and `webhook_replay`, each from its
own bypass fixture. It took the fourth before `pysource` was pulled out.

It answers two questions, and the second is the first one's isomorph: **"can this
statement run"** — a `raise` after a `return`, a replay guard after the handler
has returned, a `Semaphore` inside a branch nothing reaches. All three are in the
text and none of them runs. **Every checker asking "does the body contain X" is
really asking "will X happen", and this is the difference.**

A third came with it: a parameter merely **named** `nonce` is not a replay guard.
Reading parameter names was meant to catch `def handler(..., timestamp)`, and a
name is not a guard — what counts is what the body does with it.

### The `bypass/` gate's first run found 13 real evasions across the 20 checkers of the day
<!-- pinned: kernel/register.py::MIN_BYPASS -->
<!-- pinned: kernel/register.py::BYPASS -->

The moment the gate opened, 9 of the **then** 20 checkers were bypassable, by 13
routes in total. All were fixed, and every fix is a real rule:

| checker | The bypass | Why it worked |
|---|---|---|
| `test-shape` | `g = inspect.getsource; g(f)` | It looked at call sites and not references |
| `test-shape` | `p = Path('a')/'b.py'` split over two lines | It looked for literals inside call nodes only |
| `test-shape` | the word `Semaphore` in a comment | A text search counted as a bound |
| `dal-write` | `verb + " INTO brands ..."` | No single literal contained `INSERT INTO` |
| `route-auth` | `_unused = require_permission` | Binding the name counted as auth; it never required a call site |
| `test-weakened` | every body replaced with `pass` | It counted definitions, not live tests |
| `test-weakened` | `@unittest.skip` | The same |
| `secret` | the key split at character 20 | Neither half matched |
| `review-finding` | call it once, then assert on source | The execution trace was satisfied and the assertion still meant nothing |
| `registry-consistency` | `rule.text` set to three spaces | "Is there a rule" and "does it say anything" are two questions |
| `control-plane-budget` | 200 statements on one line | It counted lines, not statements |
| `control-plane-budget` | move the code to an unlisted directory | **A whitelist. Every whitelist dies this way** |
| `dep-provenance` | declare it in `reqs.txt` | It recognised by filename, not by content |

Two of the fixes are inversions: **whitelist → blacklist** (every `.py` except the
excluded ones) and **filename → content** (any tracked `.txt` whose lines parse as
requirements).

And `test`'s own green fixture used `true` — meaning those fixtures encoded the
assumption that exit 0 is a pass. **They were the thing that had to change.**

### Four rules that survived adversarial review now have checkers
<!-- pinned: kernel/analysis/dangling_ref.py::scan -->
<!-- pinned: kernel/analysis/test_shape.py::unbounded_fanout -->

Of 84 rules that looked mechanisable, 7 survived adversarial review (which
demands a real red and a real green). Four were built, and each was re-measured
against the reference repo before it landed:

| kind | Measured on the reference repo | How it narrowed |
|---|---|---|
| `route-auth` | 61 route handlers, **3** reaching no auth pattern | Exemptions go in `facts.public_routes` |
| `dangling-ref` | **6** imports naming something that is not there | 417 → 188 → 6: first excluding `from pkg import submodule`, then `__all__` and PEP 562 `__getattr__` |
| `test-shape` | **71** tests asserting on source text · **2** unbounded fan-outs | 94 → 71: `data.json` contains `.js`, so substring matching had to become suffix matching |
| `dal-write` | **14** DML statements outside the data layer, the first three an INSERT/UPDATE/DELETE in one tool handler | A docstring mentioning `INSERT INTO` is not a write |

| `dep-provenance` (the `always_dep_provenance` detector of the time, since removed) | **0** on the reference repo — and that is not a refutation | Reads git-tracked files only: the first version scanned `.venv/` and its own fixtures, producing 12 and 20 false positives respectively |

**Every one of those narrowings began with a green fixture going red.** What
stands between a rule being true and a rule being writable as a checker is
exactly this.

### One that was built and then removed
<!-- pinned: .v4/lenses/prevention.json -->

`test-marker` — "a test that touches the network needs a marker". **Built,
measured, removed.**

A static scan found 66 test files, and spot-checking the first (an agent loop
test in the reference repo) found 66 signs of mocking: it mocks all the way down.
Telling "really goes out" from "mocked" means following fixtures, `monkeypatch`
and `MagicMock` at once — and **a reviewer looking at the diff sees it at a
glance**.

It went to the `prevention` lens carrying that measurement. **This is the 78th
instance of "the rule is true and not checkable", not a failure.**

### Three kernel-side repairs, each starting by reproducing the defect
<!-- pinned: kernel/runner.py::_run_contained -->
<!-- pinned: kernel/analysis/redaction.py::redact -->

**Containing descendants.** A checker that exits 0 while leaving a background
process behind makes `subprocess.run` wait on the pipe rather than the process
under `capture_output` — measured: a genuine PASS became `TimeoutExpired`,
recorded as CHECKER_ERROR. And a timeout kills only the direct child, so
descendants survive, keep writing, and keep holding locks. Output goes to a file
rather than a pipe now, the checker runs in its own process group, and survivors
are looked for afterwards: **a survivor means the exit code describes an
unfinished run, so it is not an answer.**

| | Before | After |
|---|---|---|
| ordinary exit 0 | 0 | 0 |
| exit 0 leaving descendants | hangs to the timeout → CHECKER_ERROR | exit 5 immediately, saying how many were left |
| an infinite loop | timeout, descendants survive | timeout, the whole group cleaned up |

**Redaction.** A checker's stdout goes verbatim into an append-only table, and
checkers are written by LLMs. `secret_scan.py` prints a redacted version of its
own output — but **that is a checker being disciplined**, and this project's
argument about anything an agent writes is that one of them will not be. A
checker printing its own `--facts`, or printing a config it has just read, leaks
without meaning to. **A floor, not a boundary.**

**The control-plane ceiling (the `control-plane-budget` checker).** The
predecessor grew from 4,308 lines to 37,511 without a line being deleted, and its
65 gate refusals all came from two bookkeeping gates. Its answer is a committed
ceiling, and the reason is right: **every fail-closed rule here is paid for by
every adopter on every task, and a cost nobody can see is a cost nobody argues
with.** This project wrote a target into its own spec and then exceeded it by 37%
with nothing measuring. The ceiling lives in `.v4/control_plane_budget.json` —
raising it is a one-line diff, and **that diff is the argument**.

### `v4 trend`
<!-- pinned: kernel/trend.py::report -->

Every other command answers about one task. And the findings that decided
anything are all arithmetic across tasks — how deep into a task the first
engagement sentence lands, how many claims one file brings in, how many terminal
states are signatures rather than checkers — each of them SQL somebody typed once
and left nothing behind. **A number nobody can run again is an anecdote.**

No agents, no sampling, no thresholds. Nothing here gates anything: it exists so
that "should this mechanism be added" can be a sentence that answers to a
measurement.

### `v4 foresee`
<!-- pinned: kernel/foresee.py::foresee -->

Asked before cutting: for a name defined inside this scope, which file outside it
spells that name by hand. Every measured `scope widen` had the same cause — a flow
id written into a list, a handler name written into a registry table, a digest
pinned byte for byte in a test — and the worker found out when the checker said
the diff had left the boundary.

Two rules take it from noisy to useful, and both come from evidence: **imports do
not count** (the language checks them, renames follow them, and they have never
caused a widen); **a name defined all over the repo does not count** (`repo_root`
is in twenty files, and changing yours breaks none of them).

### `v4 remerge`
<!-- pinned: kernel/remerge.py::stale_after -->

B merges, HEAD moves, and every repo-scoped answer A already gave is about a tree
that no longer exists — while A's worker has exited, because exiting is how a task
finishes. Two questions, one counter.

Subject-scoped ones do not move: they are pinned to their own bytes, and a merge
that did not touch them does not expire them. Capped at three rounds, for the
same reason `ship` caps its own re-derive — at the limit this is a fact about how
those two cuts were made, and a fourth round will not discover it.

### `v4 doctor`
<!-- pinned: kernel/doctor.py::run -->

`run` asks 26 questions today, one `_check_*` function each, and the call list is
the report. Nine of them were each **false in this repo at some point with nothing
saying so**:

| | What happened |
|---|---|
| config | — |
| facts | The file was looked for under one name and emitted under another, so the table was permanently empty and the detectors depending on it found less without a word |
| kind ↔ checker | — |
| checker hash | — |
| detectors | Not one of the seven conditional detectors had ever been registered, because there was nowhere to record it — and a detector that does not run makes a repo look clean rather than broken |
| baseline | — |
| hook | The file existed with no settings file calling it, so every ship report printed DEGRADED for a hook that had never fired |
| CI | It named an audit it could not run, because the ledger lives in `.git/` and a clone does not have it |
| ledger schema | The DB predated a schema change, so every `v4 check` blew up at the insert while the whole suite stayed green |

**None of them is a gate.** It is a report, and every line says what to run.

The predecessor had 4,317 lines of adoption tooling, and its own documents record
the reason: a repo that reports itself installed and is not **is broken silently
in production**. This is the same question, as one command.

Its first run reported two things: this repo's facts file was using another
repo's name, and the hooks existed only as a template.

### CI: a skip caused by the environment is not a green
<!-- pinned: tests/run_without_silent_skips.py -->

`docs/FACTS.md` says of the drift checks that they "should run in CI, not by
hand", and they `SkipTest` when the reference repo is not present. **CI is a
fresh clone.** So every one of them was skipped quietly, the job went green, and
that green meant the checks had not run.

Measured: on the same suite, `unittest` reports `OK (skipped=1)` and this runner
exits 1 and names them.

**A skip for a reason inside the code (a variant was narrowed) stays quiet.** A
skip because the environment is missing something means the suite is reporting a
smaller world than the one it was asked about, and that has to be loud.

### Security: six scanner names inherited, one of them running today
<!-- pinned: kernel/analysis/secret_patterns.py -->

The predecessor had six scanners under `SECURITY_GATE/tooling/scanners/`. After
checking each one (the measurements are in `RATIONALE.md` §16):

| The old scanner | Today |
|---|---|
| `secret-scan` | → `checkers/secret_scan.py`, **running** |
| `dependency-audit` | **At the time** the static half ran for a while and the network half (`pip-audit` / `npm audit`) was never built. **Removed** — across the 113 questions its 156 FAILs were all pre-existing upstream debt, and its 25 FAIL→PASS transitions were all a write into a baseline suppression file |
| `bundle-secret-scan` | **At the time** the static half was registered and the build half was never built. **Removed** — 665 runs raising 0, because `kernel/facts.py` hardcoded `ui_globs` to `[]` and it had nothing to look at |
| `source-map-leakage` | **Not built.** No doctrine clause calls for it, and the target repo has zero `.map` files |
| `static-pattern-scan` | **Not built.** It is a mechanism waiting for somebody to supply rules, not a check |
| `external-scanner` (a plugin host) | **No equivalent.** The predecessor's host was not carried over, so "wiring in gitleaks takes a 20-line JSON" was a cost stated about the predecessor and does not hold here |

⚠️ **`secret_scan.py`'s pattern table is 12 shapes
(`kernel/analysis/secret_patterns.json`), inherited from the predecessor.** The
target repo's own credential list has at least four families that match none of
them: a social graph API's long token, a chat platform's bot token, a 32-hex api
hash, and a generic hex HMAC key. Measured, not guessed. Adding those lines is
this checker's next real piece of work, and it is larger than the false-positive
fixes that came before it.

### The registered checkers (each through the red/green fixture gate)
<!-- pinned: kernel/register.py::register -->

| Checker | The question it answers | Detector |
|---|---|---|
| `test` | Does the repo's declared test command pass | `always_test.py` |
| `scope` | Did the diff leave the declared scope | `always_scope.py` |
| `lint` | Were **new** structural violations added (against a baseline) | `always_lint.py` |
| `secret` | Do the changed files contain a credential | `always_secret.py` |
| `fail-closed` | Can control leave a try/except with nothing having raised | `fail_closed.py` |
| `surface-proof` | Does the surface suite this repo already maintains still pass | `surface_proof.py` (conditional) |
| `runtime-proof` | After triggering it once, is the row in the table that owns the data | `runtime_proof.py` (conditional) |
| `external-write` | Is an outbound write ever read back, and does replaying it apply twice | `external_write.py` |
| `review-finding` | Does the test closing a finding qualify (red-green plus executing the symbol) | — (raised by `v4 review add`) |
| `design-pins` | Does the symbol each `<!-- pinned: -->` names exist | `design_pins.py` (conditional) |
| `signature-change` | After a signature was tightened, did every call site follow | `signature_change.py` (conditional) |
| `dangling-ref` | Does the module an import names actually define that symbol | `dangling_ref.py` (conditional) |
| `test-shape` | Does this file prove behaviour, or only that some text is present | `test_shape.py` (conditional) |
| `test-weakened` | Does this file hold fewer test functions today than at the base | `test_weakened.py` (conditional) |
| `test-token-shape` | Is there a literal in `tests/` that looks like a real credential and does not say it is fake | `test_token_shape.py` (conditional) |
| **`spec-coverage`** | Does every command, path and kind exist; does every mechanism section pin something; **and does the spec mention every registered checker** | `always_spec.py` |
| **`registry-consistency`** | Do the registries agree with the checkers, detectors and fixtures on disk | `always_registry.py` |
| **`dead-wiring`** | Is anything declared with something on one end and nothing on the other (a table with no writer, a switch nobody reads) | `always_wiring.py` |
| **`control-plane-budget`** | Is this control plane still within the ceiling it declared | `always_budget.py` |
| **`test-expectation`** | Did a test's expected value move while the code it judges did not | `test_expectation.py` (conditional) |
| **`layer-boundary`** | Does any import cross a layer the `.v4/layers.json` declaration does not allow | `always_layers.py` |

Two rows above carry more than a question, and the rest of it follows the table.

`runtime-proof` is answered **against this framework itself** —
`tools/runtime_probe.sh` really runs `v4 task` in a throwaway repo, and then
`truth_command` asks back the `task` table that `kernel/ledger.py`'s schema owns.
Measured: 336ms PASS; and with `open_task` changed to skip that write it exits 1
and prints "The trigger succeeded and the row is not there". Throwaway is
required — triggering into the live ledger adds a row on every check, and a proof
whose side effect is data is one nobody can run twice.

`surface-proof` **is answered here too**, and the probe is
`tools/surface_probe.sh`. The kind asks "does the surface suite your repo already
maintains still pass", and a framework's surface is its command line: the
`surface_command` in `.v4/config.json` points at that script, which in a
throwaway repo runs — **through `bin/v4`** — the five commands `docs/USING.md` §1
teaches a newcomer (`init` / `doctor` / `task` / `derive` / `status`), and any
non-zero exit, or any command that should print something printing nothing,
fails. Every test in this repo imports `kernel` directly, so a launcher that will
not start, a subcommand deleted from the parser, and a `--repo` that no longer
resolves would all be green under `test_command`.

> ⚠️ This passage used to say "**always exit 4, and that is correct** … and this
> repo has no second suite". That stopped being true on `04db3e2` (2026-08-20),
> the day `surface_command` landed: a contract saying the thing its own config
> points at does not exist, with no way for a reader to learn from the documents
> where the probe was — `tools/surface_probe.sh` appeared in no document at the
> time.

The throwaway tree is **unique per run** (`mktemp -d`, deleted afterwards), which
is the inverse of `runtime_probe.sh`: the runtime one has to reuse a repo,
because `truth_command` is shared by every proof and cannot move per run, and
runs are separated by `{run_id}`. There is no truth to ask here, so there is
nothing to leave behind. The original used a fixed shared path with an `rm -rf`
on entry, and this repo teaches people to run several worktrees in parallel — two
`v4 check` runs at once would delete each other's trees and report a surface
failure that never happened. When `V4_SURFACE_PROBE` names a location it is used
and not deleted: naming it is owning it.

**A proof has to be able to say which run it is about.** `{run_id}` is
substituted into both `trigger` and `truth`, and a `truth` without it is refused.
The measured reason: `trigger: "true"` against a table a previous run had filled
— exit 0, "the truth owner agreed", zero bytes written.

> ⚠️ The first repair was "ask once before the trigger and refuse if it is
> already true", and it was wrong. That makes the answer depend on what the last
> run left behind: two verdicts from the same bytes, which the registration
> gate's determinism check refuses outright; and a correctly written real proof
> (`created_at > now() - interval '5 minutes'`) fails on a second run inside the
> window. `{run_id}` has neither problem.

**When these two kinds are raised:** not by "is a command declared", but by
**what the repo declared in its facts table**. `ui_globs` says you have a
surface; `outbound_write` says you change the outside. With no command declared
they are still raised → exit 4 → and 4 is not terminal → `ship` is HELD. The ways
out: declare it, or sign once with `v4 risk accept --kind unprovable --scope repo`
(a signature that lapses by itself the day you declare).

Measured on the reference adopter: 7 `ui_globs`, 51 `outbound_write`, 8 Playwright
specs somebody maintains — and `web/ui/e2e/**` appearing **0** times across 433
claims. While the condition was "is a command declared", not turning it on
counted as not having one.

**Why this holds for any stack**: both are facts the repo wrote, not directories
or filenames the framework guessed. **Deliberately not done**: scanning
`package.json` for playwright (serves Node only), scanning `db_migrations/*.sql`
(serves SQL migrations only).

**21 checkers, the same length as `.v4/checkers.json`.** That number is settled by
`spec-coverage`'s numeric assertion (`counted_claims` reads `N checkers` against
the registry's length), so adding a kind without editing this sentence FAILs.

⚠️ **But nothing guards which rows are in this table.** "Mentioned" means
**anywhere in the spec**, not in this table. Measured 2026-08-13: `dal-write`,
`signature-change` and `webhook-replay` were absent from this table the whole
time (they were in the engagement one), and `spec-coverage` was green because
they were mentioned elsewhere.

⚠️ **And the other direction is worse, and this line is measured:** after
`a9ae5fb` (2026-08-24) removed six kinds, this table went on listing
`dependency`, `facts-coverage`, `bundle-secret`, `dal-write`, `webhook-replay`
and `secret-chain` for three days, along with six checker filenames that did not
exist, while `v4 accept --docs` was entirely green. The reason is in
`kernel/spec_coverage.py` (the judgement behind `checkers/spec_coverage.py`): the pattern it recognises a kind by is `` `x`
claim ``, and a kind name written in the first cell of a table is invisible to it
— the checker's own docstring says so. **A table describing what exists today was
an assertion nobody was asking about.**

### ⚠️ "332 obligation categories" is not a taxonomy
<!-- pinned: kernel/coverage.py::REUSABLE -->
<!-- pinned: kernel/coverage.py::report -->

Reconciled against the actual event store: **557 unique obligation ids across 51
"families"**, and

```
366 of them (two thirds) are PO-DL-nnn — generated per phase, never recurring
genuinely reusable: PO-4 (6) · PO-5 (17) · PO-6 (5) = 28
```

**The number 332 is not in this data.** Counting families gives 51, counting ids
gives 557, counting the part that recurs gives 28. **Measuring coverage against
either of the first two is measuring against a denominator that is mostly
one-off by construction.**

Coverage today: `PO-4` → `test` and `lint`; `PO-5` → `external-write`,
`fail-closed` and `test`; **`PO-6` (surface truth) is answered by nothing**.

This is a report and not a gate: a gate would fail every task for debt no task
created, and unlike `lint` — **nothing here could ever be retired by a baseline,
because the denominator is mostly one-off by construction.**

### Deliberately not built
<!-- pinned: kernel/ledger.py::audit_chain -->

| | |
|---|---|
| **A ledger writer daemon or a separate uid** | A real boundary needs this. Detection after the fact instead, via the hash chain plus `.v4/chain_head.json`, anchored in CI. **The price: a forgery is valid until it is audited** |
| **Running checkers twice concurrently** | Checked against 11 real concurrency fixes one by one: running them on threads catches 2; the defects it introduces are not concurrency defects; and the evidence lives under `-m integration`, which `test_command` excludes — **so it cannot run its own evidence** |
| **`static-pattern-scan`** | There are no rules behind it |
| **`source-map-leakage`** | 0 `.map` files, 0 `sourceMappingURL`, and no MUST calling for it |
| **A `mutation` checker** | No evidence of defects caught, and a cost that grows linearly with mutants. It became a reviewer lens instead |
| **A blocking benchmark gate** | p95 is polluted by test runs from isolated workers → you get paged for CPU contention. **"Observe-only" is a design position, not a weaker form that already exists** — there are zero benchmark kinds and zero rows of benchmark data today. The day somebody wants to promote it to blocking, they will find there has been no data since day one |
| **A read hook and `read_observation`** | Zero consumers, and the justification is circular |
| **An external scanner plugin host** | The predecessor had a complete one (argv allowlist, `--version` probe, four parsers, fail-closed in three classes). **Not carried over, and it is a decision rather than debt:** zero adopters have asked for any external scanner, and a host's whitelist plus version probe plus four parsers all exist for the second and third scanner. Wiring in gitleaks is a checker wrapping it, roughly 40 lines, through the same three-colour gate. **Extracting a host is worth it the day a second scanner appears.** So "wiring in gitleaks takes a 20-line JSON" was said about the predecessor |

### Not built yet (owed knowingly)
<!-- unbuilt-list -->
<!-- pinned: kernel/spec_coverage.py::unbuilt_list_is_honest -->

| | Why not yet |
|---|---|
| **The merge logic for tasks running in parallel** | **⚠️ This row narrowed, and the old version was again the "claiming something built is not built" error.** It used to say a parallel orchestrator (several tasks at once) was not built, and measured on the reference adopter: **eight worktrees and five tasks open at once**, each with a file-level scope, across two waves already (`wave1/*`, `wave2/*`). The kernel supported it all along — `ledger_path` runs `git rev-parse --git-common-dir`, one ledger serves every worktree, and `hashing.claim_id` mixes in `task_id` precisely so two tasks touching one call site do not collide. `/wave` (§12.5) writes that practice down now.<br><br>**What is genuinely not built is the merge half**: rejoining is a manual `git merge`, the `remerge_max` knob was removed along with it (a knob for something that does not exist is the disease itself), and nothing says anything about two cuts changing the same symbol. `task-splitter` measured **89% clean auto-merge (n=36)**, so this is a one-in-ten gap rather than a blocking one |
| **The relational half of negative constraints** | `--forbid` holds "do not touch this file or directory". It does not hold "a report may not become an authority", "do not change the test to suit the bug", or "do not add an abstraction layer" — the shape those three share is that **the violation is in a relationship, not in a path**, and judging one needs to know which thing is the authority and which the projection, knowledge that lives nowhere here |
| **Real surface and runtime observation** | The read-back half of `external-write` exists; genuine production observation does not. It needs a live environment, credentials, and per-adopter wiring |
| **Secrets in build artefacts** | It needs a build to run **and** a real secret handed to a CI job to prove it did not leak. Using a production secret to prove a production secret did not leak is a worse trade than the leak it is looking for |

> ⚠️ **This table was itself wrong once, in the way that is hardest to catch: it
> claimed things were not built that were.** The four working roles, the two
> rules recorded as owed, and the `degraded` marker were all built and left here
> afterwards, along with one row of garbage from a failed edit — and that row
> disagreed with the row above it about how many subcommands there were.
>
> §0 says anything appearing in this document and not in §10 is built. **That is
> a promise, and nothing checked the other direction.** `spec-coverage` checks it
> now: a `` `name` `` in the **first cell of any row** of this table FAILs if it
> is already a registered checker, a claim kind, or a file that exists.
> **The reason column is not checked** — a row whose reason describes something
> already built is uncatchable by design, and that is exactly the kind this
> warning box calls hardest to catch.

### Built, and this table used to say they were not
<!-- pinned: hooks/write_block.py -->
<!-- pinned: kernel/coverage.py::report -->

| | |
|---|---|
| **The write-block hook** | `hooks/write_block.py`. **It is not a boundary** — hooks are platform configuration, an agent can change them, and a platform without hooks has none of this. The `scope` checker is still the answer itself; this is early warning |
| **A widen triggering engagement** | The `--why` **is** that engagement sentence, run through the same mechanical criteria, exempt from the duplicate check |
| **`bundle-secret`** | The static half. It does not verify build artefacts: that needs a build to run **and** a real secret handed to a CI job to prove it did not leak |
| **Reviewer lenses** | 13 lenses and 303 checks in `.v4/lenses/`. Among those carrying a predecessor doctrine as their source, `STEP_7_AUDIT_LENS` produced `llm-agent-action-surface` (11 checks); `prevention` has 114, demoted from checker candidates. `v4 review lens --lens <name>` prints the brief |
| **Obligation reconciliation** | `v4 coverage`. **And it refutes the "332 categories" claim** |
| **The three-arm experiment (phase 4)** | **Judged done by the repo owner on 2026-08-24; this row moved here from "not built yet".** Three things ran: ① the **2026-06 A/B experiment** over predecessor variants (bare / old / new-split-ON / new-split-OFF / new-fixed / old-fixed), 6 runs, ~$95, ~885K subagent tokens, a pre-registered locked rubric, blind review, mutation tests, Task B — which refuted the intra-phase split (+18% cost / +48% time) and is why `split_pipeline` was removed; the evidence is in the predecessor repo's git history (`git show d44ecbe^:research/2026-06-framework-eval/INDEX.md`). ② **§14's own oracle procedure** ran over two tasks, with the results in `RATIONALE.md` §14.2's ⚠️ box — and that run is where the "two assertions are asymmetric" design hole was found. ③ **The 113-question measurement**, in which the checkers since removed fired 2,589 times between them — the basis on which the `v4-trim-detector-layer` branch removed 11 checkers, recorded in that branch's `.v4/risks/*.json` signature reasons.<br><br>⚠️ **The shape `RATIONALE.md` §14.1 originally described (K = 5–8 tasks × three arms A/B/C, difficulty controlled by running one task three ways) was never run as written** — what ran is the three above. What this row records is that the question is no longer open, not that the original experimental design was executed |

**There is no line target for the kernel.** This line used to say 1,500–2,500
lines, and nothing measured it — the shape the control-plane paragraph above is
about. What holds is the ceiling in `.v4/control_plane_budget.json`: 15,415
statements (`ast.stmt`, not lines — a semicolon does not make a control plane
smaller) over every git-tracked `.py` outside `tests/` and `docs/`, judged by
`control-plane-budget` on every task. Raising it is a one-line diff, and that
diff is the argument.

---

### Lens files have a third field, and the brief never printed it

<!-- pinned: kernel/review.py::lens_brief -->

`LENS_KEYS` says a lens carries four fields: `name` · `source` · `checks` ·
`anti_patterns`. `why` is **not among them**, so it can never be "missing" and can
never be printed — which is the exact shape this repo's own `dead-wiring` exists
to catch, except that it lives inside a JSON field rather than a registry, so that
checker cannot see it.

**Six of the thirteen lenses carry a `why`, and four of those were written before
this was noticed** — `devx`, `electrification`, `prevention`,
`general-rule-one-false-instance`. The last is the most expensive: it
**deliberately has no checklist** (its method is "find a sentence stating a
general rule, enumerate every case it covers, and find the one it gets wrong"),
and the whole method plus three worked examples lives in `why` — while every
reviewer who ran it received a name, a source, and one check.

The fix is to print it, and it stays optional — seven lenses have none and need
none.

### A lens has two modes, and the brief knew only one

`v4 sweep` runs every lens periodically with no task — `ledger.REVIEW_TASK`
exists for exactly this — so there is no diff on that side, and "read the code as
it stands" is the only sentence that makes sense.

But `v4 review lens --lens <name> --task <id>` has always accepted `--task`, and
there is a diff on that side. Some questions **have nothing to ask without one**:
`request-fidelity` asks whether the thing you wanted arrived, and with no request
there is nothing to ask. The brief used to print the sweep sentence to a reviewer
who had a task, putting two contradictory instructions on one screen — "this is
not a diff review" above six checks about the diff. And that sweep sentence had
been written to end the same contradiction.

`lens_brief` takes `task=` now: with one it says "read this task's diff", without
one it keeps the original sentence.
<!-- pinned: kernel/cli.py::cmd_review -->

### The twelfth lens: did the thing you wanted arrive

<!-- pinned: kernel/review.py::raise_finding -->
<!-- pinned: kernel/request_cover.py::entries -->

`.v4/lenses/request-fidelity.json`. It asks something no mechanism here asked —
**not "how did you do it" but "did the thing you wanted arrive"**. The measured
basis is in §4.7: three of eight briefs came back short, and no mechanism raised
a claim.

**It is not a checker, and that is not a matter of taste.** §4.7 says a checker
that reads the request and judges whether you did it must not be built — V3
derived obligations from prose, so editing a sentence edited the obligation set:
nine rounds in one phase, two and a half hours, zero lines of code. So this
raises `review-finding` claims and gives no verdict, and a claim is closed by a
test: red at the parent, green at HEAD, and really executing the symbol. The
judgement stays with people and tests.

**`kernel/request_cover.py` has a reader again.** The recording half of `v4
cover` survived all along (479 lines, §4.7, two pins), but after its checker was
removed in `a9ae5fb` nothing told anybody to run it — meaning the defect its own
docstring describes had recreated itself. The lens does not put that gate back
(the owner explicitly did not want it); it tells a reviewer to use `v4 cover
--task <id> --show` to get the request verbatim and the spans nobody has spoken
for, as a map. **A map is not a checklist**: a span nobody spoke for was not
necessarily left undone, and the lens has an anti-pattern saying in as many words
that reporting one on that basis alone is not allowed.

Seven checks, all dict-shaped, each carrying its `why_not_a_checker`. Eight
anti-patterns, the first of which is "do not report doing too much" — this lens is
**one-directional**, and doing too much is the `scope` claim, which is already
asked once per task.



### The thirteenth lens: it passes, and it is not the thing

<!-- pinned: kernel/review.py::lens_files -->

The twelfth asks whether the thing you wanted arrived. This one asks **whether
what arrived is that thing**, and they are different failures: something that did
not arrive looks like a gap when you go looking; this is not a gap — **something
is there, it runs, it passes the gates, and it is not what you asked for. It
passes precisely because it resembles it.**

The eight examples in `why` are all measured in this repo, in five shapes, and
there is one check per shape:

| Shape | One example |
|---|---|
| A proxy stood in for the question | `_nothing_seen` counted `.py` to answer "can this be read" — **at the time** 23 detectors reported wrongly on a Go repo (dadac9e) |
| The declaration is larger than the behaviour | `spec_coverage`'s docstring said it verified the checkers the spec names; the code ran one direction only |
| It passes the gate, and not what the gate meant to measure | Four comment lines deleted to get under `control_plane_budget`, which counts `ast.stmt` |
| The red is a red, and not that red | A test recognising a call site by SQL text, three call sites sharing one string, green either way |
| It was done, and an assumption beside it became false | `report_max_repeat` counting attempt rows rather than states, so 52% escalate (fw-repeat) |

**It took one check back from `prevention`.** That check, `Request fit`, carried
`why_here: the source document has no matching lens — better here than lost` — a
check left in storage, and the storage was `prevention`, which held 114 of all
303 checks by itself. Yield per check was measured:
`general-rule-one-false-instance`'s 5 checks produce **1.4 findings each**, and
`prevention`'s 114 produce **0.07 each**, a factor of 20. And the `reviewer`
contract says sampling is not allowed — **114 checks is where sampling actually
happens.**

What takes it over, stated: that check had two halves. "Substituting a different
requirement" is taken by the three asking about proxies, about declarations
larger than behaviour, and about passing the gate; "request / implementation /
proof / doc line up" is taken by the two asking about declarations larger than
behaviour and where the red came from, plus `request-fidelity`'s check on the
acceptance bar.

### 10.0 An engagement rule cannot be kept if its checker is not

<!-- pinned: kernel/engagement.py::required_for -->
<!-- pinned: checkers/registry_consistency.py -->

**Every kind in `.v4/claim_kinds.json` must have a `checker` field**, and
`registry-consistency` verifies that kinds and checkers line up. Engagement is a
field on a kind.

So: **engagement is bound to a kind, and a kind is bound to a checker. There is
no way to keep a rule that says "stop before you write" without keeping the
program that judges you after you have written.**

That constraint first bit during the trim. Eleven kinds went, and **7 of them
carried engagement** (`bundle-secret` · `dal-write` · `dep-provenance` ·
`dependency` · `route-auth` · `secret-chain` · `webhook-replay`), while the
reason for removing them measured the **checkers** throughout — "these checkers
fired 2,589 times across the 113 questions and cannot demonstrate their value".

**That measurement does not answer engagement's question.** Two mechanisms, two
moments:

| | When | What it asks |
|---|---|---|
| engagement | **before** the first file is written | what this rule means for the code you are about to write |
| checker | **after** it is written | does what you wrote pass |

"This checker caught nothing" does not measure "that engagement sentence never
made anybody stop before writing". Removing a kind removed both at once, and only
one of them had a number.

**How it is handled today:** the **text** of those 7 rules moved into layer ①
(the first section of `CLAUDE.md`, 96 standing rules) — "where does this value
live before it enters the browser bundle", "an entry point with no authentication
call: is that a decision or an omission?", "a state change in an entry point, a
handler or a human-facing surface may not write to the store directly", "a
signature verifying does not mean it cannot be replayed". The question is still
asked once per session; what is gone is the claim machine that followed it.

**They did not move to a lens, and that was a decision:** a lens is the periodic
back gate (every four days by default), and engagement's value is in the moment
before writing. Moving them there reverses the timing.

What this section records is the constraint itself, not a plan. A rule that
takes effect before writing without a program to judge you afterwards has no
shape in vibeproof today.

### 10.1 The back gate is periodic

<!-- pinned: kernel/sweep.py::due -->
<!-- pinned: kernel/sweep.py::open_work -->

Measured (the X/Y experiment, 2026-08-09): nine lenses run once per task cost
**6.6 times** not running them, and the arm that paid still came back short on
three of eight briefs. The same lenses run once at the end over the same code
took production incidents from three to zero at **1.96 times** the cost — the
same result as the per-task arm at a third of the price.

So the back gate became periodic. And there are only two questions a program can
answer: is it due, and is now a moment when a review reads a settled tree rather
than somebody's half-written work.

```
v4 sweep              is it due, and what would run
v4 sweep --if-due     exits 1 when it is not, so cron stands down
v4 sweep --done --findings <n>    record a sweep: who reviewed, who only took a brief
v4 sweep --history    previous sweeps
```

The schedule lives in `lens_sweep` in `.v4/config.json`:

```json
{"lens_sweep": {"every_days": 4, "weekday": 6, "not_before_hour": 3}}
```

Both `weekday` and `not_before_hour` may be omitted. Four days is the default —
long enough not to become a per-task cost, short enough that a finding is still
about code somebody remembers.

**Four guards, each forced by a measurement:**

| | |
|---|---|
| A task still has unanswered claims | A review against a half-written tree reports that half as findings, and the reader has to work out which half was going to change anyway. That is the noise that teaches people to skip the checklist |
| A task that cannot be read | **Unreadable is not clean.** Treating it as clean is sweeping over work you cannot see |
| A task untouched since the last sweep | The first version swept every task ever opened, and this repo's own ledger has demo tasks nobody is going back to — each of which would block every future sweep. The same trap as `doctor` reporting a count and not the names |
| Interval, weekday and hour | They come from config, and the answer stays the same until that changes — this is a question, not a trigger |

The reviewing, the judgement and the raising of findings are all the reviewer's.
**Nothing here has a kernel calling a model.** `v4 sweep` prints the brief and
records that it happened; it does not review.

#### A brief being printed is not somebody having reviewed

<!-- pinned: kernel/sweep.py::record -->
<!-- pinned: kernel/sweep.py::reviewed_since -->
<!-- pinned: kernel/sweep.py::_lens_events -->

§10.2 is about the three states on the ship side. **The sweep side is the same
fact and the same repair.**

The durable record held only `ran`, and `ran` came from `lens_run` — written the
moment `v4 review lens` prints a brief, and printing a brief is free. Nothing on
the sweep side read `lens_reviewed`, so 13 reviewers taking briefs and none
coming back still recorded `ran: 13` for that sweep — the layer meant to replace
that self-report, wearing the ledger's name.

`record` stores both now, and `v4 sweep --done` prints all three states:

| Printed | Meaning |
|---|---|
| `n of m reported: X (k finding(s))` | A reviewer worked through X and reported k (**k may be 0**) |
| `briefed and never reported back: X` | Somebody took X's brief and did not come back |
| `no brief printed since the last sweep: X` | No brief was printed at all |

**The only difference from the ship side is how a row is recognised as one's
own.** Ship has a task and keys on `task_id = ?`; a sweep has none — by design,
which is what `ledger.REVIEW_TASK` exists for — so `_lens_events` keys on the
window plus "rows carrying no task". `v4 review lens --task T` records somebody
reading **T's diff**, and the back gate reads the code as it stands: two
different subjects, and taking one as coverage for the other reports something
that did not happen.

> **Measured 2026-08-27.** That sweep opened 11 reviewers, all 11 have both
> `lens_run` and `lens_reviewed`, and `v4 sweep --done` printed `12 of 13 printed
> their brief since the last sweep`. The twelfth was `request-fidelity`: its
> `lens_run` rows inside the window were written under `fw-rust`, `fw-reqfid` and
> `fw-tax` — three workers reading three diffs, counted as coverage of a tree
> nobody had looked at.

**Neither side gates**, for the reason given in §10.2: a lens is a judgement, and
gating on whether anybody judged buys a tick. What it buys is those three states
no longer being identical.

`raised_since`'s window was not narrowed to match, and that is not drift:
narrowing a finding count would make a sweep look as though it reported fewer
than the ledger received, and that direction is precisely what this exists to
catch. **A coverage count reported too high hides an absence, and a finding count
reported too low hides an absence — the two windows are choosing the same
thing.**



### 10.2 A ship is not held for a lens not having run, and that is a measured decision

<!-- pinned: kernel/sweep.py::open_work -->
<!-- pinned: kernel/review.py::lens_brief -->
<!-- pinned: kernel/review.py::record_lens_run -->
<!-- pinned: kernel/review.py::record_lens_reviewed -->
<!-- pinned: kernel/lifecycle.py::_lenses_reviewed -->

`v4 ship` prints a `reviewed by: …` line every time and **then ships anyway**.
The line has 3 states:

| Printed | Meaning |
|---|---|
| `reviewed by: X (n finding(s))` | A reviewer worked through X and reported n (**n may be 0**) |
| `briefed and never reported back: X` | Somebody took X's brief and did not come back |
| `no lens has reported on this task` | No brief was printed at all |

**There used to be two, and the missing one was the most common.** `v4 review
lens` writes a `lens_run` the moment it prints a brief, and printing a brief is
free. Two readers took that as "reviewed" — the ship payload's `lenses_run`
(whose own comment says it exists to stop "nobody reviewed" and "three reviewers
found nothing" reading alike), and the `reviewed by:` line. Measured: `near-miss`
had two `lens_run` rows, zero findings, and nobody had read a diff with it, while
both ships that built it printed `reviewed by: near-miss`.

So a reviewer says so when finished, in exactly the sweep's shape:

```
v4 review done --lens <name> --task <T> --findings <n>
```

**`--findings` has no default, and 0 is an answer.** Defaulting it to 0 makes "I
forgot to say" and "I reviewed and had nothing to report" the same row, which is
the same defect one level down. The middle state is the most common one, and it
used to have no words at all — somebody took the brief and walked away.

**Both events are written by `kernel/review.py`, not inserted directly by
`cmd_review`.** Every other write in the review domain (`raise_finding`,
`amend_note`, `bind_closing_test`, `bind_text_change`, `group`, `defer`,
`withdraw_deferral`) lives there; these two, the newest, had been built on the
entry surface, with even the `--findings` refusal living in a parser branch —
meaning nothing but argv could produce them, and nothing was governed by that
rule.

This is not an unwired line but a decision, and §10.1's figures are the reason:
nine lenses per task cost **6.6 times** not running them, and the arm that paid
still came back short on three of eight briefs; the same lenses run once at the
end over the same code took production incidents from three to zero at **1.96
times**. **Gating a ship on a lens is reimposing the per-task arm — buying back a
price the measurement already rejected.**

The second reason is measured volume: of the 113 questions, the 17 tasks that ran
lenses produced **between 9 and 57** `review-finding` claims each, median **27**.
A gate adding 27 things to every task is not a gate, it is a stop.

The third reason is structural: a finding hangs on `ledger.REVIEW_TASK` — the
standing task nobody ships — so "gate the ship on it" is not a flag, it is a
change of structure.

So it stays a report. **An absence is visible** (the line prints every time),
and visibility is this layer's mechanism, for the same reason `v4 sweep` records
which lenses ran: the record is the whole of it, and that is deliberate.

### 10.3 A known_miss needs somebody to run it, not somebody to mention it

<!-- pinned: kernel/analysis/symbols.py::names_in -->

SPEC's fixture rule says: what a checker misses becomes a `known_miss/` fixture,
with a test asserting it returns 0. Measured against that rule's own enforcement:
of five `known_miss` cases, **two had never been run by any test** —
`lint/known_miss/second_site_same_file` and `untyped_collaborator`, both named in
`structural_lint.py`'s docstring, with nothing executing either.
**Stating a gap in a docstring is the prose this directory exists to replace.**

So it is resolved from `.v4/checkers.json` now: the registry already says which
checker a fixtures directory belongs to, so every `known_miss` case is run from
the day it is added, without anybody having to remember to add it to a list.

The same discovery turned up a worse shape: a comment in `test_expectation.py`
said the gap around 409 `insta` snapshot assertions **is** a `known_miss/`
fixture, and that fixture does not exist. A sentence claiming an artefact exists
is worse than one admitting a gap — the first tells the next reader not to look.
A `request-fidelity` reviewer looked.



### 10.4 Which claims hold a ship, and which only report

<!-- pinned: kernel/state.py::split_open -->
<!-- pinned: kernel/state.py::gate_for -->

Every registered kind used to hold a ship as soon as it raised a claim that was
unanswered — with no per-kind switch at all.

**The axis is not importance, it is whether the cost grows while it waits.** Two
questions:

> **A** Does repairing it get more expensive?
> **B** While it waits, does anything keep running on top of the unanswered
> thing?

A yes to either → `gate: "ship"`. No to both → `gate: "report"`. Each kind's
judgement is written in `gate_why`, and that sentence is read out by `split_open`
— **a reason field with no reader is precisely what `dead-wiring` exists to
catch.**

**The default is `ship`, and that default is not a formality.** A kind with no
`gate` in the registry still holds, because the error here is one-directional: a
gate that quietly stopped guarding is a gate nobody installed and nothing said
so; a gate holding when it need not is visible on the first task. `gate_for` also
recognises only the exact word `report` — `"Report"`, `"reports"` and `true` all
hold.

#### The ones that hold a ship

<!-- pinned: kernel/lifecycle.py::report -->

| kind | Why it cannot wait |
|---|---|
| `external-write` | A — an outbound write nobody established: the charge went out and cannot be recalled, and replaying it cannot be recalled either. Waiting does not make the verdict worse, it makes the effect happen during the wait. |
| `fail-closed` | A — a fail-open handler that shipped lets things through quietly in production, and you never learn when it did. During the wait it is not "an unfixed defect", it is "a defect happening and leaving no record". |
| `review-finding` | B — this one is raised by a person, not by a rule. An automatic rule letting through something a person deliberately raised takes the verdict out of that person's hands. There is already a way to postpone it: `v4 review defer`, which demands a durable target. |
| `runtime-proof` | A — an execution that left no evidence is an execution that is over. It cannot be supplied afterwards: what you can supply is evidence of the **next** run, not the one that already happened. Waiting is giving up. |
| `scope` | A — a file touched that should not have been, discovered after a merge, means a revert; and reverting a commit somebody has already built on is not the same order of cost as changing a glob today. |
| `secret` | A — a committed credential is in git history. Fixing it today is deleting a line; fixing it later is rotating, rewriting history and notifying everyone who cloned. The cost jumps an order of magnitude and does not come back. |
| `surface-proof` | A — as with runtime-proof, a surface not proved at the time is a different run when it is proved later. |
| `test` | B — it is this repo's only test oracle. While it is red, **every other claim's green means nothing** — they were all answered on a tree nobody proved works. Postponing it postpones the meaning of everything. |

#### The ones that only report

<!-- pinned: kernel/cli.py::cmd_status -->

| kind | Why it can wait |
|---|---|
| `control-plane-budget` | Neither. Exceeding the ceiling is a number, and it does not move or make anything fail quietly while it sits. **But it is the easiest to postpone to death** — a ceiling that never comes down is made exactly this way, so the three escalation rules matter most here. |
| `dangling-ref` | Neither. A name pointing at nothing points at nothing tomorrow too. |
| `dead-wiring` | Neither. A field with no reader has no reader tomorrow — it is a silent defect, but it does nothing during the wait. |
| `design-pins` | Neither. A pin naming a symbol that is gone: the fix (change the pin, or change the code back) does not get harder with time. |
| `layer-boundary` | Neither. A cross-layer import does not get more expensive to fix later, and it is `declared` — a repo that declared it would hold this line holds it when it chooses to. |
| `lint` | Neither. It is a delta, and the baseline records today's shape, so waiting does not turn "yours" into "somebody else's debt". |
| `registry-consistency` | Neither. An inconsistent registry is fixed by re-registering, and re-registering is one command. |
| `signature-change` | Neither. A caller that did not follow will be reported by the compiler or the tests; and the verdict's question — why that default is right — answers the same later as today. |
| `spec-coverage` | Neither. Documents disagreeing with code: editing the documents costs the same today and next week. |
| `test-expectation` | Neither. An expectation moved, and that fact stays readable forever — the diff does not go anywhere. |
| `test-shape` | Neither. A badly shaped test does not make anything else green; it just proves nothing itself. |
| `test-token-shape` | Neither. As with test-shape: a test asserting on rendered text hides nothing, it just proves less than it looks like it proves. |
| `test-weakened` | Neither. The coverage is already gone, and the verdict is about something that has already happened — signing today and signing before bed **do not change a word of it**. Measured: 42 claims in this repo, 42 correct verdicts, not one false positive — it is not noise, it is a well-aimed question whose timing can move. |

#### May be left, may not be left forever

<!-- pinned: kernel/config.py::DEFAULT_THRESHOLDS -->

Three thresholds, all living in `thresholds` in `.v4/config.json`
(`kernel/config.py::DEFAULT_THRESHOLDS` supplies defaults, so a repo that
declares none still escalates). A `report` kind crossing any of them is treated
as `ship` this time:

| threshold | What it catches |
|---|---|
| `report_max_open` | A pile nobody is clearing |
| `report_max_days` | Something left until nobody remembers it |
| `report_max_repeat` | **One claim failing over and over** — not a backlog, but a rule asking about something the work itself keeps reopening, which no amount of waiting improves |

The third is what the first two miss, and it **counts distinct states, not
attempt rows**. That distinction is not a detail:

> Measured, while it counted attempts: `test-weakened` had 257 failing attempts
> across only **50 distinct states** — 81% of them the same question asked again.
> The worst claim's 10 failures were all in one state, three of them inside the
> same minute. At limit = 5, **52% of `test-weakened` claims would have been
> pulled back into holding** — one rule taking back what another rule granted, on
> the very kind it was written for. Counting states instead, none of the 237
> report claims passes five, and the highest is two.

A "state" is what an answer depends on, which is what can change the verdict: the
subject, the program, and the tree it reads (`subject_digest` · `checker_sha` ·
`worktree`). Two attempts alike in all three asked one question twice.

`exit 4` does not count — a checker saying "I cannot judge this" is not the work
reopening the question; it is a different question with a different exit, and
escalating it here would punish it for being unanswerable in this repo.

**Without these three, the two lists above should not exist.** A report nobody
reads is worse than no report: one more line saying "13 reported" in the ship
report, skipped by everyone a week later. This framework has already recorded the
same failure — the `nothing_seen` field was built because `(ran=True, claims=0)`
was a line nobody could read, and 2,842 of the 3,502 detector runs of the day
said exactly that.

So: **both `v4 ship` and `v4 status` print them**, beside `blocked` rather than
after the verdict. Escalated ones are marked `ESCALATED` together with which
threshold moved.

**`v4 sweep` narrowed too**: a task with nothing unanswered but `report` kinds no
longer counts as busy — it is a task somebody can ship, and a sweep waiting on it
is waiting for work nobody owes.


## 11. One run, end to end

**`v4` is `bin/v4`, wrapping `python3 -m kernel.cli`. `--repo` and `--acceptance`
are global flags and go before the subcommand.**
<!-- pinned: kernel/cli.py::main -->

```
export PATH="$PWD/bin:$PATH"      # or type python3 -m kernel.cli each time

v4 --repo . task   --id t-001 --request "…" --scope "kernel/**"
v4 --repo . derive --task t-001
v4 --repo . check  --task t-001
v4 --repo . status --task t-001
v4 --repo . ship   --task t-001
v4 --repo . audit
```

A measured run:

```
task t-006 open at 71178971e3d0  scope=kernel/**
2 claim(s) from 3 detector(s)
  ok   scope  PASS     52 ms
  ok   test   PASS  20986 ms
re-derive converged in 1 round(s): [0]
chain: intact
detectors: {always_scope: True, always_test: True, fail_closed: True}
SHIP
```

---

## 12. How to add a checker
<!-- pinned: kernel/register.py::verify_checker -->

```
1. kernel/analysis/<name>.py     the judgement itself. No argv, no exit codes.
                                 **When the judgement's inputs are the kernel, it
                                 lives at `kernel/<name>.py`.**
                                 `.v4/layers.json` allows `analysis` no imports at
                                 all, and `spec_coverage` needs both
                                 `config.facts_path_for` and the subject repo's
                                 `kernel.doctrine` — the declaration's comment
                                 records that the one historical `analysis → kernel`
                                 edge was "repaired, not baselined", so declaring a
                                 second is answering an enforced rule with an
                                 exception. What matters is not which directory but
                                 what it can import.
                                 **It reads files and runs git** — 12 of the 25
                                 modules read files and 2 run git (counted
                                 2026-08-27, §10), because what this repo contains
                                 is part of the judgement.
                                 "No I/O" has not held since the day it was written,
                                 and nothing verified it: the layer checker compares
                                 import edges only.
2. checkers/<name>.py            three flags, four exit classes
3. detectors/<name>.py           emits V4-CLAIM lines (or use always_*.py when unconditional)
4. tests/fixtures/<name>/red/    ≥5, each must exit 1
   tests/fixtures/<name>/green/  ≥5, each must exit 0

   **`bypass/` is the third colour, and it is required (≥3 cases, each must exit 1).**

   Red asks whether the checker can tell two states apart. Bypass asks **whether an
   author who knows the rule can get around it** — the same defect rewritten to
   look avoided: a changed import alias, a wrapping helper, a renamed symbol, a
   different suffix. This SPEC used to say the anchor here was a person reading the
   checker's diff, and a person reading a diff is not a mechanism.

   ⚠️ **A bypass case may not be a copy of a red case.** That passes by
   construction and proves nothing — it is ritual. That is what I did the first
   time, and it "passed" for all four checkers.

   ⚠️ **A bypass case whose payload is not itself a violation is not a bypass, it
   is a broken fixture.** I used `AKIAIOSFODNN7EXAMPLE` as a secret bypass, and
   that is AWS's own published example — the checker correctly exempted it.

   A case's settings file is **`<case>/.v4/fixture.json`**, and a case carrying
   `parent_content` **must also have `<case>/.v4/config.json`** — without it the
   case is not a mini-repo, paths resolve against the real repo root, and every
   case fails for a reason unrelated to the checker.

   A case can carry four things, each discovered by a green fixture failing for an
   unrelated reason:
   - its own `.v4/` (a mini-repo case) — a checker that reads config exits 4 forever
     in a directory that has none
   - its own `params` — the same checker in a different variant
   - its own `subject_refs` / `symbol` — what a checker judges is not necessarily
     the case directory
   - **`parent_content`** — a red-green checker (§8.5) needs two states to verify
     anything: the fixture has to supply what the file was at the parent commit and
     what it is now. Without it, a checker verifying whether a test fails at the
     parent only ever sees one state inside the fixture
5. .v4/claim_kinds.json          add a kind
6. v4 register --id … --checker … --fixtures … --kinds …
```

**Python 3, stdlib only.** A checker has to run in any adopter repo.

**Prefer real code for green fixtures.** One false positive taken from a real repo
is worth more than five samples written to fit the rule.

### Baselines: what to do about pre-existing violations
<!-- pinned: kernel/config.py::BASELINE_TEMPLATE=.v4/{kind}_baseline.json -->
<!-- pinned: kernel/config.py::baseline_path -->

A delta checker (`lint` today; `dependency`, until it was removed) starting up in a repo that already has N
violations fails the first task on things it did not cause — **exactly how
`secret-scan.js` accumulated 78 pieces of noise**.

**Every delta checker uses the same shape. None may invent its own:**

```
.v4/<kind>_baseline.json     a list of finding ids, committed
```

| Rule | |
|---|---|
| **A finding id may not contain a line number** | The same reason as §1's claim identity. Use `sha256(rule ‖ file ‖ symbol)` |
| **The file not existing is not an amnesty** | It does not mean "exempt everything". Fail-closed |
| **A worker cannot add to it** | `.v4/**` is in protected_paths → it needs `ACCEPTED_RISK kind=scope_widen_protected` → it leaves a commit with somebody's name on it |
| **Debt already paid may not fail a task** | An entry in the baseline with no matching violation is simply ignored. **Failing somebody for having fixed a violation is the fastest way to teach people around the gate** |
| **Carried debt is printed on PASS too** | Not only on FAIL. An honest record nobody reads is the pantomime §4 is about |

**Do not carry pre-existing violations with signatures:** `ACCEPTED_RISK` expires
against the same key (§6), and a delta checker is `staleness=repo`, so any file
changing means signing again — **once per task, and §6 says more than once per
task means the design has gone wrong.**

**Scanning checkers with `staleness=subject` need one too.** The sentence above
said "delta checker" and `staleness=repo`, and the consequence was that two
subject-scoped ones were left out: a signature expiring against subject bytes has
to be given again when somebody edits the same file beside it. Measured
(2026-08-26) —

| kind | staleness | baseline | repeated signatures |
|---|---|---|---:|
| `test-shape` | subject | ✅ | 8, and **0** after it was written down |
| `dangling-ref` | subject | none — `baseline` is not set on the kind | — |
| `fail-closed` | subject | **added** | 3 places, **9 each** |
| `external-write` | subject | **added** | 7 places in one adopter, **6–7 each** |

Several of the `external-write` ones chose `baseline_raise` as their kind, with
reasons reading "PRE-EXISTING, NOT ADDED BY THIS TASK" — **the person signing was
already asking for that mechanism; there was just no file.**

The id lives in the checker rather than beside `Finding`: `.v4/layers.json`
allows only kernel → analysis, and its own comment records that the last edge
running the other way was repaired by moving. So both checkers call
`kernel.baseline.finding_id` instead of writing sha256 a fourth time.
`partition(complete=False)` was written for scoped runs in the first place — a run
whose subject names two files has not looked at the other four hundred, and those
entries are not stale, they are out of view.

**What a checker misses becomes a `known_miss/` fixture with a test asserting it
returns 0.** An executable known gap beats a promise in prose.

---

## 12.5 The contract on the working side
<!-- pinned: kernel/derive.py::derive -->
<!-- pinned: kernel/analysis/subject_files.py::matches -->
<!-- pinned: hooks/stop_gate.py -->

This section did not exist, and the consequence was that somebody building from
this SPEC built a system **nobody uses**. The table of four roles lived in
`RATIONALE.md` §1 — outside the implementation contract, and governed by no
`spec-coverage`.

**These are not prompts.** Prompts belong in `.claude/agents/` (the monitor's in
`.github/monitor/`, because it does not run inside a task) and they change. These
are 5 **constraints**, each enforceable by the kernel or a hook, which is why they
are contract.

| Role | Produces | May not | Enforced by |
|---|---|---|---|
| `task-splitter` | a task plus a set of scope globs | — | `v4 task --scope` refuses claims outside the scope |
| `worker` | answers to claims | **may not call `v4 ship` itself** | see below |
| `reviewer × lens` | claims, not verdicts | **may not read the worker's rationale**; may not sample | see below |
| `checker-author` | a checker plus fixtures | — | `v4 register`'s gate — fixtures that do not hold cannot be registered |
| `monitor` | claims from a lens sweep, not verdicts; plus **a signature on one detector claim** (`risk accept --as-monitor`) | **may not change the repo it is sweeping** (`.github/monitor/SCOPE.md`); **may not sign any hand-raised claim** — including one another reviewer opened | **At the time** a kind used sweep freshness to hold `v4 ship`; since removed — 294 of its 295 executions were skipped, printed 0 bytes, and forced 87 signatures, because it declared `staleness: repo` and received a task's subject refs. **Layer ③ has no trigger point now.** The signature half is enforced by `claim.origin`: `review add` writes `origin = review`, `scope widen` writes `widen`, and `--as-monitor` accepts only `derive`. **That refusal is wider than the rule, and the wider half is not an oversight**: `origin` records **how** a claim was made and not **who** made it, and two sessions in one repo share a `git config user.email` while `v4` does not know which session raised it — so "is this yours" is a question no column can answer, and adding a `raised_by` would write the same word on both. The guard therefore asks what `origin` can answer (was it raised by a program), and the refusal says what it knows. **`--as-monitor` records `signed_by: monitor` in both the record file and the `accepted_risk` row**, and `v4 ship` and `v4 trend` count the routes separately |

### A worker may not ship its own work
<!-- pinned: hooks/stop_gate.py -->

Shipping is a verdict, not an action. A worker that has answered its own claims
has **no independence** on the question of whether the thing is done — it is the
one being judged.

And the hole in the other direction is larger: **nothing forces it to reach `v4
ship` at all.** A worker can run `v4 check`, see three FAILs, and stop for the day
saying "the main part is done". The ledger is clean, the chain verifies and `v4
audit` is green — because nothing was forged and nobody tried to ship.
`hooks/stop_gate.py` answers that.

### A reviewer reads blind
<!-- pinned: kernel/review.py::raise_finding -->

A reviewer reads a diff and raises claims. It **should not** see the engagement
sentences a worker wrote to answer claims, and should not see the task's
rationale.

The reason is measured: three reviewers produce 3.6 findings each per round, and
**every round they are new ones** — meaning they are samplers, not enumerators. A
sampler that has read the worker's explanation samples along that explanation.

⚠️ **Nothing enforces this today.** The kernel does not know which process read
what. It is a prompt-level constraint, written here because it is part of the
design and not because it holds. **Do not read it as a boundary.**

### Four roles in `.claude/`, and three commands stringing them together  <!-- count-exempt: besides the four roles the block carries the three commands -->
<!-- pinned: hooks/stop_gate.py -->

```
.claude/agents/worker.md            answers claims. May not ship, may not stop with neither
.claude/agents/task-splitter.md     produces the scope, and the forbids the request stated
.claude/agents/reviewer.md          `reviewer` — one lens, reading blind, claims not verdicts
.claude/agents/checker-author.md    a checker plus three colours of fixture, judged by the registration gate
.claude/commands/run.md             `/run` — one cut, from request to ship
.claude/commands/wave.md            `/wave` — N cuts at once, one worktree and one task per worker
.claude/commands/sweep.md           `/sweep` — the back gate, one reviewer per lens, at once, blind
```

**`/wave` and `/sweep` are only the "at once" part, and neither adds a
mechanism.** After `/wave`, each cut runs steps 4 to 7 of `/run` inside its own
tree; after `/sweep`, each reviewer runs `reviewer.md`. Both files exist because
"at once" has one point that depends on a person remembering, and they do not:

- `/wave` — each worker needs `export V4_TASK=<id>`. Without it the write hook
  judges against the **most recent** open task's scope (`hooks/write_block.py`
  says so itself: "Two tasks open and no `V4_TASK`: the guard has no way to know
  which one this is"), measured twice in one day on the reference adopter.
- `/sweep` — reading blind. `reviewer.md` already marks that as unenforced, and
  the only place the discipline can hold is whoever spawns the sub-agents not
  passing it down. Measured: `11 lens(es), 214 finding(s)` in this repo, and the
  same framework in an adopter reporting `11 lens(es) claimed, 1 ran`.

**A prompt is not a contract.** Those five files change, and they belong in
`.claude/`. The contract is this section's table — `v4 task --scope` refuses
claims outside the scope, `hooks/stop_gate.py` stops a session ending without a
ship attempted, and `v4 register`'s gate judges what a checker-author hands in.
**The reviewer reading blind is unenforced, as marked above.**

⚠️ The third boundary `/run` holds — **stop when a ship does not converge** —
rests on `ship_rederive_max` counting from the start of the task. Reissuing it
each time is no limit at all, and every round adds claims to an append-only
ledger.

---

## 13. The command table
<!-- pinned: kernel/cli.py::main -->

Every one of them runs, so every one has to be here. This table did not exist,
and `spec-coverage` only verified that commands the documents name exist, not
that existing commands are named — so nine commands lived outside the documents
for a long time with three meta-checkers green.

**And the reverse check then only asked whether the string appeared anywhere in
SPEC, and anywhere is a large place.** `trend`, `foresee`, `remerge`, `sweep` and
`cover` were absent from this table the whole time while `spec-coverage` stayed
green, because each of the five was mentioned once in some other section. So this
table is now a **list** rather than a scattering of mentions:
`kernel/spec_coverage.py::command_rows` reads the `` | `v4 <name>` | `` row
prefix, and every subcommand in `kernel/cli.py` must have a row of its own here. A
SPEC with no such table (an adopter's is a page and a half) is still only asked
whether it mentions them — a document with no list has no incomplete list.

| Command | What it does |
|---|---|
| `v4 init` | Creates `.v4/` for a new repo (config · claim_kinds · two empty registries). It **does not guess** `test_command`, and writes no facts template — see §13.6 |
| `v4 install` | Copies in every checker, detector, fixture, lens and hook, **registering each one through its own fixture gate**, regenerates `CLAUDE.md`, and writes a `bin/v4`. See §8.7 |
| `v4 accept` | Runs all of this repo's own gates at once, on a tree with **no local state**: the declared test command, every checker against its fixtures, every detector likewise, and the four checkers that ask about the whole tree rather than this diff. It takes the **index** (`git write-tree`) rather than HEAD — what is uncommitted is what needs verifying. `--here` skips the archive; CI runs this. **Why the archive: `.git/v4/` is not cloned, so anything that only holds because of local state dies exactly here** — measured (**at the time**) at `7d8f9c0`: 2 of 31 checkers failed to register in a clone and nowhere else |
| `v4 verify` | Runs one checker against its own red/green fixtures |
| `v4 register` | Verifies first, and enters the registry only on passing |
| `v4 verify-detector` | Runs one detector against "should fire" and "should not fire" fixtures, reporting only |
| `v4 register-detector` | The same, writing `.v4/detectors.json` on passing. **Without this step `derive` will not run it** (§2) |
| `v4 round` | Opens or closes a measurement round (§7, the ruler freezes) |
| `v4 task` | Opens a task. `--base` names the commit the delta gates diff against, defaulting to HEAD — **open the task after committing and the diff is empty, so `scope` reads nothing and goes green**. Naming an older commit only makes the diff larger; no base can hide work, because nothing is newer than HEAD |
| `v4 abandon` | A task's second ending: it will not ship, and it says why. The reason has the same minimum length as `--not-done`, and the claims all stay in the ledger. **It exists because with no `V4_TASK` the hooks guard "the most recent unfinished task"** — without this command a task left lying around becomes the permanent gatekeeper, and the only exit is shipping something nobody did |
| `v4 derive` | Runs every detector and raises claims |
| `v4 check` | Runs the checkers for a task's unanswered claims |
| `v4 status` | Each claim's derived state |
| `v4 cover` | Quotes the request back span by span and word for word, saying what each span got, or declares a deliberate omission with `--not-done --why` (§4.7). **It does not judge** — it only makes the accounting exist, and `--show` prints it back |
| `v4 ship` | Re-derives, then verifies one predicate |
| `v4 risk` | Signs for something that cannot be proved (§6) |
| `v4 coverage` | Where the predecessor's obligations map to here. Reads `.v4/obligation_catalogue.json` (**a filename that had appeared in no document**); a repo without that file does not need one, and this command only means something for a repo inheriting from the predecessor |
| `v4 cost` | What was spent, and who said so |
| `v4 trend` | Arithmetic over the whole ledger rather than one task (§10, "`v4 trend`"). No agents, no sampling, no thresholds — it gates nothing; it makes "should this mechanism be added" a sentence that answers to a measurement |
| `v4 scope` | Widens or narrows a task's scope (§5), or shows how wide it is now |
| `v4 foresee` | Asked before cutting: for a name defined inside this scope, which file outside it spells it by hand (§10, "`v4 foresee`"). Imports do not count, and neither does a name defined all over the repo |
| `v4 remerge` | Asked after a merge: which repo-scoped answers are now about a tree that no longer exists (§10, "`v4 remerge`"). Subject-scoped ones do not move, and it is capped like `ship` |
| `v4 engage` | Writes the sentence a claim asks for (§9) |
| `v4 review` | Raises a reviewer finding, or closes it (§8.5). `group` records several as one fact — measured on one sweep: 224 closed into **26 groups**, averaging 7.4 each. It only records the judgement: those 26 groups span 3.2 files on average, only 9 sit inside one file, and no split by path or lens reproduces them |
| `v4 sweep` | The back gate is periodic (§10.1): is it due, what would run, `--done --findings <n>` to record that it ran, `--history` for previous ones. `--if-due` exits 1 when it is not due, so cron stands down. **It does not review** — it prints the brief and records that it happened |
| `v4 doctor` | Is this repo actually wired, or does it only look it |
| `v4 explain` | Which program judges a claim. From the kind to the checker, the detector, and the module inside `kernel/analysis/` that reaches the judgement — a chain that used to take four manual steps, two of them JSON no command ever printed |
| `v4 facts` | `propose` / `validate` / `verify` / `scan` / `restate` a facts table. The first four were once reachable only through `python3 -m kernel.facts` while `USING.md` says everything goes through `./bin/v4`; `restate` rewrites a row whose cited line has moved (`doctor`'s `facts drift` row points at it) and refuses the ambiguous ones; CI runs `verify --gone-only` |
| `v4 doctrine` | Generates or checks layer ①'s `CLAUDE.md` (§14) |
| `v4 export` | Writes the ledger as portable JSONL |
| `v4 audit` | Walks the attempt hash chain. `--events <file>` walks an exported file |
| `v4 run-checker` | Runs one checker once against a subject file |

### Exit code
<!-- pinned: kernel/cli.py::cmd_ship -->
<!-- pinned: kernel/cli.py::cmd_check -->

**This section did not exist, and three things depend on it:** the CI job,
`hooks/stop_gate.py`, and `/run` deciding whether a ship converged.

| Command | 0 | 1 |
|---|---|---|
| `check` | no claim is blocking | a claim is blocking |
| `ship` | SHIP | HELD, or a re-scan that did not converge |
| `verify` / `register` | every fixture passes | a case did not match its expectation |
| `verify-detector` / `register-detector` | the same | the same |
| `risk accept` | signed | — a refusal (reason too short, wrong kind, `no_accepted_risk`) is **exit 2**, not 1 |
| `audit` | the chain is intact | a row does not line up |
| `doctrine --check` | `CLAUDE.md` is the generated artefact | it is not |
| `run-checker` | **the checker's own exit code, passed through unchanged** (§3's four classes) | |

**`doctor` exits 1 only when a row is BAD, and 0 otherwise** — a `warn` row does
not move it (`cmd_doctor`). It is a report and not a gate: the exit code says
whether the repo is wired, and the first thing that happens to a report turned
into a gate is somebody switching it off in CI.

### Why `v4 export` and `v4 audit --events` exist
<!-- pinned: kernel/ledger.py::export_jsonl -->
<!-- pinned: kernel/ledger.py::verify_exported -->

§4 said, of the hash chain, that "the anchor is CI running audit" — and CI could
never run it: **the ledger lives in `.git/v4/`, and a clone does not carry it**.
So that detection had never existed for a day, and in that placement it could
not exist.

```
v4 --repo . export --out .v4/ledger_export.jsonl   # ship does this itself
v4 --repo . audit --events .v4/ledger_export.jsonl # works with no database
```

`verify_exported` deliberately **does not reuse** the live walk: the live walk
reads the very rows it is judging, and a tampered database feeds it a
self-consistent lie. The export has to stand on its own, because that file is
all an auditor should need.

`.v4/ledger_export.jsonl`, like `.v4/chain_head.json`, is **excluded from the
worktree digest** — the kernel may not move the tree it is judging. `ship`
writes it on both the passing and the failing side: a held task is exactly the
one an auditor most wants to walk, and writing only on success means "auditable
only when nobody needs to audit".

**One file cannot grow forever.** GitHub warns at 50 MiB per file and refuses
a push at 100 MiB, and an adopter's export was measured at 58.7 MB after 23
days, growing 2.34 MB a day. So `export_jsonl` seals: once the open file has
outgrown `SEAL_AT` it is renamed to `ledger_export.jsonl.0001` (then `.0002`,
and so on) beside the open file and is never written again, and the new open
file opens with a `_segment` row saying how many rows of each table the sealed
files already hold and which attempt hash they ended on. Every row is exported
once. `verify_exported` walks the sealed files first, carries the chain across
each boundary, and reads a missing or misnamed segment as rows gone; every
file, sealed or open, carries its own anchor. Nothing an adopter runs changes:
`audit --events .v4/ledger_export.jsonl` finds the siblings itself, and the
sealed name keeps the open file's name as its prefix so that the worktree
digest and the text-closure check recognise it without being told. Sealing
rather than compressing, because an append-only text file packs into git as
the lines added since the last version and a gzip of it is a whole new blob on
every ship.

### What CI runs
<!-- pinned: kernel/ledger.py::audit_chain -->

`.github/workflows/v4.yml` has a `chain` job: it runs
`v4 audit --events .v4/ledger_export.jsonl`, then checks the attempt count
against the committed `.v4/chain_head.json`. **A chain verified only forwards
falls to having a few rows cut off the end, so the count has to answer to an
anchor outside the database.**

No export file is a failure, not a skip. A job that quietly skips because its
input is missing and a job that is always green are the same job.

### Hook
<!-- pinned: hooks/stop_gate.py -->

| Hook | What it stops |
|---|---|
| `hooks/write_block.py` | A Write/Edit to a path outside scope. **Protected has two states rather than being unexamined** — with a task open it is a scope question, and a glob that covers it (or a widen) passes; with no task open it denies any protected path outright. The second state is the normal one rather than the exception: `ledger.ENDED_TASKS_SQL` unions `repo-review` into ended, so every review and monitor session sits in it. The other half (shell commands) belongs to `bash_guard.py`, and at ship time to the `scope` checker |
| `hooks/stop_gate.py` | Stopping on a task that has not shipped and still has unanswered claims |
| `hooks/bash_guard.py` | A shell command that writes to a protected path |

**The three hooks do not share one output protocol, and the difference fails
very quietly.**
<!-- pinned: hooks/write_block.py -->

| Hook | Event | How it says no |
|---|---|---|
| `write_block.py` · `bash_guard.py` | `PreToolUse` | `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": "…"}}` |
| `stop_gate.py` | `Stop` | `{"decision": "block", "reason": "…"}` |

**Using the wrong one is fail-open with no signal at all.** `write_block.py`
used a top-level `decision` from the day it was written, and `PreToolUse` does
not read that field — so it computed the right answer, printed a refusal nobody
read, and exited 0, while the `hook_seen` event was written as usual and ship
reports went on printing a healthy hook.

Wiring lives in `.claude/settings.template.json`. **Without that file both hooks are files
nobody calls** — `write_block.py` existed that way for a while, and every ship
report printed DEGRADED, because it had never once fired.

`bash_guard.py` covers the other hole: the write hook watches Write/Edit, and
`sed -i .v4/config.json` never goes near it — and that file is the only oracle
every `test` claim has.

**Its judgement is not a regex.** The difference between a `>` inside quotes and
a real redirection is decided once, at tokenisation, and every consumer
afterwards trusts the token kind rather than guessing again. So `echo "a > b"`
yields one word and zero redirects, `echo a > b` yields one redirect, and
neither needs a heuristic.

**It recognises reads, not writes.** The first version was the other way round:
three tables listing which commands write, and anything unrecognised was treated
as harmless. Four routes got through in testing, and none of the four was a
missing idea — each was a missing spelling:
`python3 -c "open('.v4/config.json','w')"` (an interpreter cannot be judged from
argv, because what it was handed is a program), `rm <absolute path>/.v4/config.json`,
`git -C . rm` (a global option taking a value swallowed the subcommand), and
`sed --in-place`. Every canonical spelling of the same write was refused. **A
table that recognises writes stops only the writes it recognises, and the
spellings of a write do not run out.**

So the question is whether it can say the command only reads, and there are
three answers rather than two: it can name the file being written (refuse, with
the filename), `READERS` recognises it as read-only (allow), or everything else
(refuse). The table that now has to be complete is `READERS` — **and the price
of its incompleteness is a refusal that states a reason, not silence.**

**It is not a sandbox, and it says so.** Both refusals above (syntax it cannot
read — command substitution, `eval`, a heredoc, an unbalanced quote — and a
command on no roll call) fail closed only when the command **also names a
protected path**. That conjunction is the point: without it,
`for f in *; do echo $f; done` would be refused too, and the predecessor
measured what that produces — **100% of its blocking came from two bookkeeping
gates.**

The price, stated plainly: `sed` / `perl` / `awk` / `ruby` are not in `READERS`
(treating them as readers means trusting a flag spelling all over again), so
even `sed -n 1,20p checkers/scope.py` is refused and has to be written
`cat checkers/scope.py | sed -n 1,20p` — the protected path sits on the reading
end. `v4` itself is the opposite: its argv carries protected paths constantly
(the fix the hook prints is `v4 scope widen --add`), so an unlisted verb of its
own counts as a read, while `v4 export --out <path>` — the one place it writes a
path taken from argv — counts as a write.

`stop_gate.py` answers a question nobody had asked: **every gate in this system
hangs off `v4 ship`, and nothing requires you to get there.** A worker can run
`v4 check`, see three FAILs, and stop with "the main part is done". The ledger is
clean, the chain verifies, `v4 audit` is green — because nothing was forged and
nobody tried to ship. That is a different class of failure from the ones this
was built to catch: not a false answer, an absent one.

Three endings are accepted: answer them all · sign for one of them · leave the
FAIL standing and say so plainly in the reply. What is not accepted is leaving
with neither.

⚠️ **It blocks once.** The Stop payload carries `stop_hook_active`, meaning this
hook has already blocked this same stop. Ignore that field and **two of the
three routes above become structurally impossible**: a worker who leaves a FAIL
standing and says so hands back the same payload, receives the same message, and
stops again — same input, same output, which is a loop whose only exit is the
route that message does not list. The second time through it allows the stop,
and the FAIL stays in the ledger — **the record is the ledger, not the reply.**

⚠️ **Neither is a boundary; both are friction.** A shell defeats either one, and
the checkers at ship time catch what the hooks caught earlier.

---

## 13.5 Where the judgement corpus lives — these five documents do not carry it
<!-- pinned: kernel/layout.py::CLAIM_KINDS -->
<!-- pinned: kernel/layout.py::DETECTORS -->
<!-- pinned: kernel/analysis/structural_lint.py::RULES -->

**Build from this SPEC and you get the whole mechanism and none of the
judgement.** This section did not exist, and the consequence was a reader who
believed reading it was enough to rebuild the thing.

Kernel, ledger, chain, state, staleness, runner, fixture harness, the four
layers, every command and every exit code — all of that is here and can be
implemented from here. And **every piece of judgement that actually fires and
actually stops a task** lives in the JSON under `.v4/` and the code under
`kernel/analysis/`:

| Corpus | Lives in | How many today |
|---|---:|---:|
| Claim kinds: staleness · detector · checker · engagement rules | `.v4/claim_kinds.json` | 21 claim kinds |
| Checker registry | `.v4/checkers.json` | 21 registered checkers |
| Detector registry | `.v4/detectors.json` | 11 conditional detectors — one entry per conditional detector. `detectors/` holds 20 detector files, of which 9 always_* detectors are exempt |
| The checks a reviewer lens carries | `.v4/lenses/*.json` | 13 reviewer lenses · 303 reviewer checks |
| Where each of the predecessor's 254 rules went | `.v4/rule_dispositions.json` | 254 |
| The catalogue obligations reconcile against | `.v4/obligation_catalogue.json` | — |
| Secret patterns | `kernel/analysis/secret_patterns.json` | 12 |
| Structural lint rules | `kernel/analysis/structural_lint.py` (**code, not data** — all four are AST walkers) | 4 written, 3 enforced by the checker |
| Layer ① standing rules | `CLAUDE.md` (generated, §14) | 96 doctrine rules |

The fourth rule, `LINT-CONFIG-DUAL-TRUTH`, stays in the analysis layer instead
of reaching a checker, and that is a measured decision: across 113 questions and
178 executions it produced **zero findings**, and it needs no declaration to run
at all (it recognises a config module by filename), so that zero is an answer
rather than the kind of zero a gate that was never switched on produces. Its two
fixtures moved into `known_miss/` alongside a test asserting it returns 0 — the
thing it cannot catch written as an executable assertion rather than as a
promise. The other three have numbers: in the same ledger `LINT-IMPORT-CYCLE`
raised 14 FAIL claims, `LINT-PRIVATE-IMPORT` 4, and
`LINT-PRIVATE-MODULE-ACCESS` 2, and no other mechanism in Python asks those
three — `layer-boundary` is the one usually proposed to take them over, and it
raised zero claims across the same 113 questions, because it is
`applies_to: declared` and **at the time** no repo had declared a `.v4/layers.json`
— this repo has one now (§10, §12), so that zero is a fact about those 113
questions and not about the rule. Go is
outside this scope: `go build` itself refuses cross-package access to unexported
names and refuses import cycles (measured on four modules, three refused), so
the only one with anything left to do in a Go tree is precisely the one with no
evidence behind it.

⚠️ **The right-hand column has to name the population it counts, not stand as a
bare number.** `counted_claims` reads `N 個 X` in Chinese, and in English it
reads a count that says which set it is about — `21 claim kinds` and
`303 lens checks` are settled, `21 kinds` and `303 checks` are two of the
sentences §13.5's own history is made of. **At the time** this table said 22
kinds, 22 checkers and 7 detectors while the tree held 23 / 23 / 8. **A format
that escapes its own check is a format with no check.** How many entries a
registry holds is verified in both directions by `registry-consistency` and is
not repeated here.

**This is not a defect, it is placement.** A corpus belongs in data, not in
prose — the predecessor wrote it as 16,266 lines of doctrine, and those words
bought no exit code. **The defect was never saying so**, which made "rebuild
vibeproof from the five documents" sound achievable while what came out would be
a complete mechanism carrying zero rules. §9 records an incident of the same
shape: engagement shipped with zero rules because the assignment table had been
left in another document.

**A new adopter supplies:** `claim_kinds.json` (schema in §8), a checker and
fixtures for each kind (§12), and a facts table (`FACTS.md`). **What they do not
supply is the mechanism.**

### 13.6 How a new repo joins
<!-- pinned: kernel/init.py::scaffold -->
<!-- pinned: kernel/init.py::UNANSWERED -->
<!-- pinned: kernel/facts.py::propose -->

```
v4 --repo . init                              config · claim_kinds · two empty registries
  ↓ fill in test_command                       the only oracle a test claim has; not guessed
python3 -m kernel.facts propose . > …draft     a draft from the repo's real call sites
  ↓ check every seen_at, change kind off proposed
v4 --repo . register / register-detector      each checker / detector passes its gate first
  ↓
v4 --repo . doctor                             says, item by item, what is still missing
```

**`init` deliberately does not do two things.** It does not guess
`test_command` — a guessed oracle hands out a green claim nobody earned. And it
writes no facts template: `validate` refuses an empty `outbound_write` and an
empty `auth_decision`, so a template is a file that **cannot pass its own
validator**, and writing one means shipping a scaffold whose first act is to
fail.

`kernel.facts propose` reads the repo's real call sites, marks every row
`kind: "proposed"`, and `doctor` reports how many rows nobody has checked yet.
**It is a draft, not a table** — it guesses from the verb at the end, so
`requests.get` goes outside and `cache.get` does not.

---

## 14. Layer ①: before work starts
<!-- pinned: kernel/doctrine.py::DOCTRINE -->
<!-- pinned: kernel/doctrine.py::drift -->

Every other layer needs something to set it off. A checker fires because a
detector raised a claim; engagement asks for a sentence because that kind has a
claim. **There is a hole in that structure, and the hole is the largest class of
failure this project is aimed at: a rule the model knows and did not apply at
the moment it mattered.**

"Do not accept a stopgap" has no detector. No AST shape, no exit code, no claim.
So under the four layers it can never become an engagement rule — not because
nobody wrote it down, but because **there is nothing for it to hang on**. It
belongs to the layer that is already there before anything sets anything off.

### Where each of the 254 went — and the mechanism that verifies it
<!-- pinned: checkers/registry_consistency.py::_dispositions_hold -->

`.v4/rule_dispositions.json` records the destination of each one. **A register
like that is worth exactly what the check behind it is worth** — without one,
"nothing was lost" is a sentence, and writing that sentence is precisely how
rules disappear.

| Where it landed (`landed`) | Rules |
|---|---:|
| `CLAUDE.md` (layer ①) | 84 |
| `.v4/lenses/prevention.json` | 113 |
| `.v4/lenses/near-miss.json` | 1 |
| `.v4/lenses/` (the eight that already existed) | 30 |
| `.v4/lenses/observability-debuggability.json` | 1 |
| `.v4/claim_kinds.json` (layer ④) | 19 |
| `checkers/test_token_shape.py` (layer ②) | 1 |
| `checkers/test_shape.py` (layer ②) | 2 |
| `checkers/dangling_ref.py` (layer ②) | 1 |
| `owed` — a rule with no landing site yet, recorded as such | 1 |
| `not-applicable` — a rule recorded as not applying here | 1 |
| | **254** |

**Two moved out of layer ④ on 2026-08-09, and both moves were measured by the
X/Y experiment.** One (the audit trail) bought 2 mentions from 25 exposures at
the front gate, while the same rule was raised 6 times at the back gate and
refused with reasons by both arms — so it went to a lens. The other (a fake
token in a test must say it is fake) was caught by neither gate on the arm that
had not done it, because that one is a memory problem — so it went to a checker,
which found 21 sites in one pass. **This table can carry the `checkers/…` rows
only because `registry-consistency` had no way to record "landed in a program" as
a destination at all (§9.1).**

**`owed` is a `landed` value, not a cell in prose.** This table used to carry a
row saying four were owed on top of the 254, while **every one** of the 254 has a
`landed` value — so adding those four made the total 258: a table proving nothing
was lost, itself four over. Two of the four later got checkers (§10), and for a
while the table did not follow: it folded three checker landings, the one rule
still `owed` and the one `not-applicable` into a row of five that named none of
them. Each is its own row now. **Every cell is counted against the `landed`
values in `.v4/rule_dispositions.json`, by `spec-coverage`.**

**Matched by id, not by text.** The first version compared a prefix of the
wording and reported 141 failures — because rules get translated, shortened and
split when they land, and comparing text is a rule against rewording.

Layer ① is the stated exception: those 78 went through one synthesis into 79
grouped lines, so there is no 1:1 to compare and only the count is verified.
**That is a weaker guarantee, and it should say so rather than pretend
otherwise.**

### A methodological correction: "no instance today" is not a refutation
<!-- pinned: kernel/review.py::lenses -->

Of 114 rules, 24 were overturned in adversarial review for the reason "no red
found in the target repo".

**That is the wrong test for a preventive rule.** "No DML without a WHERE today"
means the repo is healthy, not that the rule is useless — this is Wald's bomber:
you are counting the ones that came back.

So they were not dropped, they landed in a lens: **a reviewer looking at one
diff can judge them, and a checker over a whole repo would misfire.** Each check
carries the reason it was overturned, in four separate classes.

### Where these 96 doctrine rules came from
<!-- pinned: kernel/doctrine.py::DOCTRINE -->

The predecessor's 33 guideline documents, 16,266 lines, read line by line into
**1,934 obligations**. The distribution:

| | Rules | |
|---|---:|---|
| DROP | 1,265 | Belong to a model this project does not have — step, cycle, contract, read evidence |
| ② checker | 242 | Look mechanisable |
| ③ lens | 171 | Need judgement, no mechanical oracle |
| ④ engagement | 77 | Can hang on a claim kind |
| **① doctrine** | **179** | None of the three |

Deduplicated across documents, **254 unique rules** — a rule appearing in five
documents means the predecessor said it five times, not that it matters five
times as much. Layer ① received **79 of them, in 10 groups**.

**79 is not a quota, it is a remainder.**

**Another 9 did not come from the predecessor.** They are the eleventh group
(`Building`): do not accept a stopgap, do not hardcode, fix the real gap rather
than its symptom, the simplest implementation that suffices, no compatibility
path kept for its own sake, grow the system in layers, keep it modular, prefer a
maintained library, lean on the dependencies already here. **The predecessor's
33 documents never wrote them down — and not written down does not mean nobody
kept them.** The first three are the ones §9 itself names as belonging to this
layer and missing from it.

Eight more have been added to those ten groups since, which is what a layer
with no 1:1 register looks like from the inside: the total is verified, the
provenance of any one line is not.

### Three properties
<!-- pinned: kernel/doctrine.py::render -->

| | |
|---|---|
| **Generated, not written** | A repo's `CLAUDE.md` is generated from `kernel/doctrine.py` plus that repo's own registries. Add a checker and the doctrine follows by itself. The predecessor maintained overlays by hand — in one of its documents, all seven overlays pointed at a base section that had already been deleted |
| **Short, and the length is itself the discipline** | The predecessor's standing corpus ran to about 3,400 lines and demanded proof you had read it: 621 files read 13,570 times, 349 MB, the same bytes read 14.2 times over. **Removing the proof requirement was right. Removing the corpus with it was not** — that leaves nothing at all. This layer's ceiling is the remainder after subtracting everything a checker will fire on and everything a lens already says |
| **No gate** | Nothing verifies that you read it, and nothing should. "Prove you read it" is the mechanism that produced 349 MB and changed nothing |

### Whether a rule belongs in this layer

It belongs only when all three are false:

```
a checker can fire on it        → layer ②
a reviewer can judge it on a diff → layer ③
it can hang on a claim kind     → layer ④
```

### How it stays uncorrupted
<!-- pinned: kernel/doctrine.py::write -->

```
v4 --repo . doctrine --write     generate
v4 --repo . doctrine --check     compare
```

`registry-consistency` compares `CLAUDE.md` against a fresh render every time.
**An edit by hand, or a registry that changed without a regeneration, is a
finding either way.** A generated artefact anyone may edit is a hand-written
document wearing a misleading header.

Opting in is `"doctrine": true` in `.v4/config.json`, and the opt-in has to be
explicit — inferring it from "every directory with a `.v4/` owes one" turns
every fixture case red, because a fixture is a mini-repo with a config and no
need for standing rules.
