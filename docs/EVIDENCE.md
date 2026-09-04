# EVIDENCE — what has actually been measured, and what has not

This file is the record of every real experiment run against this framework. It
is written for a reader with no prior context: another model picking this repo
up cold, or a person returning to it in six months.

It exists because the same mistake kept happening. Three times in one session
the author of these runs stated a root cause from reading the code, and three
times the measurement said something else:

| stated from reading | measured |
|---|---|
| "21 of 29 checkers are a thin CLI" | 20 of 27 (at the time; 21 registered today) |
| "the multi-stack gap is that install has no profile layer" | 8 checkers returned PASS having parsed no Go |
| "engagement stopped the bug" | the checker stopped it; engagement accepted a false sentence |

**So: nothing in this file is a claim from reading source.** Every number has a
command behind it, and every claim is tagged with how strongly it is held.

```
✅ mechanical    a program produced it; re-runnable; the author read the code it reports on
📊 agent         an LLM produced it; the author sampled but did not verify every row
⚠️ judgement     no data; stated as judgement
❌ retracted     was asserted, then measured, and did not survive
```

**Where the numbers come from.** Every measurement in this file was read off
the author's development ledger — `.git/v4/ledger.db`, exported by `v4 ship` to
`.v4/ledger_export.jsonl` — and off a private reference adopter repository
(`adopter_a`) that is not part of this tree. A public snapshot of this repo may
not carry that export. Where it does not, the figures here are a record of what
was measured, not something a reader can reproduce from the tree alone.

---

## 0. What this framework is, in one paragraph

Four layers answer four different failures. ① `CLAUDE.md` is a generated
document read before work — no gate. ② A **checker** is a Python program whose
exit code is the verdict, with no model in it. ③ A **lens** is an LLM reviewer
holding a JSON checklist. ④ **Engagement** is one sentence a worker must write
before a checker will run, judged by seven mechanical tests and no reviewer.
Everything judgeable becomes a **claim** with an id; a claim is answered by one
checker; `v4 ship` asks one question — is every claim answered against the bytes
that are there now.

---

## 1. The X/Y experiment (2026-08, 8 tasks, one repo per arm)

### 1.1 Design

Two arms, same starting code, same eight briefs, each task building on the last.

| | X | Y |
|---|---|---|
| loop | read brief → write code → self-review once → commit | read brief → write code → the whole framework → commit |
| framework, as measured at the time | none | 23 checkers · 8 detectors · 9 lenses · 17 engaged kinds · CLAUDE.md (2026-09-02: 21 claim kinds and 21 registered checkers · 11 conditional detectors registered, 20 detector files · 13 lenses · 10 engaged kinds) |

- **Base** ✅ 121 lines of real code from a production repo. Both arms started
  byte-identical across `app/` and `tests/`.
- **Briefs** ✅ eight real commits from that repo's history, rewritten as a
  person would ask (mean 11 characters). Grepped for oracle vocabulary: zero
  leaks.
- **Sandboxing** ✅ neither arm could see the reference implementation. Finding
  it would have been cheating and was checked for.

### 1.2 Instruments

**Oracle — 12 programs** ✅ Each predicate was validated *bidirectionally*
against the history it came from: it must return True on the commit that carried
the defect and False on the commit that fixed it. That validation caught two of
the author's own predicates recording the *introducing* commit rather than the
fixing one (D2, D3).

**23 checkers** (at the time; 21 registered today) ✅ mechanical, no model.

**9 lenses** (at the time; 13 lenses today) 📊 280 checklist items (303 today), four independent passes:

| pass | subject | agents | result |
|---|---|---|---|
| 1 | armY's diff, per task | 72 | 152 findings, 140 fixed |
| 2 | armX's diff, per task | 72 | 242 findings, none fixed |
| 3 | both final codebases + severity | 18 | armX 61 · armY 52 |
| 4 | armXc (armX cleaned once at the end) | 9 | 62 |

### 1.3 Result: real defects

| | armX | armY |
|---|---|---|
| **D1 credential reaches a rendered traceback** | **6 sites** ✅ | none ✅ |

Five independent confirmations of D1: the oracle predicate, the `secret-chain`
checker (a checker of the time, removed 2026-08-24 at `a9ae5fb`), armX's per-task lens, two separate final-state lenses, and one lens
that **reproduced it** — printing `url: /bot123:SUPERSECRET/sendPhoto`.

armX's own source states the defect out loud. `_scrub()` cleans the message and
`raise ... from exc` keeps the original; at `_fetch_one:1364` the code manually
writes `unknown.__cause__ = exc` with the comment *"still there for whoever logs
it"*.

