# Using it — from a request to a ship

**This is about order and judgement, not about the contract.** Which flags a
command takes and how a constraint is defined belong to [SPEC.md](SPEC.md).
Restating them here would make a second source of truth, and it would drift:
`.claude/commands/run.md` records drifting once already, saying every task ran
nine lenses when running them had been measured at 6.6 times the cost of not.

Every command goes through `./bin/v4`. Never `python3 checkers/…` directly —
the launcher sets `PYTHONPATH`, and without it a checker dies on its first
import. Python exits 1 on an uncaught exception, which is the same code as
"found a violation".

`bin/v4` is written into your repository by `v4 install`. Until it exists, every
command is the framework's own launcher with `--repo` pointing at your
repository: `<framework>/bin/v4 --repo <your repo> …`.

---

## Before you start, once

A repository adopts the framework in this order, and the order matters:
`install` refuses a repository that has not been through `init`, and there is no
`./bin/v4 doctor` to run until `install` has written the launcher.

```sh
<framework>/bin/v4 --repo <your repo> init       # writes .v4/config.json and the registries; leaves the answers only you know as TODO
# in <your repo>/.v4/config.json set "test_command" to what runs your suite. The others are optional.
<framework>/bin/v4 --repo <your repo> install    # copies checkers, detectors, hooks and lenses; each checker passes its own fixtures first
cd <your repo> && cp .claude/settings.template.json .claude/settings.json    # the hooks fire from the agent's settings, not from the repo
./bin/v4 doctor
```

`doctor` answers, line by line, whether this repo is actually wired or only looks
it. `BAD` is silent in normal use — if you see one, do not start. Warnings do not
block you; on a fresh adoption these are the usual ones:

| warn | what it means |
|---|---|
| `hooks … wired and none has ever fired` | wiring is configuration, firing is a fact; the first Write from a session opened inside the repo turns it green |
| `sweep … no sweep has ever been recorded` | layer 3 has never run |
| `CI … nothing has been exported yet` / `no workflows` | nothing has shipped yet, or this repo has no workflow that walks the chain |
| `open tasks … N unshipped` | with exactly one open and no `V4_TASK`, the hooks guard that one; with two or more they refuse to guess and deny every write until you export `V4_TASK` or end one of them |

**If you want the hooks to actually guard you:** open the session inside this
repo (`cd <repo> && claude`). Claude Code reads `.claude/settings.json` from the
**session's root**, not from wherever you `cd` to — run the commands from
another repo and these settings never load at all.

⚠️ **A sub-agent that `cd`s elsewhere does not change which repo is guarding.**
The hook command resolves its own path from the *session's* toplevel, so the
launching repo's hooks run and judge against the launching repo's protected
paths and ledger. Measured on an adopter running nine worktrees from a session
rooted one directory up: 526 `hook_seen` rows landed in the launching repo's
ledger under no task, 11 denials fired there, the worktrees' own ledgers had
zero rows, and every Write into a worktree was outside the guarded repo — so it
was allowed with nothing recorded. Their ships printed DEGRADED for every lane,
which is the visible half; the invisible half is that the write-time guard was
never on. **Run each lane from a session rooted in the repo that owns it**, and
if you cannot, the ship-time `scope` checker is the guard of record — say so
rather than reading DEGRADED as a formality.

---

## The seven steps of a task

### 1 · Open the task

```sh
./bin/v4 task --id t-xxx \
  --request '<the requester''s own words, unrewritten>' \
  --scope 'core/config/thing.py,tests/config/test_thing.py'
```

**Scope is an allowlist, and it should be narrow.** Not "where I might end up",
but "which files I will actually change".

The cost of writing it wide is measured: a task scoped `core/config/**` that
touched a single file had `fail-closed` raise a claim about a different file in
that directory — existing code the task had never been near. That claim cannot
be answered, and it is not this task's to answer.

**A directory glob pulls in every file already inside it, and the arithmetic is
worse than it sounds.** Measured on an adopter scoping a six-file change as six
`tests/*/**` globs: 58 claims, 53 of them wanting a sentence, nearly all
`external-write` about inherited files the change never opened. The same work
re-opened with the eleven paths named one by one: 9 claims, 4 wanting a
sentence. The failure mode is not the count — it is fifty explanations of other
people's code attached to your task, which is worse than none, because writing
them teaches you that the sentences do not matter.

`--request` takes the original wording. `v4 cover` will later ask you to quote
it **verbatim**, and a rewrite cannot be quoted back.

### 2 · Derive

```sh
./bin/v4 derive --task t-xxx
./bin/v4 status --task t-xxx
```

