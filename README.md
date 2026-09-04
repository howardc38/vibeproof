<!-- Language switcher. Keep this line identical in all three files. -->
**English** · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md)

# vibeproof

### Your coding agent says "done". This makes it show the evidence.

vibeproof is a small set of programs that sit beside a coding agent inside your
git repository. Every time the agent finishes a piece of work, each thing it
could be wrong about becomes a **question**. A program answers the question with
an exit code. The answer goes into a ledger nobody can edit. **Nothing ships
with an open question** — the agent either fixes the code, or a person signs
their name to the risk.

It does not write code, does not replace your CI or linter, and does not judge
whether the design is right. It answers one narrower thing: **"you said you did
that — where is the evidence?"**

---

## Is this for you?

Read the left column. If two or more rows are things you have actually been
burned by, keep reading.

| Your pain | Does vibeproof address it? | How |
|---|---|---|
| The agent says "tests added, all green", and the test **never called the function** — it only checks that some text appears in the source | **Yes.** | The Python interpreter itself reports which functions ran (`sys.settrace`). A test that never entered the function cannot close a review finding. `test-shape` also flags a test that reads source text, before it is even run |
| The agent "refactored" and **quietly deleted two tests**; the suite is green because of the deletion | **Yes.** | `test-weakened` compares the test functions at the base commit with the ones now |
| The suite is green but **never executed the file the agent changed** | **Yes.** | The `test` checker runs your test command under a tracer and refuses a green run that touched none of the changed files |
| "Added error handling" — and the `except` **swallows the failure** of a payment call, a webhook, an auth check | **Yes**, for Python, Go and TS/JS. | `fail-closed` finds a try/except around an outbound or auth call that control can leave with no exception raised |
| An outbound write (charge, post, publish) with **no read-back**, or one that **posts twice on retry** | **Yes**, once you tell it what counts as an outbound call in your codebase. | `external-write` reads a small vocabulary file you own (`.v4/facts.<repo>.json`) and asks both questions per call site |
| The agent **edited files it was never asked to touch** | **Yes**, at the moment of the write. | A hook checks every Write and Edit against the task's declared scope and refuses the ones outside it |
| A credential got **committed** | **Yes.** | `secret` scans the files the task changed and redacts before printing |
| A scanner printed `PASS` having **opened none of your files** | **Yes.** | Exit code `4` means "I could not read anything here". It is never a pass, and it never ends a task |
| The agent **stopped without finishing**, or without saying it gave up | **Yes**, once. | A stop hook lists the open questions and refuses the first attempt to stop. Fix, sign, or say plainly that you are leaving a FAIL — silence is the one ending it refuses |
| You want to know **later** who signed off on what, against which version of the code | **Yes.** | Every attempt and every signature is a row in an append-only, hash-chained SQLite ledger, and every signature is also a file that has to be committed |
| "Is this the right design?" / "Did it actually do what I asked?" | **No.** | No program here reads your intent. A reviewer lens can raise the question; a person answers it |
| You want to trust the model less | **No**, and not the goal. | It makes trust unnecessary for the mechanical parts. Judgement stays yours |
| You write mostly Rust | **Barely.** | Two checks read Rust today. See [language support](#what-it-can-read-in-your-language) |
| You want a wall the agent cannot get around | **No.** | This is friction with a record, not a boundary. An agent with a shell can edit a plain SQLite file or drop a trigger. What you get is that the tampering shows in the audit, and that the escape routes each leave a row with a name on it |

---

## What one task looks like

This is a real run from 2026-09-02 against a three-file sandbox repository. You
can reproduce it: the code is below and the commands are in the quickstart.

The agent wrote this and called it "added error handling":

```python
import requests

def charge(card):
    try:
        requests.post("https://api.example/charge", json={"card": card})
    except Exception:
        pass
    return True
```

`v4 derive` raised seven questions about the task. Five of them refused to run
their checker until the agent wrote one sentence about what the rule means for
this code. Then `v4 check`:

```
FAIL fail-closed      src/pay.py:7  in charge  [swallow]
     except Exception guards outbound call `requests.post` (line 6), and control
     can leave this try/except with no exception having propagated

FAIL external-write   src/pay.py:6  in charge  [readback/unobserved-write]
     the outbound write `requests.post` at line 6 discards what it returned, and
     `charge` calls nothing from the outbound-read table.
```

The agent tried to stop. The stop hook answered:

```
3 claim(s) on t-e2e are still open:
  OPEN    fad124898937cac4  external-write
  OPEN    3ce1741909dec365  fail-closed
Three ways to end this properly: answer them, sign for one, or say it failed.
```

After the fix (`raise_for_status()`, and reading the receipt back) both went
`PASS`. Then the `test` question turned red for a different reason:

```
FAIL test   the suite passed and executed none of the 1 changed file(s):
              src/pay.py
            A green suite that never reached the change proves the suite works.
            It says nothing about this change.
```

That is the whole idea in one screen: **green and "actually checked" are two
different facts, and the second one is the one that was always missing.**

---

## Quickstart

You need a git repository, Python 3.12, your own test command, and (for the
hooks) Claude Code. Nothing is installed from PyPI: the framework has zero
third-party dependencies.

**Once per repository.**

```sh
# 1. Get the framework. It stays outside your repo; your repo gets a launcher.
git clone https://github.com/howardc38/vibeproof.git ~/vibeproof

# 2. Scaffold .v4/ in your repo. This writes the config and asks four questions
#    it refuses to guess — the first is your test command.
~/vibeproof/bin/v4 --repo /path/to/your/repo init

# 3. Answer at least the first one.
#    In /path/to/your/repo/.v4/config.json set
#      "test_command": "python3 -m pytest -q"      # or whatever runs your suite

# 4. Install the checkers, detectors, hooks and lenses. Every checker runs its
#    own fixtures before it is allowed in, so this takes a couple of minutes.
~/vibeproof/bin/v4 --repo /path/to/your/repo install

# 5. Wire the hooks into Claude Code, then check the wiring.
cd /path/to/your/repo
cp .claude/settings.template.json .claude/settings.json
./bin/v4 doctor
```

`install` also drafts `.v4/facts.<repo>.json.draft`: its best guess at which
calls in your code write to the outside world. Read it, prune it, rename it to
`.v4/facts.<repo>.json`, and run `./bin/v4 facts validate`. Until you do, the
`external-write` question runs on a generic vocabulary and `ship` holds on every
absence the installer wrote as a placeholder. [docs/FACTS.md](docs/FACTS.md)
explains the file.

**Per piece of work.** Open the session inside the repo so Claude Code loads the
hooks, then:

```sh
./bin/v4 task --id t-1 --request '<the request, in the requester's own words>' --scope 'src/**'
./bin/v4 derive --task t-1                       # what questions does this task raise
./bin/v4 engage --claim <id> --text '<what the rule means for this code>'
#   ... write the code ...
./bin/v4 check --task t-1                        # run the checkers, read the exit codes
./bin/v4 ship --task t-1                         # SHIP or HELD, and why
git add -A && git commit                         # signatures and the ledger export travel with the code
```

[docs/USING.md](docs/USING.md) walks through each step and the judgement calls
at each one.

---

## How it works

### The vocabulary

| Word | What it is |
|---|---|
| **task** | One piece of work: the requester's words, the files it may touch, the commit it starts from |
| **scope** | The list of files the task may change. A hook checks it before every write |
| **claim** | An open question about the code, e.g. "this outbound write — what happens if it runs twice?" It is not a verdict |
| **kind** | The type of a claim. 21 kinds. The kind decides which program answers, and whether an unanswered one blocks a ship |
| **detector** | A program that reads the diff and decides where a question is worth asking. It raises claims and never judges. 20 of them |
| **checker** | A program that answers one claim with an exit code. It judges and never raises. 21 of them |
| **engagement** | For 10 kinds, one sentence the agent must write before the first line of code: what this rule means for the code about to be written. Seven mechanical tests judge the sentence; no person does |
| **signature** | Something no program can prove can be signed for, with a reason. A signature expires against the bytes it saw, exactly like a pass |
| **ship** | The final question: is every claim answered or signed, is the ledger chain intact, is every declared absence confirmed |
| **lens** | A question set a reviewer subagent reads a diff with, blind to the worker's explanations. 13 lenses. It raises findings, never verdicts |
| **ledger** | An append-only SQLite database in `.git/v4/`. Every attempt and signature is a row. Rows are hash-chained; the export is committed with the code |

**Two roles, never combined.** Detectors raise and never judge. Checkers judge
and never raise. You, or your agent, sit in the middle: answer the question, or
sign for it, and either way it leaves a row.

### What a checker's exit code means

| Exit | Name | Means |
|---:|---|---|
| `0` | PASS | looked, found nothing. The claim is answered until any input to it changes |
| `1` | FAIL | looked, found something. The claim stays open |
| `4` | UNSUPPORTED | could not read anything here. **Not a pass, and not an ending** |
| `5` | ERROR | the checker crashed |
| `6` | CHECKER_TAMPERED | the checker on disk is not the registered one. It was not run |
| `7` | SUBJECT_MOVED | the code changed while the checker ran |
| `8` | TIMEOUT | it did not finish |

### Which questions block a ship

Eight kinds hold a ship until answered or signed: `test`, `scope`, `secret`,
`fail-closed`, `external-write`, `review-finding`, `runtime-proof`,
`surface-proof`. The other thirteen only report — until the same kind piles up
(10 open), ages (14 days) or keeps re-failing (5 times), when it blocks too.

### How long an answer lasts

Every answer is pinned to six inputs: the digest of the files it judged, the
config, the checker's own bytes, the detector's bytes, the facts vocabulary, and
(for repo-wide questions) the tree. **Any one moves and the answer goes STALE**
and has to be asked again. Signatures use the same key, so signing is never
cheaper than passing.

### The three hooks

| Hook | Fires on | Refuses |
|---|---|---|
| `write_block` | Write, Edit, MultiEdit, NotebookEdit | a path outside the task's scope; a protected path; any write while an engagement sentence is still owed |
| `bash_guard` | Bash | a shell command that writes a protected path, or that names one and cannot be shown to only read it |
| `stop_gate` | Stop | ending the turn while claims are open or the task is unshipped — once. The second stop goes through and the FAIL stays in the ledger |

All three fail **open**: if a hook cannot reach the kernel or the ledger it
allows the action and says so on stderr. A hook that blocks everything is a hook
somebody switches off.

### Protected paths

Four paths decide how everything else is judged, so changing them costs a
signature, not just a widened scope: `.v4/**` (config, registries, facts),
`checkers/**`, `detectors/**`, `.github/**`.

### The judges are judged first

A checker enters the registry only after passing its whole fixture set: `red/`
must exit 1, `green/` must exit 0, `bypass/` (the same defect rewritten to evade)
must exit 1 and must not be a copy of a red case, `known_miss/` states a blind
spot on purpose. 525 cases today across 21 checkers. A checker that fails this
never judges anything.

---

## The 21 questions

Each line is the `question_template` from the registry, verbatim. The last
column says whether an unanswered claim of that kind holds a ship.

| Kind | The question it answers | Holds ship |
|---|---|:---:|
| `test` | Does the repo's declared test command pass? (and did the run execute the changed files) | ✓ |
| `scope` | Does the diff stay inside the declared scope? | ✓ |
| `secret` | Do the files this task changed contain a committed credential? | ✓ |
| `fail-closed` | Can control leave the try/except at {file}::{symbol} with no exception having propagated? | ✓ |
| `external-write` | After the outbound write at {file}::{symbol}, does anything establish it landed, and does replaying it apply the effect twice? | ✓ |
| `review-finding` | Is the finding at {file}::{symbol} closed by a test that fails without the fix and actually runs the code? | ✓ |
| `runtime-proof` | Did triggering this leave a row in the table that owns the data? | ✓ |
| `surface-proof` | Does the surface suite this repo already maintains still pass? | ✓ |
| `test-shape` | Does {file} prove behaviour, or only that some text is present? | |
| `test-weakened` | Does {file} still hold at least the test functions it held at the base? | |
| `test-expectation` | Did {file}::{symbol} change what it expects while the code it judges did not? | |
| `test-token-shape` | Is there a literal in tests/ that looks like a real credential without saying it is fake? | |
| `signature-change` | Did every caller follow the parameter this change made required? | |
| `dangling-ref` | Does {file} import {symbol} from a module that defines it? | |
| `lint` | Does this task add a structural violation that was not in the base commit? | |
| `layer-boundary` | Does any import cross a layer the declaration does not allow? | |
| `design-pins` | Do the design document's pinned claims about the code still hold? | |
| `control-plane-budget` | Is the control plane still within the ceiling it declared? | |
| `dead-wiring` | Is anything declared here with something on one end and nothing on the other? | |
| `registry-consistency` | Do the registries still describe the checkers, detectors and fixtures on disk? | |
| `spec-coverage` | Does every command, path, kind and mechanism the spec names actually exist? | |

The last four are about this framework itself and are held back when you
install into another repository. `layer-boundary` installs only once your repo
declares `.v4/layers.json`. `runtime-proof` and `surface-proof` need a command
in your config to have anything to run.

---

## What it can read in your language

Ten checkers read every file regardless of language (`scope`, `secret`, `test`,
`review-finding`, `external-write`, and the five self-consistency ones). The
rest declare which extensions they read, in `.v4/checkers.json`. A checker with
nothing to read in your repository is not installed, rather than installed and
printing `PASS` about files it never opened.

| Language | Checkers that read it | How it is read | "Did the test really execute the code?" |
|---|---:|---|---|
| **Python** | 10 | the standard-library `ast` | **Yes.** `sys.settrace` through a `sitecustomize` on `PYTHONPATH`; works under pytest, unittest, any Python runner. Used by `test` and `review-finding` |
| **Go** | 7 | Go's own `go/ast`, through a small helper compiled with your Go toolchain | **Not yet through a checker.** The kernel can read a `go test -coverprofile`, but the `review-finding` checker currently accepts Python closing tests only and answers `4` for anything else |
| **TypeScript / JS** | 8 | a regex-masked scanner, not a parser; a `bypass/` fixture watches it | Same as Go: the kernel can read `NODE_V8_COVERAGE`; no checker reaches it yet |
| **Rust** | 2 | a scanner, no parser | **No.** |

Counts are of checkers whose `reads` names that language explicitly, as of
2026-09-02.

---

## What it will not do

Every item here is verified against the code, not stated as policy.

- **It is friction, not a boundary.** The hooks are Claude Code configuration,
  they fail open, and an agent with a shell can edit the SQLite file or drop
  its triggers. What you get is that the audit shows it, and that every escape
  route leaves a row with a name.
- **The shell guard reads the command line only.** `python3 -c "open(...)"` or
  a script that opens a protected file passes it. The `scope` checker at ship
  is the answer of record.
- **A signature can be given by the agent.** `--no-tty-check` is a legal exit;
  the file it writes says `signed_by: agent`. What actually anchors a signature
  is that its file must be committed before `ship` accepts it.
- **The engagement sentence is judged mechanically.** Forty characters, name the
  subject, don't recite the rule, don't repeat yourself across tasks, don't
  paste a credential. Someone who knows the rules can satisfy them on purpose.
  The point is to make the moment happen, not to prove it did.
- **Analysis is single-file and vocabulary-driven.** A read-back in another file
  is invisible to `external-write`. An exception allowlist whose default is the
  unsafe answer is invisible to `fail-closed`. Both blind spots are kept as
  executable `known_miss/` fixtures.
- **A review finding can also be closed by a text change**, not only by a test:
  a marker of 24+ characters present at the parent commit and gone at HEAD.
  It proves a string moved and nothing more.
- **The `task` table is not in the hash chain.** Attempts and events are; a
  rewritten request text would not be caught by `v4 audit`.
- **Nobody enforces that a reviewer reads blind.** The kernel does not know
  which process read what.
- **It does not judge whether the request was met.** `v4 cover` makes you
  account for the request clause by clause, verbatim; it does not decide.

---

## Numbers

Everything below is counted from this tree on 2026-09-02, by script, not
remembered.

| | | | |
|---:|---|---:|---|
| **21** | questions, one checker each | **20** | detectors (11 conditional, 9 unconditional) |
| **8** | kinds that hold a ship | **13** | reviewer lenses, 303 checks |
| **96** | standing rules in the generated `CLAUDE.md` | **32** | `v4` commands |
| **525** | fixture cases (196 red · 212 green · 100 bypass · 6 known miss · 11 self-trigger) | **1,027** | per-language fixture files (747 Python · 166 Go · 89 TS/JS · 25 Rust) |
| **1,717** | tests in the framework's own suite | **0** | third-party dependencies |

The framework has been its own first user since 2026-08-06. The measurements
behind [docs/EVIDENCE.md](docs/EVIDENCE.md) and the examples in
[docs/DOGFOOD_LOG.md](docs/DOGFOOD_LOG.md) come from that development ledger,
which lives in `.git/v4/` and is exported to `.v4/ledger_export.jsonl` on every
ship. A snapshot of this repository that does not carry the export cannot
reproduce those figures; they are a record, not a claim about the tree you are
reading.

---

## Where to read next

| | |
|---|---|
| [docs/USING.md](docs/USING.md) | the seven steps of a task, and the judgement call at each |
| [docs/FACTS.md](docs/FACTS.md) | the vocabulary file `external-write` reads, and how to write yours |
| [docs/SPEC.md](docs/SPEC.md) | the contract: every mechanism, command and constraint, pinned to the code |
| [docs/RATIONALE.md](docs/RATIONALE.md) | why it is built this way, including what was tried and retracted |
| [docs/EVIDENCE.md](docs/EVIDENCE.md) | what was measured, when, and how firmly it stands |
| [CLAUDE.md](CLAUDE.md) | the standing rules, generated from what this repository registers |

MIT licence.
