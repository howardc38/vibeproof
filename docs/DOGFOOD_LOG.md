# Dogfood log

Every entry here is a design problem that **building** V4 exposed, which
**reading** V4 did not. Four adversarial review agents read the design and
produced roughly sixty findings; the entries below are the ones none of them
caught, because they only appear when you try to write the code.

That gap is the reason this log exists, and it is also the argument: if a
design is only wrong in ways that surface while the code is being written,
reviewing it harder does not find them.

Format: what I hit, what it means for the design, what changed.

Provenance: the numbers in these entries were read off the author's development
ledger (`.git/v4/ledger.db`, exported by `v4 ship` to `.v4/ledger_export.jsonl`)
and off a private reference adopter repository (`adopter_a`) that is not in this
tree. A public snapshot of this repo may not carry that export; where it does
not, the figures are a record of what was measured and are not reproducible from
the tree alone.

---

## 2026-08-06 · the schema section had no home for a promise the derivation section makes

Writing `ledger.py` from the rev-1 table list, four fields had nowhere to go:

| Missing | The design sentence it was supposed to serve |
|---|---|
| `detector_run` record | the derivation section: "kernel 要照寫落 ledger:呢個 task 冇跑過 external-write detector" |
| `attempt.stderr` | the exit-code section distinguishes "the checker broke" from "the code is wrong" — and everything you need to tell them apart is on stderr |
| `claim.symbol` | the derivation section makes symbol part of claim identity; the table had no column for it |
| engagement verdict vocabulary | the engagement section treats a mechanical rejection and a reviewer rejection as different things but never names them |

**What it means:** the schema section was written as prose about tables rather
than as a table. Prose can promise a field that does not exist. This is a small
instance of the failure the whole design is about.

**Changed:** the schema section rewritten as an actual column list; `event` table
absorbs detector_run; stderr and symbol added.

---

## 2026-08-06 · The append-only claim was true; the "no UPDATE" claim was not

rev 1 said state is never a column. Writing the schema, I put
`risk_id REFERENCES accepted_risk(id)` on `benchmark_baseline` — a field whose
value cannot be known at insert time, so the only way to set it is an UPDATE,
against a table the same file declares append-only.

One review agent predicted this exact hack before seeing the code. I had
already written it.

**Changed:** rev 2 derives "which risk superseded this baseline" from the rows
instead of storing it. `benchmark_baseline` dropped entirely when benchmark
became observe-only.

---

## 2026-08-06 · Registration gate verified against a hollow checker

Not a problem — a check that the registration gate does what it claims. Recorded
because "we assumed it worked" is how five of six V3 scanners ended up as
stubs.

```
real checker  (AST: try/except swallowing an outbound write)  10/10  registrable
hollow checker (sys.exit(0))                    5 red fixtures pass  NOT registrable
```

---

## 2026-08-06 · Tamper detection: measured the exact line between prevent and detect

The design claims out-of-band writes are *detectable*, not *impossible*. Tested all
three cases rather than asserting:

| Attack | Result |
|---|---|
| `UPDATE attempt SET exit_code=0` | Blocked — trigger raises |
| Raw `INSERT` of a forged PASS, bypassing the kernel | **Succeeds.** `v4 audit`: `attempt 4: prev_hash does not follow attempt 3` |
| Drop the trigger, then edit an old row in place | Caught — `attempt 2: contents do not match row_hash` |

**What it means:** the honest sentence is the one the design uses. Anyone tempted to
upgrade it to "structurally impossible" should run the second row again.

---

## Rules this log operates under

1. **The acceptance criteria are frozen** (`.v4/acceptance.json`) before
   deliverables arrive. A deliverable that fails is fixed in the deliverable.
   Editing the gate so a failing deliverable passes is the framework rewriting
   the exam because the student failed — the same move as V3 grading its own
   paperwork.

2. **Hold on rework for anything currently under adversarial attack.**
   Rebuilding a module while its design section is being attacked means
   writing it twice, and risks "fixing" the verifier in a direction the attack
   would have rejected.

3. **Dogfood coverage is stated honestly.** As of this entry the bootstrap uses
   3 of 8 V4 mechanisms (kernel-execs-checker, fixture gate, hash chain). It
   does not use claim derivation, staleness, scope declaration, engagement, or
   red-green review closure. Saying "V4 is built with V4" would be the same
   category of overstatement as rev 1's three load-bearing sentences.

---

## 2026-08-06 · The design document was in three places and they had drifted

Caught by the project owner, not by me, and not by any of the eleven agents
that had read the design.

| Copy | State |
|---|---|
| `scratchpad/V4_LITE.md` | 1,194 lines — the one I was editing |
| 當時嘅 `docs/` 入面一個設計檔(後來拆咗做 SPEC + RATIONALE) | 1,065 lines — **still rev 1**, "rev 2" matched zero times |
| Published artifact | rev 2 as of several hours earlier |

Agents spawned before the split were given the scratchpad path. An agent
spawned afterwards, working in the repo, would have found and believed the
rev-1 copy — including the three load-bearing sentences rev 2 exists to
retract.

**What it means:** the document argues that one fact stored in two places will
diverge, and demonstrates it on itself within a day. Worth stating plainly:
a rule you have written down is not a rule you are following.

**Changed:** one copy only. The scratchpad file is a
signpost. The artifact renders the repo file rather than being a third
version. No sync process was added — a sync process would be a second source
of truth wearing a helpful expression.

---

## 2026-08-06 · Three premises checked; none survived intact

An agent was asked to verify the three claims in the plan that nobody had
tested. I re-verified each finding myself before acting on it.

| Premise | Outcome |
|---|---|
| "the defect is on adopter_a HEAD right now" | **False.** Fixed by `f0060ebb` at 13:46; the plan asserting it was written at 16:19 |
| "secret stores swallow the exception" (`secure_store.py`, `secret_store.py`) | **Both false positives.** The handler collects and the enclosing code raises unconditionally |
| "Zero Core gives a 3,018-line head start" | **Hand-assembled subset.** No file or package of that size exists; 41% depends on `SysctlKinfoProc`, which Python's stdlib cannot reach |