### 1.4 ❌ Retracted after reading the code

| claim | why it did not survive |
|---|---|
| D7 "failure swallowed" | the call site is `_upload_rewinder`, a predicate returning bool |
| D10 "concurrency unchecked" | armX validates it in `_normalise_download_workers()` |
| D4 against armY | armY has no local mode; the predicate's `knows_local` matched the words "local disk" |
| `external-write` on armX | the only caller does `if payload is None: raise` |

**Raw count 4 vs 1. After reading every site, 2 vs 0.** Reporting the raw count
would have overstated the result by a factor of two.

### 1.5 The most dangerous single finding ✅

A green test protecting a false property.

```python
# armX — test green, property false
self.assertNotIn("123:SECRET", str(failure))   # str() does not render __cause__

# armY — same test name, asserted on the rendered traceback
"""...because `__cause__ is None` is also true of a plain `raise`,
   and a plain `raise` still prints the original, and its URL, through
   `__context__`."""
```

### 1.6 Test strength ✅

```
mutation testing (60 mechanical breakages, same seed)
  armX  37/60 = 61.7%   survivors 23     ⚠️ z=1.57, p≈0.12 — not significant
  armY  45/60 = 75.0%   survivors 15

                       armX   armY
  real socket tests       0      1 (8 cases)
  spec'd Mocks            0      8
  suite runtime        0.50s  4.34s     Y is slower because its tests are more real
  import client.py      55ms   57ms     no runtime difference
```

### 1.7 ❌ The two findings that overturned the first conclusions

**(a) armY did not do the same work.** Reading both arms against each brief:

```
equal 4 · armX delivered more 3 · armY delivered more 1
```

Verified mechanically ✅

```
                        armX   armY
  multipart upload         4      0    armY never uploads a local file (T1)
  ffmpeg / thumbnail      16      0    armY produces no preview at all (T8)
```

This retracts three "armY is clean" results: D4, D11 and D12 are vacuous for
armY, because it never built the feature they are about.

Causal chain ✅ armY's T1 commit reuses the doctrine's own word
(*speculatively*) from `CLAUDE.md` scope rule 2, and T8 explicitly cites T1's
decision. **One scope decision closed off two later tasks.** T5's gap is
unrelated — a narrower reading of the brief.

Structural cause ✅ `task.request` was written in two places and read in none;
as measured then, 34 of 88 doctrine rules (96 doctrine rules today) say some form of *do not do more*, and none said *do
enough*. This is what `v4 cover` was built for (at the time also a `request-coverage`
checker, removed 2026-08-24 at `a9ae5fb`; the accounting lives in `kernel/request_cover.py`).

**(b) The lens fixes introduced bugs.** 📊

```
armY  4 confirmed latent bugs, all introduced_by = review-fix
armX  5 confirmed latent bugs, all introduced_by = original-work
```

### 1.8 Three routes compared ✅ <!-- count-exempt: the fence carries a column header above the three arms -->

```
                     app LOC  tests  incident  latent  debt  total  per 100 LOC  cost
  armX  do nothing      2,579    220        3      17    41     61     2.37       ×1
  armXc clean once      3,501    326        0      16    46     62     1.77       ×1.96
  armY  every task      3,399    286        0      20    32     52     1.53       ×6.60
```

Cleaning once, at twice the cost, bought the thing that matters most —
production incidents 3 → 0. It fixed 44 findings and the total went 61 → 62,
because the code grew 36% and new code carries the ambient rate. That is the
same phenomenon as armY's 4 review-introduced bugs.

⚠️ armXc cleaned after all eight tasks, seeing the final state. That favours it.
n=1.

### 1.9 ⚠️ Statistical limits — read this before quoting any of the above

```
real defects  X 2/8 = 0.25 per task
              Y 0/8. Zero is not a rate. Rule of three: 95% upper bound 0.375/task
              → Y's true rate could statistically exceed X's measured rate

mutation      p ≈ 0.12. Not significant.

Separating these would need ~40 tasks per arm. This run had 8.
```

### 1.10 Per-layer accounting ✅

| layer | how much it did | effect |
|---|---|---|
| ① CLAUDE.md | 207 lines, no gate | with ④, caught D1. **Also the source of T1's over-narrowing** |
| ② checker | 966 runs. Blocks: review-finding 406 · external-write 24 · scope 4 · test 2 | caught no real defect, but **forced 117 red/green tests**; mutation +13 points |
| ③ lens | 152 findings, 140 accepted (92%) | 88% of elapsed time. Final density 1.53 vs 2.37 |
| ④ engagement | 289 sentences, 256 accepted, 33 refused | 25 of the 33 were framework bugs. 8 were real "filled in anything" |
| hooks | **0 fires** (as measured then) | environment limit — sub-agents did not receive them. The deeper cause was found later: `write_block` and `stop_gate` could not locate `kernel/` in this repo, recorded and fixed in `hooks/_framework.py`; all three hooks are wired in `.claude/settings.json` today |

