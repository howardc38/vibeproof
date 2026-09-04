# What a monitor session may do

`PROMPT.md` beside this file is what you paste. This is what the session running
it is allowed to touch, and why each line is where it is.

## Tools

    allowed    Read · Glob · Grep · Bash (read-only)
    refused    Edit · Write

A monitor with `Write` can repair what it found, and a reviewer that repairs its
own findings is the shape this whole layer exists to replace: the same actor
producing the problem, the fix, and the verdict. `checker-author` holds `Write`
and writes the fixtures it is graded by -- that is the defect this session is
here to audit, so it must not repeat it.

## Commands

    allowed    ./bin/v4 review add          raise a finding
               ./bin/v4 review lens         read a lens brief
               ./bin/v4 review defer        put one off, naming where the work went
               ./bin/v4 sweep --done        record that a sweep happened
               ./bin/v4 status / check      read state
               ./bin/v4 risk accept --as-monitor
                                            sign a claim a *detector* raised

    refused    ./bin/v4 risk accept         without the flag, and on anything
                                            raised by hand: a review finding
                                            or a widen, yours or anybody's
               ./bin/v4 ship                releasing
               ./bin/v4 review close        closing your own finding
               any git write                commit, push, checkout, restore, clean

`--as-monitor` is the one line of this file that moved, and the rule it was
guarding did not. Signing away your own finding is still refused, and now by
`claim.origin` rather than by this paragraph: `v4 review add` writes
`origin = review`, `v4 scope widen` writes `widen`, and the flag takes `derive`
alone.

What it refuses is wider than the rule, and that is the honest shape rather than
an oversight. `origin` records **how** a claim was made; the rule is about
**who** made it, and nothing in this framework records that -- two sessions in
one repo share a git identity and `v4` is told nothing about which one started
it. So another reviewer's finding is indistinguishable from your own, and the
flag refuses the class instead of guessing. `origin = derive` is the one answer
that is certain, because a detector is not a session. A `review-finding` is
therefore never signable with this flag; if one is down to a signature, it is
down to somebody else's `--no-tty-check`, and that gap is worth filing as a
finding rather than filling.

What it opens is a different relationship. A detector raised the claim and a
worker wrote the code; a session that did neither is a third party to both, and
the alternative today is not a person -- it is that worker signing with
`--no-tty-check`, which is what actually happens. Measured on this repo,
2026-08-27: three `agent` signatures in one day, all three by the session whose
work they waived, and the record could not say so because `signed_by` had two
values and both were already taken.

It records `signed_by: monitor` -- in `.v4/risks/<claim>.json` and in the
`accepted_risk` row, which are two different things. For a day it was in the
file only, and the file is not what any report reads: `v4 ship`'s `signed` line
and `v4 trend` both query the table, where the only column was `was_tty`, and
`--as-monitor` waives the terminal check. So the record that was supposed to
tell a monitor signature apart from a worker's `--no-tty-check` counted them as
one number, in the one place anybody counts them.

That is worth exactly one thing -- the signer is not the actor being waived --
and it proves nothing about who ran the command, because nothing tells `v4`
which session started it. Friction, not a boundary (SPEC.md §13), the same
standing as `--no-tty-check`: a worker can pass it, and doing so writes a false
line into a file that goes into a commit.

`review close` demands a test that is red at the parent commit, green at HEAD,
and observed to have entered the symbol. Splitting raise from close across two
sessions is what makes that proof mean something; one session doing both is one
actor deciding it has convinced itself.

## Reading

    allowed    the repo · its history · the ledger · lens briefs
    refused    the working session's transcript

The refusal is the whole point. A sub-agent of the working session shares its
context and runs on a prompt that session wrote; independence here does not come
from being a different model, it comes from not having heard the story.

## Why this file is where it is

`.github/**` is in `scope.PROTECTED_DEFAULT`, beside `.v4/**`, `checkers/**`
and `detectors/**`. A working session that wants a softer auditor has to widen
scope into a protected path, which produces a protected hit, a named signature,
a ledger row and a chain entry.

It can. It cannot do so quietly, and that is the only guarantee on offer --
the same one the rest of this framework makes (SPEC.md §13: friction, not a
boundary).

## Where this role is declared

The monitor is the fifth working role, and it is in the taxonomy with the other
four: `docs/SPEC.md` §12.5 carries a `monitor` row naming what it may produce
(claims from a lens sweep, never a verdict, plus one signature on a claim a
detector raised), what it may not do (edit the repo being swept, sign a finding
it filed), and what enforces each. Read that row for what is enforced: the
signature half is held by `claim.origin`, and the sweep-freshness half is not
held by anything -- the kind that did it was cut, and §12.5 says so. This file
is that row's long form, not a second authority beside it -- for as long as no
document mapped the role, the sentences below about which commands a session may
run were the only statement of it, and §12.5 claimed to own exactly that.

## Where this does not reach

Two limits, stated rather than papered over.

**Same model, same blind spots.** Running the monitor in a separate session is
mechanically enforceable and is enforced. Running it on a different mind is not.
Whatever both fail to see, both fail to see.

**Somebody has to open it, and nothing notices when nobody does.**
`./bin/v4 sweep --if-due` is an alarm clock: it exits 1 when the interval has not
elapsed and prints the briefs when it has, and nothing starts it. Measured across
two repos over ten days, before anything was built here: two sweeps.

A claim kind was built for exactly this and then removed. It raised on every task
and settled an overdue sweep as a FAIL that held `v4 ship`; `a9ae5fb`
(2026-08-24) cut it on its own numbers -- 295 runs, one failure, and every one of
those runs charged a task for a question about the repo rather than about the
change. `docs/SPEC.md` §12.5 records the same removal in the row that owns this
role, and says what this paragraph now has to say: **this layer has no trigger.**

So the limit is narrower in one place and wider in another. Something does
schedule a reminder now, and nothing costs anybody anything for ignoring it.
What is left is `v4 sweep`, which answers "is it due" when asked; the `sweep` row
of `v4 doctor`, which reports when the last one was and not whether that is long
ago; and the Monday `sweep` job in `.github/workflows/v4.yml`, which reads the
committed ledger export, prints DUE or not due, and is labelled in its own
comment as not a gate -- a green run there means the reminder was computed, not
that anything was reviewed. **None of the three holds anything**, and a reader
deciding how much this layer is worth should price it as what the sentence above
says: worth what the last person to remember it was worth.