The second is the one that matters most. The rule was "handler body contains
no `raise`", and the two examples chosen to showcase it are the two it gets
wrong. The right question is whether control can leave the `try/except`
without raising — an escape-path question, not a handler-contents one.

Both false positives are now mandatory green fixtures. Fixtures taken from
real code are worth more than fixtures written to match a rule.

**Consequence for the plan:** `並發雙跑` loses its evidence and is not built.
The defect it cited is a retry-idempotency defect, already covered by
`external-write variant=replay`; of eleven real concurrency fixes the
two-thread recipe catches two; and the proofs it would need live in
`-m integration`, which `test_command` excludes.

**The ruler moved, on the record.** The `real_defect` criterion named a defect
that no longer existed, making it unsatisfiable. A round was open, so the
tooling refused to amend it. Closing the round, amending with a reason, and
reopening cost nothing because no measurement had been taken — which is the
whole reason the freeze is scoped to a round instead of held forever.

---

## 2026-08-06 · An agent followed the design exactly and produced something that does not run

The clearest answer yet to "is this document enough for another LLM to execute
correctly". Not an opinion — a result.

The design document declared one flag, `--subject`. `kernel/runner.py` passes
`--out` on every invocation and `--facts` when facts exist. An agent built the
`fail-closed` checker from the document, argparse rejected the undeclared
flags, and **all sixteen fixtures exited 2 — `NOT registrable`**. The agent
found the cause itself and reported the deviation.

**Answer: no.** The document explains what and why, with provenance for every
number. It is a rationale, not a specification, and a competent reader working
from it builds something that fails on the first invocation.

## And the document had already drifted from the code, one day after consolidation

| | Document | Code |
|---|---|---|
| repo-scoped staleness | HEAD commit | `worktree_digest` — HEAD + diff + untracked |
| chain formula | seven fields | plus `CHAIN_SCHEME`, `config_sha`, `head_commit`, `stderr` |
| red-green | two conditions | three — the test must execute the symbol |
| chain append | unmentioned | `BEGIN IMMEDIATE`, or concurrent worktrees fork it |
| checker contract | one flag | three |

Consolidating three copies into one did not fix drift. It moved it: the copy
that matters is now the code, and prose restating code drifts from it exactly
as prose restating prose did.

**Changed:** where code is authoritative, the document points instead of
restating:

```
<!-- 例:pinned: kernel/ledger.py::CHAIN_SCHEME=v4-chain-3 -->
```

(The example shows the scheme at the time; `CHAIN_SCHEME` is `v4-chain-4` today,
and the live pin is in `RATIONALE.md` §6.3.)

`checkers/design_pins.py` resolves every pin: the file exists, the symbol is
defined, and where a value is given it matches. Rename a symbol and the
document fails a check rather than quietly lying. Registrable — 10/10 fixtures,
including one where two pins hold and a third does not.

The limit is worth stating: a pin proves the thing being described exists. It
does not prove the prose around it is right. That is a smaller promise than
"the documentation is accurate", and it is the one that can actually be kept.

---

## 2026-08-06 · Stage 1 ran, and everything it caught it caught on me

The criterion was one real task from open to ship. It took six attempts, and
each refusal was the design working rather than a bug:

**t-001 held on scope.** Declared `kernel/**,docs/**`, had touched `.v4/**` and
`checkers/**` — the file naming the test command, and the programs that judge
everything. Both are protected paths, and the design says widening into them
takes a signature rather than a flag.

**t-002: the scope checker refused to run at all — exit 6, "checker on disk is
not the registered one".** I had added a comment to it after registering it.
That is the answer to the cheapest exit from a red check, which is to edit the
checker until it agrees, and it fired without being asked to.

**t-002 again: eleven fail-closed claims, all from 嗰陣嘅 `tests/fixtures/<set>/red/`.** The
detector was right every time — those files are broken on purpose. Claims the
repo planted itself would bury the ones that mean something, so `derive_exclude`
names them.

**t-004 held on a fixture directory that could not be deleted.** The scope
fixtures were nested git repositories, which git will neither track nor clean
up. They are plain data now, and a case declaring `<case>/.v4/fixture.json` is copied
to a temp directory and initialised only while the checker runs.

### t-005: the system ate itself, and this is the one worth remembering

Both claims passed. Ship then reported `scope` as STALE, seconds after it had
been answered, with nothing having been edited.

`.pyc` files had been committed before `.gitignore` existed, and gitignore does
not apply to what is already tracked. Every test run rewrote them, so the
working-tree digest moved, so every repo-scoped claim went stale the moment
another one was answered. `test` invalidated `scope`; `scope` invalidated
`test`. **There was no state in which both could hold, so nothing could ever
ship.**

The digest was right. Those files did change. They should never have been in
the tree.

Worth sitting with: the staleness rule was fixed hours earlier *because* keying
on HEAD let uncommitted work through. Keying on content closed that and opened
this. Neither showed up in sixty review findings across eleven agents. Both
appeared within minutes of running the thing.

**t-006: SHIP.** Two claims answered, re-derive converged in one round, chain
intact, three detectors ran and the report says which.

### What the fixture harness needed, discovered by using it

Two extensions, neither predicted:

- A checker that judges a whole repo rather than a set of files could not be
  gated at all. `test` and `scope` — the two with the widest reach — were
  exactly the ones that would have shipped ungated.
- A checker whose answer depends on params could only be tried on one input.
  The scope fixtures were five ways of testing the same thing until a case
  could carry its own params.

---

## 2026-08-06 · Red-green landed, and the getsource test is refused by name