---

## 2. First-adoption run (2026-08-09)

**Method** ✅ Build a fresh repo from nothing, run `init → install → task →
derive → engage → check → cover → ship`, with two defects planted. The author
did not know in advance what would go wrong.

**Nine real gaps, none of which are visible from inside this repo**, because
this repo has already declared everything.

| | before | after |
|---|---|---|
| a library with genuinely no outbound write | **could not adopt at all** | adopts; ship blocks until confirmed |
| `secret` | **13 false positives** — its own bypass fixtures | 0 |
| `scope` | **false positive** on the framework's own `.pyc` | 0 |
| `test` | **SUBJECT_MOVED on every first run**, PASS on every second | passes first time |
| permanent blockers | 3 kinds, exit 4 on every task, nothing prompting the fix | 0 |
| a bad config | 30-line Python traceback | one line |
| `dangling_ref` on this repo | **42 findings, all false** | 0 |
| `checker_sha` | changing the deciding code left every PASS looking fresh | `program_sha` covers the import closure |
| engagement "rule copied back" | 9 characters of filler walked through | containment; padding cannot lower it |

Two of these deserve naming:

**`dangling_ref`** was red on this repo with 42 findings, every one false:
`kernel/analysis/` has no `__init__.py`, so `_module_path` walked past it to
`kernel/__init__.py`.

**And the one that fix exposed:** the verdict went from 42 findings to none and
**not one recorded PASS went stale**. `checker_sha` hashed `checkers/x.py`, but
20 of the 27 checkers registered at the time were a thin CLI over `kernel/analysis/`. "A different program
gave that answer" was true of the argument parser and false of the judgement.

---

## 3. `v4 cover` — the gate that named its own bypass ✅

Asked directly: the worker can see the request, so can it just quote the whole
thing back?

```
$ v4 cover --task t-1 --quote "<the entire request>" --symbol fetch
recorded. 100% of the request is accounted for.
$ v4 check --task t-1
ok  request-coverage  PASS        # at the time: that kind was removed 2026-08-24
```

`checkers/request_coverage.py:24` (the checker of the time; the rule now sits in
`kernel/request_cover.py::clauses`) named that exact move as the reason the floor
is 75% rather than 100%. **Lowering a floor does not stop one entry from
swallowing the request; it only makes less of it necessary.** Two mechanical
rules now: a quote may not span a clause boundary, and every clause needs an
entry. `、` is not a boundary — it separates a list inside one clause, and the
registration gate refused the first version over four green fixtures carrying
`我要佢識send相、片同檔案`.

---

## 4. Cross-stack run — a Go repo (2026-08-09) ✅

**Method** A Go repo with four planted defects, all 27 checkers registered at the time (21 today) run against it.

```
  15 checkers  exit 4   honest: "I cannot read this"
   8 checkers  exit 0   green having parsed no Go at all
   1 checker   exit 0   genuinely (secret is a byte scan)
   3 checkers  exit 1
```

The worst: `dependency` (a checker of the time, removed 2026-08-24 at `a9ae5fb`)
returned `PASS: no package manifest declares a dependency` about a `go.mod` with
an unpinned require and no `go.sum`.

`route_auth.py`'s docstring has stated the principle since day one — *answering
"fine" by looking at nothing is the failure this layer exists to refuse.* 15
authors thought about it and 8 did not.

> **A rule that holds only when each of 27 authors remembers it is a tally of
> who was careful, not a rule.**

Fixed by making it mechanical: `.v4/checkers.json` carries `reads: [glob…]`; the
kernel does not run a checker whose repo contains nothing matching, and records
UNSUPPORTED. Undeclared is a registry error, not a default. `v4 install` uses the
same test as a hold-back — which is the whole of the multi-stack answer, with no
profile layer.

```
Go repo     (當時) 23 installable kinds → 6 installed, 17 skipped, each naming what it reads
            false greens 8 → 0
Python repo (當時) 23 → 22 installed, only `bundle-secret` skipped (no JS/TS; that kind was removed 2026-08-24)
```

---

## 5. Five-task run (2026-08-09) ✅

