"""Accounting for the request itself.  SPEC.md §4.7.

Every mechanism in this framework asks how the work was done. Not one of them
asks whether the thing that was asked for arrived. Measured on an eight-task
run of a fully armed repo: three of the eight briefs came back with less than
they asked for -- no way to send a file from disk, no way to read what a local
Bot API server actually answers, and a bug report answered by making the bug
expressible rather than by fixing it -- and nothing anywhere raised a claim,
because `task.request` was written in two places and read in none. That is the
shape this project's own `dead-wiring` checker exists to find, sitting in the
kernel.

The doctrine is one-sided in the same direction: 34 of its 88 standing rules
say some version of do not do more, and none says do enough.

The obvious fix is the one that must not be built. V3 derived obligations from
prose, so editing the prose changed the obligation set and produced the next
failure -- nine rounds on one phase, two and a half hours, no code. A checker
that reads a request and decides whether it was met is that machine again.

So this does not judge. It makes the accounting exist:

    quote the request back, in pieces, verbatim, until the pieces cover most
    of it -- and say for each piece what it got.

A quote is checked by `str.__contains__` against the stored request. It cannot
be paraphrased, so it cannot drift from what was asked. A piece that got
nothing is declared `not done` with a reason, which is a sentence a person can
argue with in a diff. Narrowing stays available -- it stops being silent.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .config import DEFAULT_THRESHOLDS
from .ledger import insert

#: One place decides it. Writing `40` here as well is the dual truth
#: `LINT-CONFIG-DUAL-TRUTH` exists to find, and it found it here first.
MIN_CHARS = DEFAULT_THRESHOLDS["min_chars"]

KIND = "request-coverage"

#: Whitespace and the punctuation both languages here use to separate clauses.
#: Excluded from the denominator, because quoting a comma proves nothing and
#: forcing a worker to include one is a rule about typing.
_NOISE = re.compile(r"[\s，。、,.!！?？；;：:「」『』\"'()（）\[\]【】\-—…·]+")

#: Where one thing being asked for ends and the next begins.  Deliberately not
#: every punctuation mark in `_NOISE`: a bare `.` appears in `app/client.py`
#: and a `:` in `08:00`, and splitting there would cut a clause in half and
#: refuse a quote that is one piece of the request.
#:
#: `、` is a list separator inside one clause, not a boundary. It was in this
#: set for one revision, and the registration gate refused four green fixtures
#: over `我要佢識send相、片同檔案` -- one thing being asked for, cut into three.
_CLAUSE = re.compile(r"[，。；;!！?？\n]+|(?<=[a-z0-9)\]])\.\s+")


#: Where this task's request stops and the previous task's begins.
#:
#: This module owns it because this module owns what a request looks like.
#: `lifecycle.carry_forward` writes the line and `own` below matches it, and
#: for a while each spelled it out separately -- a literal in one file, a regex
#: in another, nothing holding them together. Change either spelling and the
#: reader silently stops recognising the writer: every carried clause counts as
#: this task's own work again, with no error anywhere. One definition, and
#: `carried_line` is the only way to write it.
CARRIED_OPEN, CARRIED_CLOSE = "[continues ", "]"
_CARRIED = re.compile(
    "^" + re.escape(CARRIED_OPEN) + r"\S+" + re.escape(CARRIED_CLOSE), re.M)


def carried_line(after: str) -> str:
    """The header `own` will recognise.  `lifecycle.carry_forward` writes it."""
    return f"{CARRIED_OPEN}{after}{CARRIED_CLOSE}"


def _weight(text: str) -> int:
    """How much of a request a span is worth: its characters, less punctuation."""
    return len(_NOISE.sub("", text or ""))


def own(request: str) -> str:
    """This task's own request, without the block `--after` carried in.

    `--after` is right and stays: without it a worker at its context limit can
    only push on until it breaks, start fresh and undo the last task, or paste a
    summary it invented. But the block it carries is the previous task's *whole*
    request, and that request already carried the one before it, so a chain
    accumulates every request in it.

    Measured on a six-task chain: 42, 129, 197, 307, 386, 501 clauses, while the
    sixth task changed four files. In the fifth, 293 of 343 entries (85%) were
    `--not-done` saying "that belonged to an earlier cut" -- an hour of writing
    receipts for work somebody else already accounted for.

    Coverage asks what *this* task was asked to do. The carried block is context
    for the worker, not a request to answer, and it is marked as such.
    """
    m = _CARRIED.search(request or "")
    if not m:
        return request or ""
    mine = (request or "")[:m.start()].rstrip()
    # A request that is nothing but carried context has none of its own to
    # measure. Keeping the old behaviour beats a zero denominator.
    return mine or (request or "")


def clauses(request: str):
    """The separately-askable pieces of a request.

    One entry per clause is the decomposition this whole gate is for. The floor
    was set at 75% with a comment saying 100% "would be answered by quoting the
    whole thing in one entry" -- but lowering a floor does not stop that move,
    it only makes less of it necessary. Measured: one entry carrying the entire
    request scored 100% and passed, which is the accounting-shaped silence the
    module docstring says it exists to remove, reached in one command.

    So a quote may not span a boundary. A request that really is one thing stays
    answerable by one entry; a request that asks for two cannot be answered by
    one span that swallows both.
    """
    request = own(request)
    out = [c.strip() for c in _CLAUSE.split(request or "") if c and c.strip()]
    return out or [(request or "").strip()]


def spans_a_boundary(request: str, quote: str) -> bool:
    """Does this quote reach across two things that were separately asked for?"""
    request = own(request)
    start = 0
    # Keep a clause's closing punctuation with that clause. Stripping it and
    # then comparing an exact quote misreported one whole sentence as two.
    for end in [m.end() for m in _CLAUSE.finditer(request)] + [len(request)]:
        if quote in request[start:end]:
            return False
        start = end
    return True


def _now():
    return datetime.now(timezone.utc).isoformat()


#: What "done" means for one piece, in the requester's terms.
#:
#: `--quote` and `--symbol` answer "did you account for it". They cannot answer
#: "is what you built the thing that was asked for": the check is
#: `str.__contains__` plus a character ratio, so a task that quotes the request
#: perfectly, names a real symbol, and implements something else scores 100%.
#:
#: Measured on the X/Y run: three of eight briefs came back with less than they
#: asked for and nothing raised a claim. `cover` was the answer to that
#: measurement, and it answered the accounting half only.
#:
#: The doctrine already required this and had no mechanism: 「驗收門檻由請求者
#: 給出。只有程式碼已經決定了的,才可以推斷。」
#:
#: Deliberately not judged. A checker that reads a request and decides whether
#: it was met is V3's prose-derivation machine again -- nine rounds on one
#: phase, no code. Making the sentence exist is the whole of it, because a
#: written acceptance condition is one a person can argue with in a diff.
ACCEPTANCE_MIN = 12


class NotInTheRequest(ValueError):
    """A quote that is not a literal span of what was asked."""


def _names_in(path: Path) -> set | None:
    """Every name this file binds.  None still means no verdict.

    Delegated to `analysis.symbols`, which answers for Python, Go and
    TypeScript/JavaScript. The Python half moved there unchanged; what changed
    is that `None` now covers a smaller set of files. The repair this function
    exists for -- an entry naming a function that does not exist, accepted and
    counted -- reached Python and stopped, and 79 of 113 DeepSWE tasks are not
    Python.
    """
    from .analysis import symbols
    return symbols.names_in(path)


#: Suffixes that make a bare reference look like a path in this repo.  Used
#: only to decide whether a value with no `::` is worth checking the existence
#: of; a value that is not obviously a path is left alone.
SOURCE_SUFFIXES = frozenset({
    ".py", ".pyi", ".go", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
    ".rs", ".java", ".rb", ".kt", ".swift", ".c", ".h", ".cc", ".cpp", ".cs",
})


def _looks_like_repo_path(ref: str) -> bool:
    """Is this bare reference obviously meant to be a file in this repo.

    Conservative on purpose, and the direction matters: this would rather let a
    made-up path through than call `go test -run TestFoo` a broken reference,
    because the second mistake makes `--test` unusable for every project that
    is not pytest -- and the docstring below names exactly that as legitimate.

    Whitespace is the tell. A runner invocation has some; a path does not.
    """
    ref = ref.strip()
    if not ref or any(c.isspace() for c in ref):
        return False
    return "/" in ref or Path(ref).suffix in SOURCE_SUFFIXES


def unresolved(root, ref: str):
    """Why `path::name` does not point at anything, or None.

    `--symbol` and `--test` were free text. Measured on the first real task run
    through this: two entries named `core/config/gcs_bucket.py::_require_bucket`,
    a function that does not exist -- the real one is `validate_gcs_bucket_name`
    -- and both were accepted, counted, and took the kind to PASS at 100%.

    Naming a symbol that is not there is the accounting-shaped silence this
    module's docstring says it exists to remove, reached by a typo. A reference
    with no `::` is left alone: a bare path, a test id in another runner's
    notation, and a symbol in a language this cannot parse are all legitimate.

    **This function parses the subject.** It reaches `symbols.names_in`, which
    is `ast.parse` for Python, so the removed `request-coverage` checker opened
    a source file every time an entry named one -- it passed `root` in for
    exactly that. The reason written down for removing it said the opposite
    ("never opened a source file"), was copied forward into `docs/SPEC.md`, and
    was found false on 2026-08-26 by the `request-fidelity` lens. It read
    source; what it never did was judge source, and that is the real reason.
    """
    ref = (ref or "").strip()
    if not ref or root is None:
        return None
    if "::" not in ref:
        # A bare reference is legitimate -- a plain path, a test id in another
        # runner's notation -- and the docstring above says so. But "legitimate"
        # and "unchecked" are two different things, and this was the second: a
        # value that looks like a path in this repo and is not one accounted for
        # nothing, in all 113 tasks, and dropping the `::` was all it took.
        if _looks_like_repo_path(ref) and not (Path(root) / ref).is_file():
            return f"{ref} is not a file in this repo"
        return None
    rel, _, name = ref.partition("::")
    name = name.split("[")[0].strip()          # pytest parametrisation
    path = Path(root) / rel.strip()
    if not path.is_file():
        return f"{rel.strip()} is not a file in this repo"
    names = _names_in(path)
    if names is None or not name:
        return None
    if name not in names:
        return (f"{path.name} does not define {name!r}. Naming a symbol that is "
                f"not there accounts for the work the way a receipt for a name "
                f"nobody can find accounts for a purchase.")
    return None


def fault(request, entry, min_chars=MIN_CHARS, root=None):
    """Why this entry does not account for anything, or None.

    Shared with `measure()` on purpose. The first version validated only in
    `record()`, so the rules lived on the CLI path and anything that reached the
    ledger another way -- a fixture, a future writer, a hand-edited row --
    counted in full. A checker that trusts its own input is the hollow-scanner
    shape, and its three bypass cases were all really failing on coverage.

    **There is no checker any more.** This said "shared with the checker", and
    the request-coverage checker was removed -- `unresolved()` above says so in
    as many words, and `checkers/` holds no such program and
    `.v4/claim_kinds.json` no such kind. Both surviving callers, `record()` and
    `measure()`, are reached only from `cli.cmd_cover`, so the state this
    paragraph describes as repaired -- these rules living on the CLI path
    alone -- is the state today.

    The rules stay here rather than moving back into `record()`, because that
    is the half of the repair that still holds: one function answers "why does
    this entry account for nothing", and both readers ask it. What changed is
    what asks; what was fixed was two answers to one question.
    """
    quote = (entry.get("quote") or "").strip()
    if not quote:
        return "an entry with no quote accounts for nothing"
    if quote not in own(request):
        if quote in (request or ""):
            return (f"{quote!r} is in the block `--after` carried in from the "
                    f"task this one continues, not in what this task was asked "
                    f"to do. That task accounted for it; this one does not have "
                    f"to say so again.")
        return (f"{quote!r} is not in this task's request. Quote it exactly -- "
                f"copy the span out of `v4 status`. A paraphrase can drift from "
                f"what was asked, which is the whole thing this is guarding.")
    if spans_a_boundary(request, quote):
        pieces = clauses(request)
        return (f"this quote reaches across {len(pieces)} things that were asked "
                f"for separately. One entry per piece, each with the symbol or "
                f"test that got it:\n    "
                + "\n    ".join(repr(c) for c in pieces[:6])
                + ("\n    …" if len(pieces) > 6 else "")
                + "\n  A span that swallows the whole request accounts for it "
                  "the way a receipt for 'goods' accounts for a purchase.")
    if entry.get("not_done"):
        why = (entry.get("why") or "").strip()
        if len(why) < min_chars:
            return (f"deciding not to do part of a request is allowed and is "
                    f"often right, but it is not allowed to be silent. "
                    f"{len(why)} characters, and the floor is {min_chars}.")
    elif not (entry.get("symbol") or entry.get("test")):
        return ("a piece that was delivered names the symbol that delivers it, "
                "a test that reaches it, or both. Naming neither is the silence "
                "this exists to remove.")
    elif (bad := next((u for u in (unresolved(root, entry.get("symbol")),
                                   unresolved(root, entry.get("test"))) if u),
                      None)):
        # Asked once. It was `any(...)` to decide and then the same two calls
        # again to name which -- four file reads for one answer, and two of them
        # only to repeat what the first two already knew.
        return bad
    elif len((entry.get("acceptance") or "").strip()) < ACCEPTANCE_MIN:
        return (f"a piece that was delivered says what would make it done, in "
                f"the requester's terms -- `--acceptance '…'`, at least "
                f"{ACCEPTANCE_MIN} characters. Quoting the request and naming a "
                f"symbol answers 'did you account for it'; nothing here answers "
                f"'is this the thing that was asked for'. Measured: three of "
                f"eight briefs came back short and every one of them would have "
                f"passed this gate.")
    return None


def record(conn, *, task_id, request, quote, symbol=None, test=None,
           not_done=False, why="", acceptance="", min_chars=MIN_CHARS,
           root=None):
    """One piece of the request, and what it got."""
    quote = (quote or "").strip()
    entry = {"quote": quote, "symbol": symbol, "test": test,
             "not_done": not_done, "why": why,
             "acceptance": (acceptance or "").strip()}
    bad = fault(request, entry, min_chars, root)
    if bad:
        raise NotInTheRequest(bad)
    insert(conn, "event", task_id=task_id, claim_id=None, kind="request_cover",
           actor="worker",
           payload={"quote": quote, "symbol": symbol or "", "test": test or "",
                    "not_done": bool(not_done), "why": why,
                    "acceptance": entry["acceptance"]},
           created_at=_now())
    return quote


WITHDRAW_KIND = "request_cover_withdrawn"


def withdraw(conn, *, task_id, quote, why, min_chars=MIN_CHARS):
    """Cancel an earlier entry for `quote`, and say why.

    The ledger is append-only, so a wrong entry cannot be deleted -- and `fault`
    now rejects an entry naming a symbol that is not there, which means one typo
    would hold a task closed forever with no way out but abandoning it. That is
    not integrity, it is a trap: the append-only rule exists so that corrections
    are visible, not so that mistakes are permanent.

    So the correction is itself an append. Both rows stay in the ledger and both
    are readable; only the accounting moves. A withdrawal cancels entries
    recorded *before* it, so re-recording the same quote afterwards works and is
    the normal way to fix one.

    `why` has the same floor as `--not-done`, for the same reason: withdrawing an
    entry silently is the accounting-shaped silence this module exists to remove.
    """
    quote = (quote or "").strip()
    why = (why or "").strip()
    if not quote:
        raise NotInTheRequest("withdrawing needs the quote of the entry to cancel")
    if not any(e.get("quote") == quote for e in entries(conn, task_id)):
        raise NotInTheRequest(
            f"no live entry quotes {quote!r}. `v4 cover --show` lists them.")
    if len(why) < min_chars:
        # Raised, like the two refusals above it. This returned the message as
        # a string, so one function had two error contracts for one class of
        # failure and the caller had to handle both -- `cli.cmd_cover` wraps
        # this in `except NotInTheRequest` and then separately tests `if bad:`
        # on the return value, while `checkers/request_coverage.py`, which
        # reaches the same rules through `measure()`/`fault()`, sees neither.
        # `record()` one function above raises for every case including this
        # identical floor.
        raise NotInTheRequest(_short(len(why), min_chars))
    insert(conn, "event", task_id=task_id, claim_id=None, kind=WITHDRAW_KIND,
           actor="worker", payload={"quote": quote, "why": why},
           created_at=_now())
    return None


def _short(n, floor):
    return (f"withdrawing an entry is allowed and is often right, but it is not "
            f"allowed to be silent. {n} characters, and the floor is {floor}.")


def entries(conn, task_id):
    rows = conn.execute(
        "SELECT kind, payload FROM event WHERE task_id = ? AND kind IN "
        "(?, ?) ORDER BY id", (task_id, "request_cover", WITHDRAW_KIND)).fetchall()
    out = []
    for r in rows:
        try:
            p = json.loads(r["payload"])
        except (ValueError, TypeError):
            continue
        if r["kind"] == WITHDRAW_KIND:
            # Only what came before it. Re-recording the same quote after a
            # withdrawal is how a correction is made, and it has to survive.
            out = [e for e in out if e.get("quote") != p.get("quote")]
        else:
            out.append(p)
    return out


def withdrawals(conn, task_id):
    """Entries that were cancelled, and why.  Read by `v4 cover --show`.

    `entries()` drops them, which is the accounting. This is the other half: a
    correction that nobody can see is the same silence as no correction.
    """
    rows = conn.execute(
        "SELECT payload FROM event WHERE task_id = ? AND kind = ? ORDER BY id",
        (task_id, WITHDRAW_KIND)).fetchall()
    out = []
    for r in rows:
        try:
            out.append(json.loads(r["payload"]))
        except (ValueError, TypeError):
            continue
    return out


def measure(request: str, covered, min_chars=MIN_CHARS, root=None) -> dict:
    """How much of the request has been accounted for, and by what.

    Overlapping quotes count once: the span is what matters, not how many times
    somebody quoted it. Without that, five entries quoting the same clause
    would read as full coverage of a request nobody had finished reading.

    An entry that does not survive `fault` counts for nothing and is reported.
    """
    # `fault` still sees the whole thing, so it can tell "you quoted the carried
    # block" from "you quoted nothing that is here"; everything measured is this
    # task's own request.
    full = request or ""
    request = own(full)
    total = _weight(request)
    if not total:
        return {"total": 0, "accounted": 0, "ratio": 1.0, "delivered": 0,
                "not_done": 0, "missing": "", "faults": []}
    faults = []
    good = []
    for e in covered:
        bad = fault(full, e, min_chars, root)
        (faults.append({"quote": e.get("quote", ""), "why": bad})
         if bad else good.append(e))
    covered = good
    marked = bytearray(len(request or ""))
    delivered = not_done = 0
    for e in covered:
        q = e.get("quote") or ""
        start = 0
        while True:
            i = (request or "").find(q, start)
            if i < 0 or not q:
                break
            for j in range(i, i + len(q)):
                marked[j] = 1
            start = i + 1
        if e.get("not_done"):
            not_done += 1
        else:
            delivered += 1
    accounted = _weight("".join(
        c for c, m in zip(request or "", marked) if m))
    gaps = []
    run = []
    for c, m in zip(request or "", marked):
        if m:
            if run:
                gaps.append("".join(run))
                run = []
        else:
            run.append(c)
    if run:
        gaps.append("".join(run))
    missing = " … ".join(g.strip() for g in gaps if _weight(g) >= 4)

    # A clause nobody quoted at all. The character ratio alone lets a short one
    # be dropped in silence -- a request whose clauses weigh 20 and 2 reads as
    # 91% with the second never mentioned, and "no way to send a file from disk"
    # is exactly that shape: a small span of the brief, and the whole of what
    # did not arrive. Being deliberately skipped is still available; it is
    # `--not-done` with a reason, which is a sentence somebody can argue with.
    quoted = [e.get("quote") or "" for e in covered]
    unspoken = [c for c in clauses(request)
                if _weight(c) >= 4 and not any(q and q in c for q in quoted)]

    return {"total": total, "accounted": accounted,
            "ratio": accounted / total, "delivered": delivered,
            "not_done": not_done, "missing": missing[:400], "faults": faults,
            "clauses": len(clauses(request)), "unspoken": unspoken}