`review-finding` is registered: ten fixtures, ten as expected. The one that
matters is `red/source_inspection_only`, which is the shape found in the target
repo:

```python
assert 'name[:8]' in inspect.getsource(app.notify)
```

Delete the fix and it fails, so it satisfies fail-at-parent and pass-at-HEAD
perfectly. The checker refuses it anyway:

```
FAIL: test_new.py has not earned the right to close this finding.
  never executed notify. A test that reads source text instead of calling the
  code satisfies red-green while proving nothing
```

Without the third condition, a worker closes any review finding with one line.

### Three more harness gaps, all found by using it

The fixture harness has now needed four extensions, none of them predicted, and
each one was blocking a checker that could otherwise never be gated:

| Extension | What could not be tested without it |
|---|---|
| a case may be its own repo | `test` and `scope` — the two with the widest reach |
| a case may carry `params` | `scope` — five fixtures were five ways of testing one input |
| a case may carry `parent_content` | anything red-green: the parent has to hold the defect and the working tree the fix, and one snapshot cannot say both |
| a case may name its own `subject_refs` | `review-finding` — the default is every file sorted, which put `.v4/config.json` first, a perfectly good subject for nothing |

The pattern is worth naming: **the harness's defaults are right for the
checker shape that was built first, and wrong for every shape after it.** Each
gap surfaced as a green fixture failing for a reason that had nothing to do
with the checker under test.

---

## 2026-08-06 · Three ways the mechanisms defeated themselves, found by a triage pass

An agent re-checked 33 surviving adversarial findings against the code rather
than the document, ran each attack, and turned up three that mattered. All
three were self-inflicted by earlier fixes.

### Signing a claim un-answered every other one

`risk.accept` writes `.v4/risks/<claim>.json`. That path is under `.v4/`, which
is protected, and it is part of the working tree. So:

- the `scope` claim failed, reporting the signature record as tampering with the judges
- the worktree digest moved, so every repo-scoped claim went STALE

Measured over three rounds of sign-one-then-the-other: **1/2 terminal, every
time.** Signing the second expired the first. Two repo-scoped claims could
never both be terminal, so `ship` returned `HELD` forever — and signing is the
only exit for `UNSUPPORTED`, for a missing checker, and for registering a new
checker.

The record is the signature's own output, not a change to what judges the work.
It is now excluded from both the digest and the scope comparison.

### The staleness stamp was taken after the checker ran

`lifecycle.check` computed `worktree_digest` *after* `run_checker` returned. A
test suite runs for twenty seconds, which is room enough for another worktree
to edit a file — and that edit was absorbed into the stamp, so the answer
certified bytes it never saw.

This is the hole `worktree_digest` was introduced to close, walking back in
through a different door. The stamp is taken before the run now, and taken
again after: if they differ, the attempt is recorded as `SUBJECT_MOVED` rather
than guessed at, because neither value describes what was tested.

### The registration gate announced itself

`register.py` hardcoded `claim_id: "fixture"` and `task_id: "fixture"`. Two
lines defeat it:

```python
if subject["task_id"] != "fixture":
    return 0          # not the harness, so always green
```

The agent wrote that, ran `v4 verify`, and got `registrable` on all ten
fixtures including the determinism check. **The gate is the only mechanical
cost on a checker an LLM wrote, and it could be switched off in production
while passing every test of itself.** Registration subjects now carry ids
derived from the kind and root, indistinguishable in shape from a real task.

### And the pin checker caught me within seconds

Writing the baseline section, I pinned
`kernel/analysis/dependency_audit.py::BASELINE_FILENAME` — a symbol that does
not exist. `design_pins` refused immediately:

```
SPEC.md -> kernel/analysis/dependency_audit.py::BASELINE_FILENAME: not defined.
The document describes something the code no longer has.
```

The constant now lives in `kernel/config.py` where every delta checker can
reach it, which is what the paragraph was claiming in the first place. (The
module itself has since gone too: `kernel/analysis/dependency_audit.py` was
removed on 2026-08-24, with the `dependency` kind.)

**And for a while nothing reached it.** `BASELINE_TEMPLATE` and `baseline_path`
had zero callers: `dependency_audit.py` (since removed) spelled out its own literal,
`structural_lint.py` spelled out its own, and `doctor.py` built a third with an
f-string. Moving a constant is not the same as removing a divergence -- the
divergence was three literals, and adding a fourth definition nobody called left
all three. Both checkers and `doctor` now format the template, so `design_pins`
pinning the symbol means something again.

---

## 2026-08-06 · What building found that reading could not

Thirteen agents read the design and produced roughly a hundred and twenty
findings. Building it produced about fifteen. The interesting thing is that the
two sets barely overlap, and the reason is structural.

**Reading finds rules that are wrong. Building finds compositions that are wrong.**

| Rule A, correct | Rule B, correct | Together |
|---|---|---|
| a signature leaves a record in git | a repo-scoped claim is judged on tree content | the record moves the tree, so signing expires its own signature |
| `test` is judged on tree content | `.pyc` had been committed | running tests staled `scope`, running `scope` staled tests, nothing could ever ship |
| the gate must run the real checker | the subject said `task_id: "fixture"` | two lines pass every fixture and go green in production |

None of the three lives inside a single rule, so no reader was going to find
them. **A reviewer reading a diff is structurally unable to see this class** —
which is a first-hand argument for why the checker layer earns its keep over
the reviewer layer, and belongs in the four-layer argument's L2.

And they compound: the signature livelock was created by the fix for the
staleness bug. Every repair makes new pairs, and the new pairs have been
reviewed by nobody.

### So: `v4 audit --compositions`

For every pair of repo-scoped claims, ask whether answering one left the other
judged against a tree that no longer exists.