**Method** One repo, five tasks in sequence, `check → git commit → ship` each
time. Four more defects, three of them undeliverable before this run.

**(a) `worktree_digest` said "content" and hashed content *relative to a
commit*.** `v4 check` green, `git commit` with not one byte altered, `v4 ship`,
and every repo-scoped claim came back STALE. The ordinary flow could not
converge. It is one git blob hash per path now — from the index for an untouched
file, `git hash-object` for a touched one — so staging and committing are no-ops.
Also faster: 51ms against 69ms.

**(b) `bash_guard` was dead in every adopting repo.** It imports `kernel`,
`install` does not copy `kernel`, and a hook is run directly by the coding agent
with no `bin/v4` and so no `V4_HOME`. The `except Exception: print("{}")` around
the import made a dead guard and a clean command look identical — the one hook
whose reason for existing is `sed -i .v4/config.json` allowed exactly that.

**(c) `install` never overwrote, so no fix ever reached an adopter.** A whole
day of them sat behind one `if d.exists()`. Fixed with a manifest of what was
shipped. The first version of that fix stamped the manifest with the *edited*
bytes, so the next install would have called the edit untouched and destroyed
it — worse than never updating. Its own test caught that.

**(d) An `absence` declaration nobody re-read.** The repo declared
`outbound_write` absent, a later task added `open(path, "w")`, no claim was
raised (correctly — the detector has no vocabulary), and the task shipped. This
was a mechanism added the same morning.

⚠️ The check written for (d) is exactly as strong as the scan it verifies — a
verb ending on a dotted call. It cannot see a write behind `subprocess`, and it
did **not** see the `open()` that started this.

---

## 6. The gate was hollow, and only a real task showed it (2026-08-09) ✅

One task on the real adopter -- `t-v4-smoke`, seven `ValueError` messages in
`adopter_a/core/config/bucket.py` made to name the rule they are about.
Before it, `v4 install` reported success and `v4 doctor` was all-ok.

```
                         before        after      how it was found
checkers registered      2 / 23        23 / 23    reading the install output
detectors registered     0 / 9         9 / 9      "0" printed with no reason
secret-chain, one run    45 s          1.5 s      it was walking .venv/
doctor's baseline check  never ran     fires      no kind had ever declared one
v4 cover, false symbol   100% PASS     refused    four of my own entries were fake
```

(`secret-chain`, `bundle_secret` and `dependency_audit`, named here and in §6.1
below, were checkers of the time. None is a registered kind today: all three
were removed on 2026-08-24 at `a9ae5fb`.)

Two root causes, both invisible from the source:

**No `PYTHONPATH` for a checker subprocess.** An adopter has `checkers/` and no
`kernel/`; `bin/v4` supplies the path and every child inherited it. Inheritance
is not a contract -- `v4 --repo <adopter> install` run from the framework side
gave the child nothing, and all 27 checkers of the time died on their first import.

**Exit 1 from a crash and exit 1 from a finding are the same number.** So every
red fixture "passed" by crashing and every green fixture failed, and the gate
that decides what may judge a repo reported success while registering two of
twenty-three. `runner.reported_nothing()` now separates them on the one
unambiguous signal: CPython writes `Traceback (most recent call last):` to
stderr and nothing to stdout.

### 6.1 What the task itself found ✅

```
one rule, re-derived      secret_chain / test_shape / bundle_secret each wrote
                          their own exclusion list; secret_chain walked .venv
                          and reported jwt/jwks_client.py. → subject_files.tracked()

three baseline readers    dependency_audit / structural_lint / layer_boundary,
                          three behaviours on an unreadable file, three key
                          names, against SPEC §6's "唔准各自發明". → kernel/baseline.py

debt nobody caused        secret-chain and test-token-shape had no baseline, so
                          adoption blocked every task forever. adopter_a' own
                          standing debt is now in git: 66 / 2 / 8 ids.

a false positive          fail_closed did not know "swallow, then verify", and
                          said "execution continues as if the operation had
                          succeeded" about a handler two lines above a read-back
                          that raises. → trys_sealed_by_a_later_check()

who changed this file     kernel_written() guessed from a list of four path
                          names. .v4/installed.json records every shipped byte;
                          it now answers from the record, and an edited checker
                          still reports because its sha stops matching.
```

### 6.2 ⚠️ The finding that is about the operator, not the framework

Four `v4 cover` entries were written for this task. **All four named symbols and
tests that do not exist** -- `_require_bucket`, `REJECTIONS`,
`test_each_rejection_says_which_rule` -- and the kind passed at 100%. `--symbol`
and `--test` were free text; §3 above calls `cover` "the gate that named its own
bypass", and this is a second one it had not named.