Detectors raise claims by reading **code**, not documents. So how much there is
to answer is decided by what is actually inside your scope.

### 3 · Engage — the sentence before the work

```sh
./bin/v4 engage --claim <id>              # print this kind's rules
./bin/v4 engage --claim <id> --text '…'   # what they mean for this code
```

**Not a restatement of the rule** — the rule is already on the screen in front
of you. Say what it means *for this code*. Seven mechanical tests judge the
sentence and no person does. It has to be forty characters or more, not the
question restated, not the rule's own words, not a near-copy of anything written
before on any task, and free of anything a secret scanner would read as a live
credential — and when the claim names a file or a symbol, the sentence has to
name it too. Repo-wide kinds (`scope`, `secret`, `test`) have no file to name, so
that last test cannot fire for them; the other six still apply.

This step is not paperwork. Measured once: a request asked for "the error
message should carry the value it rejected", and writing the `secret` sentence
was where it surfaced that one of the rejection rules is *"this URL carries
userinfo"* — so doing as asked would have written a password into the log and
into every traceback. **That was found before the work, not in code review.**

### 4 · Do the work

Change code, write tests. Nothing special, with one exception:

**Touch any file and every repo-scoped claim's answer expires.** That is
deliberate, and it closes a real route: write a stub that passes, collect the
greens, write the real thing without committing it, ship — with a clean ledger
and a chain that verifies.

### 5 · Cover — account for the request, clause by clause

```sh
./bin/v4 cover --task t-xxx \
  --quote '<a span of the request, word for word>' \
  --symbol 'path/to/file.py::name' \
  --test   'tests/x_test.py::test_name' \
  --acceptance 'what done means, in the requester''s terms'
```

| rule | why |
|---|---|
| `--quote` is verbatim and may not straddle two clauses | one quote swallowing the whole request is the receipt that says "goods, one batch" |
| `--symbol` / `--test` must exist | they are resolved against the repo. Measured once: four entries named symbols that had been invented, and it was accepting them and reporting 100% PASS |
| not doing a clause is allowed | `--not-done --why '…'`. **Silently not doing it is not** |

Got one wrong:

```sh
./bin/v4 cover --task t-xxx --withdraw --quote '…' --why '…'
```

The ledger is append-only, so a withdrawal is itself an append — both rows stay,
and `--show` lists the withdrawal and its reason. **Withdrawing without
replacing is not an exit:** that clause becomes one with no entry at all, and
the `unspoken` check catches it immediately, at 91% character coverage or any
other number.

### 6 · Check

```sh
./bin/v4 check --task t-xxx
```

Cheap claims run first. While a cheap one is still failing, an expensive one
(`test`, say) is held back — because the edit that answers the cheap failure
expires the expensive one's answer before you can read it.

```
--   8e04f70614e20035  test  not run (567s)
1 expensive claim(s) not run: something cheaper is still failing.
  v4 --repo . check --task t-xxx --all      # run them anyway
```

`--claim <id>` asks for one by name and is never held back.

**Three ways out of a FAIL, and all three are accepted:**

| way | when |
|---|---|
| change the code | what it says is true |
| sign it, `v4 risk accept` | this repo cannot answer it, structurally. `status` shows RISK_ACCEPTED at once; **`ship` is held until the signature file under `.v4/risks/` is tracked by git** — commit it |
| `v4 cover --not-done --why` | this part was decided against |

**The fourth way is the one that is not accepted: stopping with neither.**

### 7 · Ship

```sh
./bin/v4 ship --task t-xxx
```

Ship **re-derives until it converges**. The first run often comes back `HELD`,
because the re-derive found a claim `v4 check` never saw — you changed something
after deriving. **Ship does not run checkers for you; it refuses to accept a
claim that has no answer.** Run that one, then ship again.

Shipping writes `.v4/ledger_export.jsonl` — **commit it**, CI walks the chain
through that file. It exists only once something has shipped; before that,
`./bin/v4 audit --events .v4/ledger_export.jsonl` has nothing to read. Once
the file outgrows 25 MB a ship seals it as `ledger_export.jsonl.0001` beside
a fresh open file — commit the sealed one too, and never edit it; the walk
crosses from one to the next by itself.

---

## When the scope is not enough

```sh
./bin/v4 scope widen --task t-xxx --add path/to/file --why '…'
```

**This is the cheap exit, and it is meant to be used.** It is one event, not a
re-plan: nothing is re-split and nothing already answered is re-run. The reason
has to be long enough, and it has to name the path.

Going around it is what costs: `scope` runs at ship, which is where you find out
that twenty minutes of work went into the wrong file.