**`LIVELOCK` is an approximation, and the wording here used to overstate it.**
The code does not compute the reverse direction: it marks a pair `LIVELOCK` when
*both* claims have been answered more than once, which is evidence of a
back-and-forth rather than proof of one. A pair that collides one way and where
both sides happen to have re-run -- ordinary for a repo-scoped claim that went
red then green -- is printed the same way. The one-way finding is exact; the
livelock label is a signal to go look.

Run against this repo's own history, with no hint about what to look for:

```
t-004: [one-way] answering test left scope judged against a tree that no longer exists
t-005: [one-way] answering test left scope judged against a tree that no longer exists
t-006: 6 repo-scoped kind(s), no pair invalidated another
```

t-004 and t-005 are the committed-bytecode era. t-006 is the first task after
it was fixed. **It reproduced the defect from recorded data without being told
the defect existed.**

Its limit is worth stating: it reasons from what checkers actually wrote, so a
collision has to happen once to be seen. It catches the second occurrence, not
the first.

It is a kernel command rather than a checker, for a reason the spec now names:
it audits the ledger, and the registration gate builds fixtures out of files. A
fixture case gets a fresh empty ledger, so no red case is expressible and every
fixture would pass for a boring reason. **A checker that cannot be gated has no
business being one.**

## And the lint premise I had been citing was itself a rule bug

I quoted "206 critical, 194 of them one rule" throughout the design as the
reason lint needs a baseline. Verified today: `snowball-lint.py:75-76` says in
its own docstring that a package assembling its public surface from private
submodules is "the boundary being implemented, not crossed" — and `:139` gates
that exemption to `__init__.py`. Any file aliasing its own package is flagged.
All 188 were that shape.

Rewritten rules, same 297 files: **200 criticals → 23**, 0 false positives in
35 hand-checked. And the number that decides whether it can be adopted, from a
59-commit replay with the baseline frozen at the start: **0 of 59 commits fail
with a baseline, 22 of 59 without.**

---

## 2026-08-07 · The bypass gate found thirteen ways past twenty checkers on its first run

The registration gate proved a checker could tell two states apart. Whether an
author who knows the rule can walk around it was never asked, and the spec said
the anchor was a person reading the checker's diff -- which is not a mechanism.

`bypass/` is required now: three cases minimum, each a real evasion of that
specific rule, each of which must still exit 1. It found thirteen on the first
run, and every fix is a rule rather than a patch:

| checker | the evasion | why it worked |
|---|---|---|
| `test-shape` | `g = inspect.getsource; g(f)` | looked at call sites, not references |
| `test-shape` | a path assembled a line earlier | looked inside the call node, not the function |
| `test-shape` | a comment naming `Semaphore` | a text search read a mention as a bound |
| `dal-write` | `verb + " INTO brands ..."` | no single literal held the whole statement |
| `route-auth` | `_unused = require_permission` | binding the name decided nothing |
| `test-weakened` | every body replaced with `pass` | counted definitions, not live tests |
| `test-weakened` | `@unittest.skip` | same |
| `secret` | a key split at the twentieth character | neither half matches |
| `review-finding` | call the symbol once, then assert its source | tracer satisfied, assertion still empty |
| `registry-consistency` | a rule whose text is three spaces | present is not saying something |
| `control-plane-budget` | two hundred statements on one line | counted lines |
| `control-plane-budget` | code in a directory nobody had listed | an allowlist |
| `dep-provenance` | dependencies declared in `reqs.txt` | recognised files by name |

Three of the checkers in that table -- `dal-write`, `route-auth` and
`dep-provenance` -- were registered at the time (2026-08-07) and are not kinds
today: all three were removed on 2026-08-24 at `a9ae5fb`.
`kernel/analysis/route_auth.py` survives as a library with no checker or
detector importing it.

Two of those forced inversions rather than fixes. Lines became AST statements,
because a semicolon does not make a control plane smaller. And the allowlist of
counted directories became a denylist of excluded ones -- **an allowlist is
walked around by putting the code somewhere nobody thought to name, which is
what every allowlist eventually is.** `dep-provenance` took the same shape:
requirements files are recognised by content now, because `pip install -r
reqs.txt` works and a name glob does not.

### Two things I got wrong writing the cases

**A bypass case that copies a red case passes and proves nothing.** I did that
for four checkers and all four "passed". It is ceremony, and it is now refused
in the spec by name.

**A bypass whose payload is not actually a violation is a bad fixture, not an
evasion.** I planted `AKIAIOSFODNN7EXAMPLE` as a secret; it is AWS's published
example and the checker correctly exempts it.

### And the gate reached the test that verifies the gate

Its synthetic checker asked whether a `raise` token appeared in a handler. The
bypass cases walked through it with a raise after an unconditional return and a
raise inside a nested function. It asks about escape paths now. **The gate made
the checker correct, which is the only argument for the gate that matters.**

---

## 2026-08-07 · Prevention was possible, and the predecessor had already built it

The design said since its first honest revision that out-of-band writes to the
ledger are detectable and not preventable, because no file permission, uid or
socket boundary stops a process that can already open the file. The sentence is
true. The conclusion drawn from it was not.

An INSERT trigger whose condition calls a function only the writing module
registers on its own connection. Measured, all five cases:

```
kernel inside writing()   inserts
kernel outside writing()  ABORT: inserts into attempt go through the kernel
external python sqlite3   no such function: v4_kernel_can_write
external sqlite3 CLI      no such function: v4_kernel_can_write   <- the cited attack
after DROP TRIGGER        inserts
```

The fourth row is the exact one-liner the design used as proof that prevention
was impossible. The fifth is why this is still not a boundary, and it stays in
the document.

`tests/test_kernel.py` had a test named
`test_forged_row_is_detected_not_prevented`. It failed on the first run after
this change, which is the whole argument in one line.

**What it means:** "no boundary can stop it" and "nothing can raise the cost"
are different claims, and the design had been treating the second as though it
followed from the first. Twenty lines and no daemon moved it from one shell
command to two, and the second one leaves a schema that says the row could not
have been written.