They now resolve against the repo. That immediately produced a trap of its own:
the ledger is append-only, so one typo would hold a task closed forever. Hence
`v4 cover --withdraw`, where the correction is itself an append and both rows
stay readable.

### 6.3 The one that needed a second measurement ✅

`test` asks whether the suite executed the files this task changed, by tracing
through `sitecustomize`. It reported "the suite passed and executed none of the
1 changed file(s): core/config/bucket.py" -- a file the task's own tests
import and call fifteen times.

Believing it would have produced the exactly wrong conclusion: that the task
shipped untested code. What made it worth chasing was a second measurement at a
different scale -- the same tracer over that one test file records the file
correctly. Right alone, wrong together.

```
                          traced   repo's own files   the changed file
whole suite, before          1            0                  no
one test file                645          2                  yes
whole suite, after          5946         921                 yes
```

`PYTHONPATH` reaches every Python child a test command starts, all of them wrote
one shared file, and the last writer won. The last writer is not the test run:
`multiprocessing.resource_tracker` is spawned by anything using a semaphore or
shared memory and outlives its parent, having executed nothing. One file per
pid, unioned by the reader.

The repair introduced its own defect, and this framework caught it: "do not
write an empty file" collapsed *a Python run that touched nothing* (a finding)
into *a command that is not Python* (unanswerable), and `checkers/test.py`'s own
`suite_green_without_touching_the_change` bypass fixture went from exit 1 to
exit 0. Both cases are tests now.

### 6.4 Cost

`v4 install` on a 5,400-file adopter: **10 min 26 s**. It was fast before because
nothing ran.

---

## 7. What is still not established

```
③ lens end to end   The scheduler (`v4 sweep`) is verified. "Nine lenses review
                    (13 lenses today), findings become claims, ship blocks on
                    them" has not been run since the X/Y experiment.

hooks in a live     As of 2026-08-09: verified by feeding synthetic PreToolUse
session             payloads, never observed firing from a real coding-agent
                    session, `v4 ship` still printing DEGRADED. The cause was
                    found afterwards -- `write_block` and `stop_gate` could not
                    locate `kernel/` here -- and is recorded and fixed in
                    `hooks/_framework.py`; `.claude/settings.json` wires all
                    three hooks. Whether they fire in a live session is not
                    re-measured in this file.

non-Python stacks   As of 2026-08-09, 16 of 27 checkers were Python-AST. They
                    were then honestly absent on other stacks rather than
                    falsely green, and no Go or TypeScript checker had been
                    written. Today 8 of the 21 registered checkers declare
                    `**/*.ts` in `reads` (7 of them `**/*.go` too, 2 `**/*.rs`),
                    backed by `kernel/analysis/gosource.py` and `rssource.py`.

scale               The longest run is five tasks. Ledger and chain behaviour
                    over dozens of tasks is unmeasured.

④ engagement        Its own module docstring says it is the layer with the
                    weakest evidence, and two of its criteria were defeated and
                    repaired in one day. Nothing isolates what it buys.
```

---

## 8. How to re-run any of this

Every experiment above is a shell sequence against a scratch repo. The shape is
always the same, and it is the shape to use for the next one:

```bash
#-- step 1: a repo that does not know about this framework
mkdir -p /tmp/probe/{app,tests} && cd /tmp/probe && git init -q .

#-- step 2: plant a defect you can state in one sentence before you start
#    (state it first — a defect found after the fact is a story, not a result)

#-- step 3: adopt
python3 -m kernel.cli --repo . init          # then fill test_command
python3 -m kernel.cli --repo . install
#    prune .v4/facts.<repo>.json.draft, rename it, replace every AUTO: line

#-- step 4: run the loop and read every message
./bin/v4 task --id t-1 --request '…' --scope 'app/**,tests/**'
./bin/v4 derive --task t-1
./bin/v4 engage --claim <id> --text '…'      # one distinct sentence per claim
./bin/v4 check  --task t-1
./bin/v4 cover  --task t-1 --quote '…' --symbol <name>
git commit -am '…'
./bin/v4 ship   --task t-1
```

**Three rules the runs above learned the hard way:**

1. **Read the code behind every finding before reporting it.** Half of the first
   defect list did not survive that.
2. **Write one distinct engagement sentence per claim.** A loop that writes the
   same sentence everywhere gets refused by the cross-task duplicate rule, and
   the refusal looks like a framework bug.
3. **State what you expect before running.** Seven of the nine first-adoption
   gaps were things nobody would have thought to look for.