Widening onto a protected path (`.v4/**`, `checkers/**`, `detectors/**`,
`.github/**`) needs a signature — changing what judges you is not something a
task does quietly. The widen itself is accepted and prints the `risk accept`
command; the `scope` checker enforces the signature at check and ship once the
protected file is actually in the diff. `CLAUDE.md` is not a protected path: it
is generated, and `./bin/v4 doctrine --write` regenerates it.

---

## Dropping a task

```sh
./bin/v4 abandon --task t-xxx --why '…'
```

**Do not ship a task you did not do, just to close it.** Abandoning is a task's
second ending, and the claims stay in the ledger — what was found does not
become false because nobody followed it up. `--why` has to be at least 40
characters, like every other reason this framework records.

That has a practical consequence. With no `V4_TASK` set and exactly one task
open, the hooks guard that one, so a task left lying around becomes the one
guarding you. With two or more open they refuse to guess: every write is denied
with the list of open tasks until you export `V4_TASK` or end one of them.
`v4 doctor` has an `open tasks` line naming them.

---

## Layer 3 — the lens sweep

```sh
./bin/v4 sweep            # is it due?
./bin/v4 sweep --if-due   # exits 1 when it is not, for cron or CI
```

Lenses do not run per task; they sweep periodically (every 4 days by default, in
`lens_sweep` in `.v4/config.json`). **Not a preference, a measurement** — nine
lenses per task cost 6.6 times not running them.

There is a second gate: **it will not run while a task is unanswered.** Reviewing
a tree somebody is still writing in reports half-finished state as findings, and
that noise is what teaches people to stop reading the checklist.

Once due, each lens gets its own reviewer, in parallel, reading **blind** — do
not pass the worker's engagement sentences down. A sampler that has read the
explanation samples along it.

---

## Updating the framework

```sh
cd <framework repo> && ./bin/v4 --repo <adopter> install
```

- change `kernel/**` → **live immediately**, no install needed (an adopter has no
  kernel of its own; `bin/v4` puts the framework's on `PYTHONPATH`)
- change `checkers/**` / `detectors/**` → install
- install takes **~10 minutes** on a 5,400-file repo and about two on a
  three-file one. It is slow because every checker really does run its own red /
  green / bypass fixtures. **Worry when it is fast** — it was fast once because
  every checker was dying on import: 2 of 23 kinds registered on the reference
  adopter, and install reported success.

After a first install, `git status` shows new directories (`checkers/`,
`detectors/`, `hooks/`, `.v4/lenses/`, `.v4/fixtures/`), a new `bin/v4`, and two
edits to files you own: a short pinned-example block appended to your
`README.md`, and `.v4/home` appended to your `.gitignore`. Commit them together.

After a later install, `git diff` shows a pile of `checkers/*.py`. **Do not panic
and do not revert** — `.v4/installed.json` records each file's sha, and `scope`
and `test` can both tell "you did not write this". Change one byte and the sha
stops matching and it is reported: **the exemption only recognises
"unmodified".**

---

## When not to trust a green

More than half of what this framework catches is "a mechanism that looks like it
is working and is not". So:

**Before saying "this gate works", answer "have I made it fail?"**

Measured several times, and it found something every time:

| what was broken | what should happen | what did |
|---|---|---|
| a checker changed to `sys.exit(0)` | install throws it out of the registry | ✅ it does, and `doctor` reports the hash mismatch |
| `mv` a baseline file away | `doctor` says so | ✅ it does — and that check had never once run since the day it was written |
| withdraw a cover entry and not replace it | `unspoken` catches it | ✅ caught |

Put it back with a **literal path**, never one assembled from variables.

---

## One task, end to end

```sh
./bin/v4 doctor                                    # 0 BAD?

./bin/v4 task --id t-tg --request '…verbatim…' \
  --scope 'core/config/chat_endpoint.py,tests/config/test_chat_endpoint.py'
./bin/v4 derive --task t-tg                        # 9 claims, 4 seconds
./bin/v4 status --task t-tg

./bin/v4 engage --claim <id>                       # read the rules
./bin/v4 engage --claim <id> --text '…'            # × 6

#   ← the code and the tests get written here

./bin/v4 cover --task t-tg --quote '…' --test '…' --acceptance '…'   # × 4
./bin/v4 check --task t-tg                         # 5.7 seconds, expensive held
#   ← fix the FAILs
./bin/v4 check --task t-tg                         # once green, the expensive runs

./bin/v4 ship --task t-tg                          # HELD, most likely
./bin/v4 check --task t-tg --claim <the new claim>
./bin/v4 ship --task t-tg                          # SHIP

git add -A && git commit                           # signatures and the export get committed
```