---

## 2026-08-07 · The write hook had been failing open since the day it was written

PreToolUse reads `hookSpecificOutput.permissionDecision`. The top-level
`decision` field, which this hook printed, is not supported for that event. So
the hook ran on every write, computed the right answer, printed a refusal
nothing reads, and exited 0 -- which means allow.

There was no error and no log. **The ship report even printed DEGRADED
correctly**, because `hook_seen` events were being written -- the hook was
firing, so the only signal anyone had said it was working.

Nothing would have caught it: the registration gate tests checkers against
fixtures, and a hook is not a checker. The predecessor knew the shape and said
so in as many words, in a document nobody had read.

**What it means:** a mechanism can be running, logging, and deciding correctly
while having no effect at all, and every observable will look healthy. The only
thing that finds this is executing it and reading the bytes it emits.

---

## 2026-08-07 · The same capability, added to two of three places, four times

| when | capability | had it | did not |
|---|---|---|---|
| earlier | mini-repo cases | `verify_checker` | `verify_detector` |
| earlier | `parent_content` | `verify_checker` | `verify_detector` |
| today | `parent_content` | `verify_checker`, `verify_detector` | `self_trigger` |
| today | fixture-set `facts.json` | `verify_checker`, `verify_detector` | `self_trigger` |

Each one surfaced the same way: a case failing for a reason that had nothing to
do with what was being verified. The fourth time it was extracted -- `_Case` and
`fixture_facts`, one each.

**Half-extracted at the time, and worth saying so.** `fixture_facts` is used by
all three gates. `_Case` was used by `self_trigger` alone; `verify_checker` and
`verify_detector` still each carried their own inline temp-dir-and-git-init. So
the next capability added to `_Case` reached one gate out of three -- which is
the failure this entry is about, one layer down. (Since closed:
`kernel/register.py` now constructs `_Case` in `verify_checker` and
`verify_detector` as well, and the comment at each site records the divergence
this paragraph predicted -- only one of the copies had seeded the ledger.)

**Three copies of a setup is three chances to give a capability to two of them.**

The same shape appeared again in the analysis modules. "Prose describing a write
is not a write" was learned by `dead_wiring` on itself, then independently by
`dal_write`, `test_shape` and `webhook_replay`, each from its own bypass
fixture. `kernel/analysis/pysource.py` owns it for those three -- `dead_wiring`
still has its own, and that one is not a duplicate: it needs the source with
docstrings blanked and line structure intact, not a set of names. Different
question, different shape, so it stays. Along with the question it
turned out to be half of: **can this statement run?** A `raise` after a
`return`, a replay guard after the handler returned, a `Semaphore` in a branch
nothing reaches -- all present, none reachable.

---

## 2026-08-07 · "No instance today" is not a refutation, and I made the error twice

An adversarial pass over 84 candidate checker rules kept 7. Reading the
refutations afterwards, 24 of the 77 were rejected for "no red instance exists
in the target repo today".

**That criterion is wrong for a preventive rule.** A repo with no unguarded
DELETE is what a rule against unguarded DELETEs looks like when it works.
Counting the bombers that came back is how you conclude the armour is
unnecessary -- and this project already had a name for that error, applied to
something else, weeks earlier.

Those 77 are in a `prevention` lens now, each carrying its own refutation.

Then, three commits later, I refused to build `webhook-replay` because neither
available repo has a webhook handler. **Same error, same session, hours after
writing the correction down.** It was built, on 2026-08-07. The checker has since
been removed (2026-08-24, `a9ae5fb`); `kernel/analysis/webhook_replay.py`
remains, used by `kernel/facts.py` to recognise Go handlers, with no checker or
detector consuming it and no `webhook-replay` kind registered today.

---

## 2026-08-07 · doctor asked eight questions and two were true of this repo

Every question `kernel/doctor.py` asks (eight at the time; 27 `_check_`
functions today) was silently true here at some point that day. Two still were
when it first ran:

**This repo had no facts table of its own.** It carried the reference repo's,
and the loader prefers `facts.<root>.json` and falls back -- so every detector
here had been reading another project's vocabulary. The exact failure the spec
records as the reason facts loading was rewritten, arriving from the other side.

**The hooks were wired in the template only**, so the repo that uses its own
framework had none of them firing.

Writing the facts table took two refusals from the validator and both were
right. It refuses an empty `auth_decision`; this repo has no HTTP routes, so my
first attempt declared none -- and being made to look again turned up five,
including the ledger write gate and the fixture gate. **They were easy not to
see because the subject is the kernel rather than a user.**

The third refusal was wrong, and that is the interesting one. It refused an
empty `ui_globs`, and this repo genuinely has no front end. A required key set
to `[]` is a statement; a missing key is a repo nobody catalogued. **Conflating
them forces a fake glob, which is worse than the gap.**


---

## 2026-08-07 · Reading four documents line by line, and what only that found

Every mechanical check over these documents passed. Then a line-by-line read of
all four found nine defects the checks structurally could not see, and following
them into the code found seven live bugs. The shape they share: **the form stayed
valid while the meaning went stale.**

**`四條全部要過:` above a block listing three.** The missing colour was
`bypass/` -- the one that found thirteen real evasions the first time it ran.
Anyone building `register` from `SPEC.md` §3 built the gate without it, and nothing said
so: the number was right when written, the block lost a line, and every check
here reads names rather than counts.

**`SPEC.md` said layer 1 was empty today and sent the reader to a section number
for it**, after layer 1 had 79 rules (96 doctrine rules today) and after they had moved to `SPEC.md` §14.
Both halves wrong, both individually well-formed. And the section it named sat
physically after the one numbered above it, so the pointer resolved to a CLI
walkthrough.

Seven live bugs, each found by following a sentence into the code:

| what the document said | what ran |
|---|---|
| `policy` picks between two behaviours | nothing read the value; `no_accepted_risk` signed exactly like `allow` |
| a conditional detector runs twice | "conditional" was a text scan of the detector's own file, so of the three detectors that read the table it saw one |
| three endings are fine | the hook blocked every one of them, forever, because it threw away `stop_hook_active` |
| the kernel-written set is excluded | there were two copies of the set and `checkers/scope.py` had the older one |
| every pin fails a check when it breaks | `design_pins` was pointed at one document, and the pin in another already named a superseded chain scheme |
| the baseline constant has one owner | `baseline_path` had zero callers and three literals spelled it out |
| every row cites where the symbol was seen | four rows cited a definition, and nothing ever ran `verify` |

**The last one is the pattern in miniature.** `kernel.facts verify` failed on any
commit that shifted a line, so it was never wired into CI, so the table drifted
until four rows cited symbols that were not there. A check that cannot pass does
not get run, and a check that does not get run is indistinguishable from one that
always passes. Splitting *moved* from *gone* made it runnable, and it is in CI
now -- the same move as `.v4/chain_head.json`, which exists because "the anchor
is CI running audit" was a job that could never fail.

Five of the nine document defects became checks rather than edits: a Chinese
numeral lead-in against the block below it, section numbers ascending, one row
per engaged kind, the disposition table against the register, and every fixture
colour the gate enforces named in the contract. The first of those has an
escape hatch that costs a sentence, because a block really can hold a control
row the lead-in does not count -- **an exemption with no reason is a line number
in disguise.**

The four remaining are wording, and they stay wording. **A checker that reads
one document and calls the other stale is a checker that has to know which one
is right.**

---

## 2026-08-07 · The gate that printed a verdict and threw it away

`v4 verify-detector` was written the same day as the checker gate and never
wired to anything. It ran the fixtures, printed `VERDICT: registrable`, and
exited. `SPEC.md` §2 said in bold that a conditional detector must pass it.

Nothing could tell whether one had. `derive` ran every file in `detectors/`.
`registry-consistency` compared detector *names* against `claim_kinds.json` and
never a hash. `claim.detector_sha` was taken during derivation from the file
that was about to run, so there was nothing to compare it with. **A gate whose
result is not written down is a gate nothing can require.**

`register-detector` writes `.v4/detectors.json`; `derive` refuses an
unregistered or edited detector and records it as not run; `registry-consistency`
and `doctor` check both directions.

**The gate found something on its first run, and then found something about
me.** `bundle_secret` (a detector of the time; the `bundle-secret` kind was
removed 2026-08-24) failed one green case. The reason is the one `SPEC.md` §2 already
gives: its detector asks whether a change touches client source and its checker
asks whether a secret reaches the bundle, so every one of the checker's green
cases -- front-end files with no leak -- is a file the detector must fire on.
Four of the five passed anyway. Not because they were right: they were not
mini-repos, so their paths resolved outside `ui_globs` and the rule never ran.
**One case failed for the real reason and four passed for no reason.**

Then I registered five more detectors against their checkers' fixture sets
within ten minutes, because the convention `<name>_detector` existed and nothing
enforced it -- four of those already had their own set, sitting unused since the
day they were written. So the rule became a refusal.

And the refusal was wrong at first, because the `SPEC.md` wording was too strong and the
code says so in two places: `external_write` and `fail_closed` share one
`kernel/analysis/` module between detector and checker and say in their
docstrings that they "can never disagree". Where that holds, one fixture set is
not a shortcut, it is the guarantee. The refusal now asks whether they share an
analysis, which is decidable, rather than whether the directory is shared, which
was a proxy.

Three smaller things fell out. `policy` was a required config field nothing
read, so `no_accepted_risk` signed exactly like `allow_accepted_risk` -- and 62
fixtures had `"policy": "p"` in them, which is what a field means when it means
nothing. `stop_gate` offered three endings and blocked all of them forever
because it discarded `stop_hook_active`. `kernel.facts verify` could not pass on
any commit that shifted a line, so it was never in CI, so this repo's own table
drifted until four rows cited symbols that were not there.

**The common shape: a mechanism that reports rather than records.** A verdict
printed to a terminal, a config value read by nobody, a hook that computes the
right answer and returns it in a field the platform does not read, a check that
cannot pass. Each of them looks like a working part from the outside, and the
only way any of them was found was following a sentence in a document down into
the code that was supposed to implement it.

---

## 2026-08-07 · The reviewer wrote its findings into a transcript

An agent given only the five documents was asked what it could not build. Its
list was mostly rule corpus rather than mechanism, which is the honest shape of
this project. Verifying the rest one at a time in the code, three of its claims
were wrong and one was worse than it said.

The one that was worse: `.claude/agents/reviewer.md` told the reviewer to print

    V4-CLAIM: kind=review-finding file=<path> symbol=<s> note="..."

`parse_claim_lines` is called from exactly two places -- `derive`, on a
detector's stdout, and the registration gates, on a fixture run. **Nothing reads
a reviewer's output.** Every finding the reviewer layer ever produced went into
its own transcript and stopped. The entry point that works, `v4 review add`, was
not mentioned in the prompt at all.

And the format handed the reviewer `kind=` and `file=`, which is exactly what
`SPEC.md` §8.5 exists to take away from it -- `RATIONALE.md` lists "a reviewer
emits `kind=lint file=README.md` and buys a guaranteed PASS" as a rev-1 defect
it had already removed.

`dead_wiring` now asks where a `V4-CLAIM:` line is written that nothing parses.
Making that check work required the lesson `pysource` exists for, in the form
markdown has it: the paragraph telling a reviewer *not* to print the line tripped
the rule until fenced blocks were separated from prose. **A rule caught by the
sentence explaining the rule** -- which is the bug `dead_wiring` originally found
on itself.

Three claims that did not survive checking. `v4 register` does have `--timeout`.
`ship` and `check` do return 1 -- the exit contract was correct and merely
undocumented. And red-green granularity is not confused: `executed_files` is the
suite-wide question for a `test` claim and `redgreen.verify` is the per-symbol
one for a `review-finding`, two mechanisms rather than one inconsistency.

**What was true, and is now written down:** `--out` is validated as JSON and
never read (`RATIONALE.md` said the kernel reads it, by schema); `v4 coverage`
reads `.v4/obligation_catalogue.json`, a filename no document had ever
mentioned; only `verify` and `register` consult a measurement round, so "the
tool refuses to measure" was broader than what runs; the three hooks use two
different output protocols and picking the wrong one fails open silently;
`depends_on_kind` references the first matching claim and no kind uses it yet;
and one engagement sentence is owed per claim, not per kind -- nineteen claims
of one kind means nineteen sentences.

The last of those got its own section, because it is the honest answer to "can
another model build this from the documents". `SPEC.md` §13.5: the mechanism is
in the documents and **the criteria are not**. They live in `.v4/*.json` and
`kernel/analysis/`, which is where they belong -- at the time 22 kinds, 280 lens
checks, 254 disposition records, 12 secret patterns, 3 lint rules (2026-09-02:
21 claim kinds, 303 lens checks). Saying so is the
difference between a reader who supplies them and a reader who ships a complete
apparatus with nothing in it.

---

## 2026-08-07 · Two exemptions, and what each was hiding

Layer 1's 88 rules (at the time; 96 doctrine rules today) were in English in a project that works in Chinese. That is
the one layer with no gate -- it works only by being read at the moment it
applies -- so the language is friction, not a preference. Translating them
turned up two checks that were quiet for two different reasons.

**`layer_one_has_what_it_was_assigned` was failing open the whole time.** It
carried `except ImportError: return []`, and `checkers/spec_coverage.py` never
put the repo root on `sys.path`, so `from kernel import doctrine` failed on
every run and the check reported clean. It exists to catch exactly two rules
`SPEC.md` §9 assigns to layer 1 by name, and for the entire time both were missing,
it said nothing. The fix separates "this repo has no kernel" -- an adopter owes
no doctrine -- from "it has one and the import failed", and only the first is
silence.

**`counted_claims` exempted every line containing `engagement`,** on a reading
that was correct: 當時 `15 個帶 engagement 嘅 kind` is not a claim about 22, so
comparing it to the kind count would be wrong. What that made it, though, was
the one number nobody else could settle *and* the one number nothing did.
`SPEC.md` §9 said 15 kinds and 36 rules for as long as there were 16 and 37 (at the time;
10 engaged kinds and 29 engagement rules on 2026-09-02). It is settled
now, against `claim_kinds.json`, rather than exempted.

**A third number escaped by formatting.** `SPEC.md` §13.5's corpus table carried bare
figures in a right-hand column -- `22`, `22`, `7` -- and `counted_claims` reads
`N 個 X`. A cell with no measure word is invisible to it, so the table said
22 kinds, 22 checkers and 7 detectors while there were 23, 23 and 8 at the time
(2026-09-02: 21 claim kinds, 21 registered checkers, 11 conditional detectors). The column
carries measure words now. **A format that escapes its own check is a format
with no check.**

The shape all three share is the one this project keeps returning to: the
exemption, the import guard and the bare number are each locally reasonable,
and each converts a check into a thing that cannot fail.

---

## 2026-08-07 · Eleven cross-layer edges, one going the wrong way

Every file in `kernel/analysis/` states in its own docstring that it does no
I/O. `external_write.py` imported the whole of `kernel.facts` -- which opens
files and runs git -- for two functions that happen to be pure. The sentence was
true when written, the module grew a loader, and nothing read the sentence
again.

Measured before building anything, which is this project's method: eleven
cross-layer imports across the repo, and exactly one going the wrong way.

**Baselining it would have been available and wrong.** Layer 1 carries "a rule
violated long-term and never enforced must be either enforced or changed;
leaving both undone is not an option", and a baseline with one entry that the
author could fix in an afternoon is leaving both undone. So `kernel/facts.py`
split: the grammar -- what a pattern is, when it matches, how a table validates
-- into `kernel/analysis/facts_grammar.py`, the loader staying put and
re-exporting it. `kernel.facts.scan_source` is still the same object, so no
obsolete path is preserved; this is a module composing the one below it, not a
compatibility layer.

The rule is now `.v4/layers.json` plus a checker. The config is **an allowlist
of edges**, and that direction is deliberate in a project that has twice
recorded allowlists failing: the legal edges are few, named and arguable, while
the illegal ones are every pair nobody thought about. The inversion this project
keeps making -- allowlist to denylist -- applies where the *set being permitted*
is open-ended. Here it is the set being forbidden that is open-ended.

**ArchUnitPython says the same things and is not used.** It expresses layer
rules, dependency direction and cycles, has no runtime dependencies, and is a
pytest library. A checker here is stdlib-only, takes three flags and returns one
of four exit classes, so adopting it would mean wrapping a test runner in a
checker to get a rule that is eighty lines of AST walk. **The rule language was
worth copying. The dependency was not.**

Thirteen fixture cases passed first run, including all three bypasses -- an
import inside a function body, `import pkg.loader as _l`, and a comment claiming
the edge was permitted. All three fail for one reason: the judgement walks the
AST rather than the text.

---

## 2026-08-08 · The three-arm experiment, and what its first run measured

Two tasks, three arms each, from `adopter_a`: an object-storage staging adapter (192 lines
in the original) and a chat-platform bot API transport (607 lines). Each oracle was
written from that module's real defect history -- the commits that fixed it --
committed and hashed before any arm ran.

**The first task measured the experimenter.**

`SPEC.md`-style prose put five defect classes in the armed layer, on the reading
that `external-write` covers them. Run against the registered checker set, the
answer was zero: not one of the five fires anything. The L1 layer was empty, so
the first stop-loss -- "B has to beat A on L1" -- had nothing to evaluate.

The procedure in `RATIONALE.md` §14.2 asks for one assertion, that L2 is
disjoint from the checker set. I ran it, and it passed. **It does not ask for
the other one**, that L1 is contained in the checker set, and the asymmetry is
not neutral: a wrong L2 makes something the framework caught look like evidence,
and a wrong L1 makes something the framework did **not** catch look like
evidence. The second is the one the stop-loss rests on.

A second, independent failure in the same run. Both framework arms raised only
`scope`, `secret` and `test` claims -- no `external-write` at all -- because the
facts table I wrote for the sandbox was narrower than the detector's built-in
vocabulary, so the narrowing guard refused that detector on every round.
`facts_narrowed_detection`, five times, in the ledger. **The guard did exactly
what it was built for and I did not read the output.** It was added the same
week, documented in `SPEC.md` §4, and the first time it fired on something of
mine I walked past it.

**And the scoring was not reproducible.** Three scorers, one frozen oracle,
three diffs. Arms A and C have the same structure on L1-1 -- a
`verify_anonymously_readable` that no production path calls -- and were scored
differently, each scorer flagging the ambiguity and choosing a different reading.
Re-scored mechanically (does any non-test caller reach the readback), A and C
both have it and B does not. **A criterion a scorer interprets is a criterion
that measures the scorer.**

What the run is worth, stated plainly: it is a harness validation that found
three real defects in its own method, and one usable directional signal on the
unarmed layer (A 2 defects, B 1, C 1). It is not a verdict, and it was never
going to be at n=1 -- `RATIONALE.md` §14.1 says K = 5–8 for a reason.

---

## 2026-08-08 · Task 2, and a criterion that a missing feature satisfies

The chat-platform bot API transport, 607 lines in the original, three arms, oracle frozen
and its L1 coverage asserted by running first -- one of the four L1 items is
genuinely armed (`external-write` fires on a send whose result is discarded),
the other three are L2.

**All three arms scored zero on all nine items.** No arm has any defect the
oracle can see.

The floor `RATIONALE.md` §14.1 warned about, arriving exactly as described: with
0–3 defects per arm, "B beats A on L1" is not decidable, and at 0–0–0 there is
nothing to decide. The stop-loss table printed `FAIL` on the first criterion
purely because `0 < 0` is false. **That is a tie at the floor, not B losing**, and
the comparison should say so.

**And two of the nine criteria were vacuous.** L1-1 is "a non-idempotent method
is retried after a transport failure" and L2-1 is "the retry policy and the
operator diagnostic are two lists that can disagree". Both presuppose a retry
path. Checked mechanically -- a request call inside a loop -- **none of the three
arms implements retry at all**, so both criteria are clean by construction. The
original does retry, on a `_REPLAY_SAFE_METHODS` set, because a poll loop over a
flaky socket needs one; all three arms instead pushed the decision to the caller
and said so in the failure message.

A criterion a missing feature satisfies measures nothing. It is the Wald error
wearing different clothes: I counted the planes that came back, and this time I
wrote the counting rule myself.

**Two tasks, and the experiment has produced no signal about the framework.**
What it has produced is four defects in its own method: an unasserted L1
classification, a facts table that disarmed the detector under measurement,
a scoring rule that measured the scorer, and a criterion that a missing
capability satisfies. Every one of them is the same shape -- a statement about a
mechanism that nobody ran.

That is worth more than a n=1 result would have been, and it is not what the
experiment was for.

---

## 2026-08-08 · rev 3: six tasks in a chain, and the first result the design asks for

Six consecutive changes to `app/chat/client.py`, taken from the real commit
sequence, each arm building on its own previous output. Base is `6a6735ff` --
a 121-line transport that already exists, so the arms extend rather than write.
The requirements are six short notes in a person's voice; none names a solution.

Seven predicates, each validated against the history it came from: True on the
state the real fix repaired, False after it. One (`D5`, tenant isolation) was
dropped because its real fix spans four files and the isolation lives outside
this experiment's scope -- a predicate that cannot be shown to fire does not
ship. L1/L2 was measured by probing the registered checker set, not asserted:
four armed, three unarmed.

```
          D1  D2  D3  D4  D6  D7  D8     L1  L2  存活
A  T1      有   —   —   —   ·   ·   ·      0   1   1
   T3      有   ·   —   —   ·   有   ·      1   1   2
   T6      有   ·   ·   有   ·   有   ·      1   2   3
B  T6      有   ·   ·   有   ·   ·   ·      0   2   2
C  T6      有   ·   ·   有   ·   ·   ·      0   2   2
```

**Stop-loss ① (B beats A on L1): PASS.** A carries `D7` -- a failure swallowed
on a send path -- from T3 to T6, four steps. `fail-closed` is armed for that
class, and neither framework arm ever carries it.

**Stop-loss ② (B does not lose to A on L2): PASS.** Two each.

**Stop-loss ③ (B's accumulation is not steeper): PASS.** A 1→3, B 1→2, C 0→2.

**And the one both framework arms lost.** `D1` -- the bot token reaching a
reader through `raise ... from exc` -- is present in the base, is unarmed, and
survives all six steps in every arm. C cleared it at T1 and let it back in at
T2. Six changes were made next to it and nobody looked. That is the snowball
this design was built to see, and it is visible.

**Three predicate false positives, caught before reporting.** A broadcast loop
(`for chat in audience: send(...)`) read as a retry. A dict read
(`payload.get("chat")` inside a `try`) read as a retry. Both turned one arm's
whole T6 red, and the first turned *all three* red at the same step -- which is
what a predicate reporting on itself looks like. The fix is the lesson
`kernel/analysis/route_auth.py` already carries: a bare verb name is not a verb,
the receiver has to look like a transport.

n is one chain. The slope is measured, not the variance of the slope.
