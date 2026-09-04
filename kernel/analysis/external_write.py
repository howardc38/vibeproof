"""Pure analysis for the ``external-write`` claim kind.  SPEC.md §2, §3, §10.

``detectors/external_write.py`` and ``checkers/external_write.py`` are thin
argv/exit-code wrappers around this module, so the two can never disagree about
what counts as a defect.  A detector that raises a claim the checker cannot
reproduce is a permanently OPEN claim; one shared rule is what prevents it.

**No I/O here.**  No ``open``, no ``print``, no ``sys.exit``, no argv.  Callers
hand in source text plus a :class:`kernel.facts.Facts` table and get findings.

--------------------------------------------------------------------------------
The two questions
--------------------------------------------------------------------------------

An *outbound write* is a call that changes the world outside this process.  The
set of them is not hard-coded here: it is ``facts.outbound_write``, loaded and
validated by ``kernel/facts.py`` (see ``docs/FACTS.md``).  After one, two things
can be wrong, and they are separate ``variant`` values because the fix for each
is a different edit:

``readback``  nothing in this code can tell whether the write landed.
``replay``    running the same operation twice produces the side effect twice.

--------------------------------------------------------------------------------
Why write and read are two lists, and why this module never merges them
--------------------------------------------------------------------------------

A ``readback`` claim is answered by *adding a read-back*.  If the read-back were
itself classified as a write, answering the claim would mint a new claim of the
same kind, forever -- SPEC.md §4 convergence condition ②.  ``kernel/facts.py``
already refuses a table where a write pattern matches a read pattern
(``_check_write_read_disjoint``), and this module adds the second half of the
property:

  :func:`_unobserved_writes` suppresses **every** write in a scope that contains
  any outbound read, not just the write that precedes it.

So one added read-back silences the whole scope, including any bookkeeping write
the fix itself introduces (``self.post_repo.record_receipt(...)``).  Without
that, "persist proof that the write landed" would be a new unobserved write and
the pair would ping-pong.  ``tests/test_external_write.py`` runs the fix and
re-runs the detector to hold this down.

--------------------------------------------------------------------------------
Blind spots -- what a green result does NOT mean
--------------------------------------------------------------------------------

1. **Exception-classification allowlists.**  ``submission_outcome_is_unknown``
   at ``adopter_a f0060ebb^`` decides whether to keep the duplicate-post guard
   by ``isinstance(exc, (Timeout, ConnectionError))``.  The fixed version is the
   same construct with two more classes in the tuple.  No AST distinguishes
   them, so the defect this project's own acceptance file names is a miss, kept
   executable at ``tests/fixtures/external_write/known_miss/``.

2. **Cross-file confirmation.**  The read-back for a write in ``a.py`` may live
   in ``b.py``.  This analysis is single-file, because a claim's subject is one
   file's bytes and a checker that reached further would answer a question the
   staleness key does not cover.

3. **Local durable writes.**  ``db`` and ``fs`` writes are excluded from
   ``readback`` (see :data:`LOCAL_KINDS`): a filesystem syscall and a
   transactional statement report their own failure in-process.  The question
   "did it land?" is about a network boundary that can answer 2xx and mean
   nothing.  A network filesystem is misfiled by this rule.

4. **Replay-sensitivity is a vocabulary, not a proof.**  :data:`REPLAY_VERBS`
   decides which writes duplicate on a retry.  A create spelled with a verb that
   is not in the table is invisible.

5. **Retry that is not written as a loop.**  A caller-level retry, a supervisor
   restarting the process, or a queue redelivery all replay the write with
   nothing in this file to see.  ``replay`` only fires where the file itself
   shows the write being re-executed or shows a key that will not survive one.
"""

from __future__ import annotations

import re

import ast
from dataclasses import dataclass

# The grammar, not the loader: this module states it does no I/O, and
# `kernel.facts` opens files and runs git. Same functions, right layer.
from . import pysource
from . import facts_grammar as facts_mod
from .subject_files import PROTECTED_DEFAULT

# ==============================================================================
# RISK REGISTRY -- edit these tables to narrow or widen the rule.
# Nothing below this block hard-codes a repo symbol; the call-site vocabulary
# lives in `.v4/facts.<repo>.json` and arrives as a `Facts` argument.
# ==============================================================================

#: Write kinds whose failure is reported in-process, so "did it land?" has an
#: answer without a read-back.  A ``write_text`` that fails raises; a POST that
#: times out may have applied.  Excluding these is the difference between 97 and
#: 386 candidate sites on ``adopter_a``, and the 289 dropped are all local.
LOCAL_KINDS: frozenset[str] = frozenset({"db", "fs"})

#: Verbs whose repetition is a second side effect.  A ``put``/``update``/
#: ``delete`` replayed leaves the same end state; a ``post``/``insert``/``send``
#: replayed leaves two.  Matched as *words* of the pattern, so
#: ``.send_media_group`` and ``\bINSERT\s+INTO\b`` both resolve.
REPLAY_VERBS: frozenset[str] = frozenset({
    "post", "publish", "send", "sendmail", "upload", "create", "insert",
    "generate", "submit", "charge", "refund", "adjust", "append", "enqueue",
    "dispatch", "notify", "mutation", "add", "executemany", "cp",
})

#: Argument / variable names that carry a client-supplied dedupe identity.
#: Matched as words, so ``idempotencyKey``, ``idempotency_key`` and
#: ``Idempotency-Key`` all resolve.
IDEMPOTENCY_WORDS: frozenset[str] = frozenset({
    "idempotency", "idempotence", "idempotent", "dedupe", "dedup",
    "clientmutationid",
})

#: `nonce` was in the set above, and it is the one word here that means the
#: opposite of the rest.
#:
#: An idempotency key must be *the same* on a retry -- that is the entire point,
#: and `SHAPE_FRESH_KEY` below reports one built fresh per call because a retry
#: then sends a different key and the server dedupes nothing. A cryptographic
#: nonce must be *different* every time: a CSRF `state`, an OAuth exchange
#: nonce, a signature nonce. Freshness is the defect in one and the requirement
#: in the other, so one word cannot decide both.
#:
#: Reported from an adopter: `secrets.token_urlsafe` bound to the OAuth state
#: nonce in `meta_oauth.py` came back as a "new-per-call idempotency key" for a
#: code-exchange POST two functions away -- an exchange that is single-use by
#: the vendor's contract and never retried, so there is no replay to key. Not a
#: near miss: every OAuth or webhook-signing module has one of these, so this
#: was a finding the checker would raise on all of them.
#:
#: `kernel/analysis/webhook_replay.py` reached the same conclusion from the
#: other side and wrote it down: a parameter "merely *named* `nonce`" was one of
#: its bypass fixtures, because "a name is not a guard". This module decides
#: entirely by name, which is why it is the one that had to lose the word.
#:
#: Three things still catch what the word was there for. `idempotency_nonce`
#: keeps `idempotency`, so a name that says both is unchanged. A key named for
#: the job -- `idempotency_key`, `dedupe_key`, `Idempotency-Key` -- is matched by
#: the set above and by `IDEMPOTENCY_NAMES`. And `SHAPE_RETRY_NO_KEY` asks a
#: retry loop for a key by structure rather than by vocabulary, which is the
#: half of this rule that never depended on what anything was called.

#: Whole names that mean the same thing but carry no ``idempotency`` word.
IDEMPOTENCY_NAMES: frozenset[str] = frozenset({
    "client_mutation_id", "clientmutationid", "dedupe_key", "dedup_key",
    "idempotency_key", "idempotence_key", "idempotencykey",
    "client_request_id", "request_key", "transaction_key", "operation_key",
})

#: Calls that produce a value that differs on every evaluation.  An idempotency
#: key built from one of these does not survive the retry it exists for --
#: ``adopter_a 317e10a7`` is exactly this: ``"idempotencyKey": uuid.uuid4().hex``
#: on a commerce inventory adjust, so a retry adjusted the stock twice.
FRESH_TAILS: frozenset[str] = frozenset({
    "uuid1", "uuid3", "uuid4", "uuid5",
    "token_hex", "token_urlsafe", "token_bytes", "urandom",
    "random", "randint", "randrange", "choice", "choices", "getrandbits",
    "time", "time_ns", "monotonic", "monotonic_ns", "perf_counter",
    "now", "utcnow", "today", "timestamp",
})

#: Words that make a loop a retry rather than a fan-out.  ``for post in posts``
#: sends N different messages on purpose; ``for attempt in range(3)`` sends the
#: same one again.
RETRY_WORDS: frozenset[str] = frozenset({
    "attempt", "attempts", "retry", "retries", "tries", "backoff", "redeliver",
})

#: Decorators that replay the whole function body.
RETRY_DECORATOR_WORDS: frozenset[str] = frozenset({
    "retry", "retrying", "backoff", "repeat", "reattempt",
})

#: Sleeping inside a loop that writes is the other retry tell.
SLEEP_TAILS: frozenset[str] = frozenset({"sleep", "wait", "pause"})

#: Calls that keep the value handed to them.  An expression statement discards
#: its value, but ``results.append(client.post(...))`` has stored the response
#: even though the statement returns nothing.
RETAINING_TAILS: frozenset[str] = frozenset({
    "append", "add", "extend", "insert", "update", "setdefault", "put",
    "push", "append_row", "appendleft", "set", "store", "record",
})

#: Expressions a read-back may return that a caller cannot distinguish from
#: "the outside world holds nothing".  ``adopter_a f0060ebb^``'s
#: ``_recent_media_ids`` returned ``set()`` on an unreadable body, and the
#: caller diffed against it as if the account were empty.
EMPTY_CALL_TAILS: frozenset[str] = frozenset({
    "set", "frozenset", "dict", "list", "tuple",
})

#: The Go spellings of a value that is different every time it is evaluated.
#:
#: `FRESH_TAILS` above is Python's: `uuid4`, `token_hex`, `utcnow`. Go writes
#: the same three ideas as `uuid.New()`, `rand.Read()` and `time.Now()`, and
#: `new` and `read` are far too common to put in one shared set -- `x.New()` is
#: how Go spells every constructor. So they are matched as whole dotted names
#: rather than as tails, which is the same move `PROPOSE_AUTH` had to make for
#: `hmac.Equal`.
GO_FRESH_CALLS: frozenset[str] = frozenset({
    "uuid.New", "uuid.NewString", "uuid.NewRandom", "uuid.Must",
    "ulid.Make", "ksuid.New", "xid.New", "nanoid.New",
    "rand.Int", "rand.Intn", "rand.Int63", "rand.Read", "rand.Uint64",
    "time.Now", "time.Since",
})

#: What a Go retry sleeps with.  `time.Sleep` is the whole idiom; the rest are
#: the two libraries that wrap it.
GO_SLEEP_CALLS: frozenset[str] = frozenset({
    "time.Sleep", "backoff.Retry", "retry.Do", "clock.Sleep",
})

VARIANT_READBACK = "readback"
VARIANT_REPLAY = "replay"
SHAPE_UNOBSERVED = "unobserved-write"
SHAPE_BLIND_READBACK = "uninformative-readback"
SHAPE_FRESH_KEY = "per-call-idempotency-key"
SHAPE_RETRY_NO_KEY = "retry-without-idempotency-key"

MODULE_SYMBOL = "<module>"


@dataclass(frozen=True, order=True)
class Finding:
    """One defect.  Ordered so output is deterministic."""

    path: str          # repo-relative
    line: int          # display only -- never part of a claim's identity
    col: int
    symbol: str        # innermost enclosing def/class, or "<module>"
    variant: str       # VARIANT_READBACK | VARIANT_REPLAY
    shape: str         # SHAPE_*
    trigger: str       # the outbound-write pattern that put this site in scope
    trigger_line: int
    detail: str        # the sentence a human reads in the checker's stdout


@dataclass(frozen=True, order=True)
class Claim:
    """A finding collapsed to its claim identity: (file, symbol, variant)."""

    path: str
    symbol: str
    variant: str
    line: int
    findings: tuple[Finding, ...]


# ==============================================================================
# Small helpers
# ==============================================================================

def _words(identifier: str) -> set[str]:
    """``send_media_group`` -> {send, media, group}; ``idempotencyKey`` -> {idempotency, key}."""
    out: list[str] = []
    for chunk in identifier.replace("-", "_").replace(".", "_").split("_"):
        cur = ""
        for ch in chunk:
            if ch.isupper() and cur and not cur[-1].isupper():
                out.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            out.append(cur)
    return {w.lower() for w in out if w}


def _pattern_words(pattern: str) -> set[str]:
    """Words of a facts pattern, symbol or regex.

    ``\\bINSERT\\s+INTO\\b`` has to yield ``insert``: the verb of a DB write
    lives in a SQL literal, so the pattern that finds it is a regex and the
    replay question still has to be answerable about it.
    """
    letters = []
    cur = ""
    for ch in pattern:
        if ch.isalpha() or ch == "_":
            cur += ch
        else:
            if cur:
                letters.append(cur)
            cur = ""
    if cur:
        letters.append(cur)
    out: set[str] = set()
    for chunk in letters:
        out |= _words(chunk)
    # Regex metacharacter letters are not verbs.
    return out - {"s", "b", "w", "d"}


def dotted_name(node: ast.AST) -> str:
    """``self.session.post`` -> "self.session.post"; unresolvable heads -> "?"."""
    parts, cur = pysource.attribute_chain(node)
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    else:
        parts.append("?")
    return ".".join(reversed(parts))


def is_replay_sensitive(entry, source_line: str = "") -> bool:
    """Does replaying this write leave a second side effect?

    A ``symbol`` pattern names the verb, so the pattern decides.  A ``regex``
    pattern may not: ``adopter_a``' caller-layer DB row is one alternation over
    forty verbs, and reading *its* words would make ``record_heartbeat`` a
    create.  For those the matched source line is the evidence.
    """
    if entry.match == "regex":
        return bool(_pattern_words(source_line) & REPLAY_VERBS)
    return bool(_pattern_words(entry.pattern) & REPLAY_VERBS)


def _is_idempotency_name(name: str) -> bool:
    low = name.lower().replace("-", "_")
    if low in IDEMPOTENCY_NAMES:
        return True
    return bool(_words(name) & IDEMPOTENCY_WORDS)


def _is_fresh_expr(node: ast.AST) -> bool:
    """Does evaluating this expression twice give two different values?"""
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            tail = dotted_name(child.func).split(".")[-1]
            if tail in FRESH_TAILS:
                return True
    return False


def _is_empty_value(node: ast.expr | None) -> bool:
    """``None`` / ``False`` / ``0`` / ``""`` / ``[]`` / ``set()`` and friends."""
    if node is None:
        return True
    if isinstance(node, ast.Constant):
        return node.value is None or node.value is False or node.value in (0, "", b"")
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    if isinstance(node, ast.Call) and not node.args and not node.keywords:
        return dotted_name(node.func).split(".")[-1] in EMPTY_CALL_TAILS
    return False


# ==============================================================================
# Source index -- lines, scopes, statements, parents
# ==============================================================================

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)
_DEFS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


class _Index:
    """Everything the rules need to ask about one parsed file."""

    def __init__(self, tree: ast.Module, source: str) -> None:
        self.tree = tree
        self.end = max((getattr(n, "end_lineno", 0) or 0) for n in ast.walk(tree)) or 1
        self.parent: dict[int, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                self.parent[id(child)] = node

        self.defs = [n for n in ast.walk(tree) if isinstance(n, _DEFS)]
        self.funcs = [n for n in self.defs if isinstance(n, _FUNCS)]
        self.stmts = [n for n in ast.walk(tree) if isinstance(n, ast.stmt)]

    # -- geometry ---------------------------------------------------------
    @staticmethod
    def _span(node) -> tuple[int, int]:
        return node.lineno, getattr(node, "end_lineno", None) or node.lineno

    def _innermost(self, nodes, line: int):
        best = None
        best_size = None
        for node in nodes:
            start, end = self._span(node)
            if start <= line <= end:
                size = end - start
                if best_size is None or size < best_size:
                    best, best_size = node, size
        return best

    def symbol_at(self, line: int) -> str:
        node = self._innermost(self.defs, line)
        return node.name if node is not None else MODULE_SYMBOL

    def scope_at(self, line: int):
        """The function whose body contains ``line``; ``None`` means module level."""
        return self._innermost(self.funcs, line)

    def scope_span(self, scope) -> tuple[int, int]:
        return self._span(scope) if scope is not None else (1, self.end)

    def stmt_at(self, line: int):
        return self._innermost(self.stmts, line)

    def ancestors(self, node):
        cur = node
        while id(cur) in self.parent:
            cur = self.parent[id(cur)]
            yield cur

    def in_handler(self, node) -> bool:
        return any(isinstance(a, ast.ExceptHandler) for a in self.ancestors(node))

    def enclosing_loop(self, node, scope):
        stop = scope if scope is not None else self.tree
        for anc in self.ancestors(node):
            if anc is stop:
                return None
            if isinstance(anc, (ast.For, ast.AsyncFor, ast.While)):
                return anc
        return None

    def nodes_in(self, span, types):
        lo, hi = span
        return [
            n for n in ast.walk(self.tree)
            if isinstance(n, types) and lo <= n.lineno <= hi
        ]


def _hit_lines(source: str, entries) -> dict[int, list]:
    """``{lineno: [Entry, ...]}`` for one facts list.

    The matching is ``kernel.facts.scan_source`` -- the same code path the facts
    file is verified with, so a pattern can never mean one thing to the table's
    own tooling and another thing here.
    """
    by_pattern = {e.pattern: e for e in entries}
    out: dict[int, list] = {}
    for pattern, linenos in facts_mod.scan_source(source, entries).items():
        for lineno in linenos:
            out.setdefault(lineno, []).append(by_pattern[pattern])
    return out


# ==============================================================================
# readback
# ==============================================================================

def _top_expression(stmt):
    """The expression a statement evaluates, with ``await`` stripped."""
    value = getattr(stmt, "value", None)
    while isinstance(value, ast.Await):
        value = value.value
    return value


def _outcome_is_observed(index: _Index, stmt, scope) -> bool:
    """Does anything in this file keep what the write returned?

    An expression statement throws its value away -- including the value of
    anything nested inside it -- unless the outermost call *stores* it:
    ``staged.append(self.gcs_adapter.upload_file(path))`` keeps the upload's
    result as a list element, while
    ``await asyncio.to_thread(self.client.send_message, ...)`` keeps nothing.
    Reading only the statement's type gets the first one wrong, and it was this
    rule's single biggest false positive on real code
    (``core/workers/dispatch.py:415``).

    ``resp = client.post(...)`` counts only if ``resp`` is read afterwards; an
    assignment nobody reads is the same as dropping the value, and it is the
    form a discard takes once a linter has complained about the bare call.
    """
    if stmt is None:
        return True
    if isinstance(stmt, ast.Expr):
        value = _top_expression(stmt)
        if isinstance(value, ast.Call):
            return dotted_name(value.func).split(".")[-1] in RETAINING_TAILS
        return False
    if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
        targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
        if any(not isinstance(t, ast.Name) for t in targets):
            return True                       # stored on an object -> kept
        names = {t.id for t in targets if isinstance(t, ast.Name)}
        if names == {"_"}:
            return False
        span = index.scope_span(scope)
        loaded = {
            n.id for n in index.nodes_in(span, ast.Name)
            if isinstance(n.ctx, ast.Load)
        }
        return bool(names & loaded)
    return True


def _unobserved_writes(index: _Index, path: str, writes, reads, lines) -> list[Finding]:
    """Replay-sensitive writes whose outcome nothing in this file examines.

    Restricted to replay-sensitive writes on purpose.  "Did it land?" only has a
    consequence when the answer decides whether to run the write again, and
    re-running a ``delete`` or an overwrite costs nothing -- so an unconfirmed
    ``keyring.delete_password`` is not a defect, while an unconfirmed ``publish``
    is the whole duplicate-post problem.  Without this the rule's output on
    ``adopter_a`` was 70% deletes, which is the fastest way to teach people to
    ignore a gate.
    """
    findings: list[Finding] = []
    for line in sorted(writes):
        text = lines[line - 1] if 0 < line <= len(lines) else ""
        entries = [
            e for e in writes[line]
            if e.kind not in LOCAL_KINDS and is_replay_sensitive(e, text)
        ]
        if not entries:
            continue
        scope = index.scope_at(line)
        span = index.scope_span(scope)
        if any(span[0] <= r <= span[1] and
               any(e.kind not in LOCAL_KINDS for e in reads[r])
               for r in reads):
            # This scope already observes the outside world.  Suppressing every
            # write in it -- not only the ones before the read -- is what makes
            # answering a claim of this kind terminate.
            #
            # `e.kind not in LOCAL_KINDS`, on this side too. The write side has
            # filtered by it since the beginning, for the reason the docstring
            # above gives -- `fs` and `db` report their own failure in-process
            # and cannot answer "did it land" across a network -- and the read
            # side did not, so one local read silenced every network write in
            # the scope. Measured against `default_table()`:
            # `def publish_it(client, body, p): client.publish(body)` is
            # reported, and adding `Path(p).read_text()` as its first line
            # returns nothing at all. `_blind_readbacks` right below already
            # filters its reads the same way; the two halves of one module
            # disagreed about what counts as observing the outside world.
            continue
        stmt = index.stmt_at(line)
        if _outcome_is_observed(index, stmt, scope):
            continue
        entry = entries[0]
        where = "this module" if scope is None else f"`{index.symbol_at(line)}`"
        findings.append(Finding(
            path=path,
            line=line,
            col=getattr(stmt, "col_offset", 0),
            symbol=index.symbol_at(line),
            variant=VARIANT_READBACK,
            shape=SHAPE_UNOBSERVED,
            trigger=f"outbound write `{entry.pattern}` [{entry.kind}]",
            trigger_line=line,
            detail=(
                f"the outbound write `{entry.pattern}` at line {line} discards what it "
                f"returned, and {where} calls nothing from the outbound-read table. "
                f"Nothing here can tell a write that landed from one that did not: the "
                f"transport not raising is not the same answer."
            ),
        ))
    return findings


def _blind_readbacks(index: _Index, path: str, reads) -> list[Finding]:
    """A read-back whose failure path is indistinguishable from 'nothing there'.

    ``adopter_a f0060ebb^``::

        payload = self._request_with_retry("GET", f"{ig}/{edge}", ...)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            return set()          # <- an unreadable list is not an empty account

    The caller diffed a later listing against that ``set()`` and concluded the
    account held nothing, which is how an interrupted publish became a second
    publish.  The fixed version raises.
    """
    findings: list[Finding] = []
    seen: set[tuple[str, int]] = set()
    for line in sorted(reads):
        entries = [e for e in reads[line] if e.kind not in LOCAL_KINDS]
        if not entries:
            continue
        scope = index.scope_at(line)
        if scope is None:
            continue
        span = index.scope_span(scope)
        returns = index.nodes_in(span, ast.Return)
        empties = [r for r in returns if _is_empty_value(r.value)]
        if not empties or len(empties) == len(returns):
            # Every return empty means the function reports through something
            # other than its return value; no return at all means the same.
            continue
        for ret in empties:
            if not (index.in_handler(ret) or _guarded_by_shape_test(index, ret, scope)):
                continue
            key = (path, ret.lineno)
            if key in seen:
                continue
            seen.add(key)
            entry = entries[0]
            findings.append(Finding(
                path=path,
                line=ret.lineno,
                col=ret.col_offset,
                symbol=index.symbol_at(ret.lineno),
                variant=VARIANT_READBACK,
                shape=SHAPE_BLIND_READBACK,
                trigger=f"outbound read `{entry.pattern}` [{entry.kind}]",
                trigger_line=line,
                detail=(
                    f"`{index.symbol_at(ret.lineno)}` reads the outside world at line "
                    f"{line} and returns an empty value at line {ret.lineno} on the path "
                    f"where the read could not be used. A caller cannot tell that from "
                    f"'the outside world holds nothing', so a write this read-back was "
                    f"meant to confirm reads as never having landed."
                ),
            ))
    return findings


def _guarded_by_shape_test(index: _Index, node, scope) -> bool:
    """Is this ``return`` on the branch of a validity test rather than a lookup?

    ``if not isinstance(data, list): return set()`` is a shape test -- the read
    came back unusable.  ``if row is None: return None`` inside a lookup is not
    a defect, so a plain identity test on a name is not enough on its own; the
    test has to be about the *shape* of what came back.
    """
    stop = scope if scope is not None else index.tree
    for anc in index.ancestors(node):
        if anc is stop:
            return False
        if isinstance(anc, ast.If):
            for sub in ast.walk(anc.test):
                if isinstance(sub, ast.Call) and dotted_name(sub.func).split(".")[-1] in {
                    "isinstance", "issubclass", "hasattr", "callable",
                }:
                    return True
    return False


# ==============================================================================
# replay
# ==============================================================================

def _scope_key_bindings(index: _Index, span):
    """``[(line, name, value_node)]`` for every idempotency-key-shaped binding."""
    out = []
    for node in index.nodes_in(span, (ast.Assign, ast.AnnAssign, ast.keyword, ast.Dict)):
        if isinstance(node, ast.keyword):
            if node.arg and _is_idempotency_name(node.arg):
                out.append((node.value.lineno, node.arg, node.value))
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and isinstance(key.value, str)
                        and _is_idempotency_name(key.value)):
                    out.append((value.lineno, key.value, value))
        else:
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                name = (target.id if isinstance(target, ast.Name)
                        else target.attr if isinstance(target, ast.Attribute) else "")
                if name and _is_idempotency_name(name) and node.value is not None:
                    out.append((node.value.lineno, name, node.value))
    return sorted(out)


def _resolves_to_fresh(index: _Index, span, value) -> bool:
    """Fresh directly, or a local name bound to something fresh in this scope."""
    if _is_fresh_expr(value):
        return True
    if isinstance(value, ast.Name):
        for node in index.nodes_in(span, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == value.id:
                    if _is_fresh_expr(node.value):
                        return True
    return False


def _is_retry_loop(index: _Index, loop) -> bool:
    text_nodes = [loop.target, loop.iter] if isinstance(loop, (ast.For, ast.AsyncFor)) else [loop.test]
    words: set[str] = set()
    for node in text_nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                words |= _words(sub.id)
            elif isinstance(sub, ast.Attribute):
                words |= _words(sub.attr)
    if words & RETRY_WORDS:
        return True
    for sub in ast.walk(loop):
        if isinstance(sub, ast.Call) and dotted_name(sub.func).split(".")[-1] in SLEEP_TAILS:
            return True
    return False


def _is_retry_decorated(scope) -> bool:
    if scope is None:
        return False
    for dec in getattr(scope, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        if _words(dotted_name(target)) & RETRY_DECORATOR_WORDS:
            return True
    return False


def _replay_findings(index: _Index, path: str, writes, lines) -> list[Finding]:
    findings: list[Finding] = []
    scopes_done: set[tuple[int, str]] = set()

    sensitive = [
        (line, e)
        for line in sorted(writes)
        for e in writes[line]
        if is_replay_sensitive(e, lines[line - 1] if 0 < line <= len(lines) else "")
    ]

    # -- a key that does not survive the retry it exists for -------------------
    # File-scoped, not scope-scoped.  A key is generated in the handler and spent
    # by a transport helper one call away (``adopter_a 317e10a7^`` builds
    # ``variables`` in ``adjust_inventory_quantity`` and POSTs in
    # ``_execute_query``), and a freshly-generated idempotency key is wrong
    # wherever the write is: surviving a replay is the only thing it is for.
    if sensitive:
        write_line, write_entry = sensitive[0]
        for key_line, name, value in _scope_key_bindings(index, (1, index.end)):
            scope = index.scope_at(key_line)
            if not _resolves_to_fresh(index, index.scope_span(scope), value):
                continue
            mark = (key_line, SHAPE_FRESH_KEY)
            if mark in scopes_done:
                continue
            scopes_done.add(mark)
            findings.append(Finding(
                path=path,
                line=key_line,
                col=0,
                symbol=index.symbol_at(key_line),
                variant=VARIANT_REPLAY,
                shape=SHAPE_FRESH_KEY,
                trigger=f"outbound write `{write_entry.pattern}` [{write_entry.kind}]",
                trigger_line=write_line,
                detail=(
                    f"`{name}` at line {key_line} is generated fresh on every call, in a "
                    f"file whose outbound write `{write_entry.pattern}` (line {write_line}) "
                    f"duplicates on replay. A retry sends a different key, so the server's "
                    f"dedupe cannot match it and the effect applies twice -- the key has to "
                    f"be derived from the operation, not from the clock or a random source."
                ),
            ))

    # -- a write the file itself re-executes, with nothing to dedupe on --------
    for line, entry in sensitive:
        scope = index.scope_at(line)
        span = index.scope_span(scope)
        symbol = index.symbol_at(line)
        if _scope_key_bindings(index, span):
            continue
        loop = index.enclosing_loop(index.stmt_at(line) or index.tree, scope)
        replayed_by = None
        if loop is not None and _is_retry_loop(index, loop):
            replayed_by = f"the retry loop at line {loop.lineno}"
        elif _is_retry_decorated(scope):
            replayed_by = f"the retry decorator on `{symbol}`"
        if replayed_by is None:
            continue
        mark = (line, SHAPE_RETRY_NO_KEY)
        if mark in scopes_done:
            continue
        scopes_done.add(mark)
        findings.append(Finding(
            path=path,
            line=line,
            col=0,
            symbol=symbol,
            variant=VARIANT_REPLAY,
            shape=SHAPE_RETRY_NO_KEY,
            trigger=f"outbound write `{entry.pattern}` [{entry.kind}]",
            trigger_line=line,
            detail=(
                f"the outbound write `{entry.pattern}` at line {line} is re-executed by "
                f"{replayed_by}, and nothing in `{symbol}` carries a dedupe identity. The "
                f"second execution is a second side effect: the first one may already "
                f"have landed when the response was lost."
            ),
        ))
    return findings


# ==============================================================================
# Public API
# ==============================================================================

def analyse_source(source: str, *, path: str, table) -> list[Finding]:
    """Findings for one file's text.  Raises ``SyntaxError`` if it will not parse."""
    tree = ast.parse(source)
    index = _Index(tree, source)
    writes = _hit_lines(source, table.outbound_write)
    reads = _hit_lines(source, table.outbound_read)
    lines = source.splitlines()
    findings = _unobserved_writes(index, path, writes, reads, lines)
    findings += _blind_readbacks(index, path, reads)
    findings += _replay_findings(index, path, writes, lines)
    return sorted(findings)


def to_claims(findings) -> list[Claim]:
    """Collapse findings to one claim per ``(file, symbol, variant)``.

    Deliberately lossy: a function with four unconfirmed sends is one question
    to answer, not four.  The findings ride along so the checker can still name
    every line.
    """
    buckets: dict[tuple[str, str, str], list[Finding]] = {}
    for f in findings:
        buckets.setdefault((f.path, f.symbol, f.variant), []).append(f)
    return sorted(
        Claim(path=p, symbol=s, variant=v, line=min(f.line for f in g),
              findings=tuple(sorted(g)))
        for (p, s, v), g in buckets.items()
    )


# ==============================================================================
# The table this rule reads when a repo ships no facts file
# ==============================================================================

#: A generic, repo-agnostic outbound vocabulary.  ``kernel/register.py`` runs a
#: checker's fixtures **without** ``--facts``, and SPEC.md §10 requires the
#: checker to run in any adopter repo, so there has to be a table to fall back
#: to.  It is built through :func:`kernel.facts.build` rather than assembled by
#: hand, so it passes the same validator -- including the write/read
#: disjointness rule that keeps this claim kind from triggering itself.
_DEFAULT_TABLE = {
    "repo": "<default>",
    "generated_from_commit": "0" * 40,
    "outbound_write": [
        {"pattern": p, "kind": k, "seen_at": "<builtin>:1", "match": m}
        for p, k, m in [
            # TypeScript, and the reason two of these are regex rather than
            # symbol is the language: `fetch(url)` is a GET and
            # `fetch(url, { method: 'POST' })` is a write, and one call name
            # covers both. A symbol row would either report every read as a
            # write or miss every write. Measured before these rows existed:
            # the whole default table was Python client names, so a TypeScript
            # file matched nothing and the rule answered about nothing.
            (r"""fetch\s*\([^)]*method\s*:\s*['"](?:POST|PUT|PATCH|DELETE)""",
             "http", "regex"),
            ("axios.post", "http", "symbol"),
            ("axios.put", "http", "symbol"),
            ("axios.patch", "http", "symbol"),
            ("axios.delete", "http", "symbol"),
            ("requests.post", "http", "symbol"),
            ("requests.put", "http", "symbol"),
            ("requests.patch", "http", "symbol"),
            ("requests.delete", "http", "symbol"),
            ("httpx.post", "http", "symbol"),
            ("httpx.put", "http", "symbol"),
            ("httpx.patch", "http", "symbol"),
            ("httpx.delete", "http", "symbol"),
            (".client.post", "http", "symbol"),
            (".session.post", "http", "symbol"),
            # Go's `net/http` idiom, and with the receiver for the reason the
            # two rows above have it: a bare `.Post` matches any domain type
            # with a `Post()` method. The table already carried `.SendMessage`
            # and `.Publish` capitalised, so Go was half here -- measured, a
            # bypass fixture calling `client.Post` matched nothing in the
            # shipped table while the fixture set's own table caught it, which
            # is a gate that only closes for repos that have already written
            # their facts.
            (".client.Post", "http", "symbol"),
            (".client.Do", "http", "symbol"),
            (".client.Put", "http", "symbol"),
            (".client.Patch", "http", "symbol"),
            (".send_message", "http", "symbol"),
            (".send_photo", "http", "symbol"),
            (".send_document", "http", "symbol"),
            (".publish", "http", "symbol"),
            (".upload_file", "http", "symbol"),
            (".chat.completions.create", "http", "symbol"),
            (".responses.create", "http", "symbol"),
            (".images.generate", "http", "symbol"),
            (".models.generate_content", "http", "symbol"),
            ("keyring.set_password", "secret", "symbol"),
            (".write_text", "fs", "symbol"),
            (".write_bytes", "fs", "symbol"),
            (".executemany", "db", "symbol"),
            (r"\bINSERT\s+INTO\b", "db", "regex"),
            (r"\bUPDATE\s+[\w\".]+\s+SET\b", "db", "regex"),
            (r"\bDELETE\s+FROM\b", "db", "regex"),
            # The same transports, spelled the way Go spells them. A fallback
            # table that is repo-agnostic in one language only is a table that
            # answers "no outbound write here" for every repo in the other,
            # which is the silent pass this rule exists to stop.
            ("http.Post", "http", "symbol"),
            ("http.PostForm", "http", "symbol"),
            ("http.Put", "http", "symbol"),
            (".Client.Post", "http", "symbol"),
            (".SendMessage", "http", "symbol"),
            (".SendPhoto", "http", "symbol"),
            (".SendDocument", "http", "symbol"),
            (".Publish", "http", "symbol"),
            (".UploadFile", "http", "symbol"),
            ("os.WriteFile", "fs", "symbol"),
        ]
    ],
    "outbound_read": [
        {"pattern": p, "kind": k, "seen_at": "<builtin>:1", "match": m}
        for p, k, m in [
            # The other half of the same fork: a `fetch` with no method is the
            # GET that reads the outside world back, and a scope that has one
            # is a scope that observes.
            (r"""fetch\s*\((?:(?!method\s*:)[^)])*\)""", "http", "regex"),
            ("axios.get", "http", "symbol"),
            ("axios.head", "http", "symbol"),
            # The Go half of the same pair.
            (".client.Get", "http", "symbol"),
            (".client.Head", "http", "symbol"),
            ("requests.get", "http", "symbol"),
            ("requests.head", "http", "symbol"),
            ("httpx.get", "http", "symbol"),
            ("httpx.head", "http", "symbol"),
            (".client.get", "http", "symbol"),
            (".session.get", "http", "symbol"),
            (".getresponse", "http", "symbol"),
            (".get_updates", "http", "symbol"),
            (".get_file", "http", "symbol"),
            (".responses.retrieve", "http", "symbol"),
            (".files.get", "http", "symbol"),
            ("keyring.get_password", "secret", "symbol"),
            (".read_text", "fs", "symbol"),
            (".read_bytes", "fs", "symbol"),
            (r"\bSELECT\b", "db", "regex"),
            ("http.Get", "http", "symbol"),
            ("http.Head", "http", "symbol"),
            (".Client.Get", "http", "symbol"),
            ("os.ReadFile", "fs", "symbol"),
        ]
    ],
    "auth_decision": [
        {"pattern": "check_permission", "kind": "authz", "seen_at": "<builtin>:1"},
    ],
    "entrypoint_globs": ["**/main.py"],
    "ui_globs": ["**/ui/**"],
    "config_files": ["pyproject.toml"],
    "protected_paths": list(PROTECTED_DEFAULT),
}


def default_table():
    """The built-in table, validated by ``kernel.facts`` on every call."""
    return facts_mod.build(_DEFAULT_TABLE, source="<builtin default table>")


def table_from(obj) -> object:
    """``Facts`` from a parsed facts file, or the built-in table when there is none.

    Never degrades to an empty table: a malformed file raises ``FactsError``
    from ``kernel.facts``, because a detector scanning with no patterns reports
    a clean repo.
    """
    if not obj:
        return default_table()
    return facts_mod.build(obj, source="--facts")


# ==============================================================================
# The same three questions, asked of Go
# ==============================================================================
#
# Go answers two of them better than Python and one of them worse.
#
# Better: a write's outcome is a returned `error`, so "does anything observe
# it" is a question about names rather than about statement types -- there is
# no `staged.append(upload(...))` shape to get wrong. And the blind read-back
# has a construct of its own: `if err != nil { return nil, nil }` hands the
# caller an empty value *and* a nil error, which is the exact defect
# `_blind_readbacks` was written for, spelled where a reader can see it.
#
# Worse: an idempotency key handed straight into a call --
# `Post(url, Header{"Idempotency-Key": uuid.New()})` -- is not a binding, and
# only bindings are read here. Python's `_scope_key_bindings` reads keyword
# arguments and dict literals as well. Stated rather than papered over: the Go
# half finds a key that was named, not one that was passed inline.


def _go_span(shape, fn: str):
    for f in shape.get("funcs") or []:
        if f.get("name") == fn:
            return f.get("line", 0), f.get("end", 0) or f.get("line", 0)
    return None


def _go_scope_at(shape, line: int) -> str:
    """The function whose body contains this line, innermost first."""
    best, best_size = "", None
    for f in shape.get("funcs") or []:
        lo, hi = f.get("line", 0), f.get("end", 0) or f.get("line", 0)
        if lo <= line <= hi and (best_size is None or hi - lo < best_size):
            best, best_size = f.get("name") or "", hi - lo
    return best


def _go_targets_at(shape, line: int) -> list:
    """The names a statement on this line assigns to, or [] for a bare call."""
    out = []
    for a in shape.get("assigns") or []:
        if a.get("line") == line:
            out.extend(a.get("targets") or [])
    return out


def _go_name_read_elsewhere(shape, name: str, span, line: int) -> bool:
    """Is this name read, as opposed to only bound.

    The emitter records an identifier wherever it appears, the assignment's own
    left-hand side included, which is what `ctx=Load` filters out on the Python
    side. Counting is what replaces it: if the binding line mentions the name
    more often than it assigns it, one of those mentions is a read.

    That distinction is not pedantry here. `if _, err := client.Post(…);
    err == nil` is the ordinary Go way to write a checked call, and the whole
    check happens on the binding line -- a rule that only looked at *other*
    lines would report every one of them.

    `_` is never read: it is the name Go has for discarding.
    """
    if name == "_":
        return False
    lo, hi = span
    bound = sum(1 for a in shape.get("assigns") or []
                if a.get("line") == line
                for t in a.get("targets") or [] if t == name)
    from .webhook_replay import _go_alive
    dead = shape.get("dead_after") or {}
    # `_ = resp` is not a read. It is Go's way of saying "I am ignoring this",
    # written because the compiler refuses an unused local -- so the shape the
    # Python fixture calls "bound but never read" can only be spelled this way
    # here, and counting it as observation would make the rule unfalsifiable in
    # Go. Only the exact form: `_ = resp.Body.Close()` does something.
    discarded = {a.get("line") for a in shape.get("assigns") or []
                 if (a.get("targets") or []) == ["_"]
                 and (a.get("names") or []) == [name]}
    here = 0
    for r in shape.get("refs") or []:
        if r.get("name") != name:
            continue
        at = r.get("line", 0)
        if not (lo <= at <= hi) or not _go_alive(r, dead) or at in discarded:
            # A check written below the function's own `return` compiles and
            # never runs. Counting it as observation would make the bypass the
            # fix: move the `if err != nil` down one line and the finding goes
            # away while the write stays unconfirmed.
            continue
        if at != line:
            return True
        here += 1
    return here > bound


#: A returned value a caller cannot tell from "the outside world holds nothing".
_GO_EMPTY_VALUES = frozenset({"nil", '""', "``", "0", "false", "[]", "{}"})


def _go_is_empty_return(values) -> bool:
    """`return`, `return nil`, `return nil, nil`, `return []string{}, nil`.

    Every value it hands back is a zero. One non-zero value means the caller
    was told something, and this rule is about the ones that were not.
    """
    if not values:
        return True                       # a bare `return` in a named-result fn
    for v in values:
        text = str(v).strip()
        if text in _GO_EMPTY_VALUES:
            continue
        if text.endswith("{}"):
            continue                      # `[]string{}`, `map[string]int{}`, `T{}`
        return False
    return True


def _go_calls_in(shape, span, fn: str = None) -> list:
    lo, hi = span
    return [c for c in shape.get("calls") or []
            if lo <= c.get("line", 0) <= hi
            and (fn is None or c.get("fn") == fn)]


def go_analyse_source(source: str, *, path: str, table) -> list:
    """`analyse_source`, for Go.  Same variants, same shapes, same table."""
    from . import gosource
    shape = gosource.shape_source(source)
    if shape is None:
        raise SyntaxError("go/parser refused this file")
    lines = source.splitlines()
    writes = _go_hit_lines(source, table.outbound_write)
    reads = _go_hit_lines(source, table.outbound_read)
    findings = _go_unobserved(shape, path, writes, reads, lines)
    findings += _go_blind_readbacks(shape, path, reads)
    findings += _go_replay(shape, path, writes, lines)
    return sorted(findings)


def _go_hit_lines(source: str, entries) -> dict:
    by_pattern = {e.pattern: e for e in entries}
    out: dict = {}
    for pattern, linenos in facts_mod.scan_source(source, entries,
                                                  lang="go").items():
        for lineno in linenos:
            out.setdefault(lineno, []).append(by_pattern[pattern])
    return out


def _go_unobserved(shape, path, writes, reads, lines) -> list:
    findings = []
    for line in sorted(writes):
        text = lines[line - 1] if 0 < line <= len(lines) else ""
        entries = [e for e in writes[line]
                   if e.kind not in LOCAL_KINDS and is_replay_sensitive(e, text)]
        if not entries:
            continue
        fn = _go_scope_at(shape, line)
        span = _go_span(shape, fn) or (1, max(lines and [len(lines)] or [1]))
        if any(span[0] <= r <= span[1] and
               any(e.kind not in LOCAL_KINDS for e in reads[r])
               for r in reads):
            # The scope already observes the outside world. Suppressing every
            # write in it -- not only the ones before the read -- is what makes
            # answering a claim of this kind terminate, exactly as on the
            # Python side. SPEC.md §4 convergence condition (2).
            continue
        targets = _go_targets_at(shape, line)
        if any(_go_name_read_elsewhere(shape, t, span, line) for t in targets):
            continue
        entry = entries[0]
        where = f"`{fn}`" if fn else "this file"
        findings.append(Finding(
            path=path, line=line, col=0, symbol=fn or MODULE_SYMBOL,
            variant=VARIANT_READBACK, shape=SHAPE_UNOBSERVED,
            trigger=f"outbound write `{entry.pattern}` [{entry.kind}]",
            trigger_line=line,
            detail=(
                f"the outbound write at line {line} returns an error and "
                f"nothing in {where} keeps it, so this code cannot tell a "
                f"delivered write from a dropped one. Replaying `{entry.pattern}` "
                f"leaves a second effect, which is what makes the difference "
                f"matter."),
        ))
    return findings


def _go_blind_readbacks(shape, path, reads) -> list:
    """`return nil, nil` from inside an error handler, after an outbound read.

    Go hands the caller two values, so this defect has a construct: the empty
    value and the nil error travel together, and the caller reads "there was
    nothing there" from a call that failed. `fail-closed` does not see it --
    the handler *does* return, which is the question that rule asks.
    """
    findings, seen = [], set()
    for line in sorted(reads):
        entries = [e for e in reads[line] if e.kind not in LOCAL_KINDS]
        if not entries:
            continue
        fn = _go_scope_at(shape, line)
        span = _go_span(shape, fn)
        if not fn or span is None:
            continue
        returns = [r for r in shape.get("returns") or [] if r.get("fn") == fn]
        empties = [r for r in returns if _go_is_empty_return(r.get("values"))]
        if not empties or len(empties) == len(returns):
            # Every return empty means the function reports through something
            # other than its return value; no return at all means the same.
            continue
        handlers = [(e.get("line", 0), e.get("end", 0))
                    for e in shape.get("errs") or [] if e.get("fn") == fn]
        for ret in empties:
            at = ret.get("line", 0)
            if not any(lo <= at <= hi for lo, hi in handlers):
                continue
            if (path, at) in seen:
                continue
            seen.add((path, at))
            entry = entries[0]
            findings.append(Finding(
                path=path, line=at, col=0, symbol=fn,
                variant=VARIANT_READBACK, shape=SHAPE_BLIND_READBACK,
                trigger=f"outbound read `{entry.pattern}` [{entry.kind}]",
                trigger_line=line,
                detail=(
                    f"line {at} answers a failed read with an empty value and "
                    f"no error, so the caller cannot tell it from a successful "
                    f"read of nothing. `{entry.pattern}` at line {line} is the "
                    f"read; return the error instead."),
            ))
    return findings


def _go_replay(shape, path, writes, lines) -> list:
    findings, done = [], set()
    sensitive = [(line, e) for line in sorted(writes) for e in writes[line]
                 if is_replay_sensitive(
                     e, lines[line - 1] if 0 < line <= len(lines) else "")]
    if not sensitive:
        return findings
    write_line, write_entry = sensitive[0]

    # -- a key built where it is handed over, never bound to a name ------------
    #
    # Python's `_scope_key_bindings` reads keyword arguments and dict literals
    # as well as assignments, and the Go half read only assignments. Go has no
    # keyword argument, so the one shape missing here is the composite literal:
    # `Post(url, Body{IdempotencyKey: uuid.New().String()})` builds the key at
    # the call and binds nothing, which is the same defect with no name to
    # notice it by -- and `317e10a7` in the reference adopter is that exact
    # line, spelled in Python.
    #
    # The freshness has to be on the field's own line. `IdempotencyKey: key`
    # names a value bound elsewhere, and the loop below already reports that at
    # its binding -- so requiring the call here is what keeps one defect from
    # being two findings.
    for f in shape.get("fields") or []:
        key = f.get("key") or ""
        at = f.get("line", 0)
        if not _is_idempotency_name(key):
            continue
        if not any(c.get("name") in GO_FRESH_CALLS
                   for c in _go_calls_in(shape, (at, at))):
            continue
        if (at, SHAPE_FRESH_KEY) in done:
            continue
        done.add((at, SHAPE_FRESH_KEY))
        findings.append(Finding(
            path=path, line=at, col=0,
            symbol=_go_scope_at(shape, at) or MODULE_SYMBOL,
            variant=VARIANT_REPLAY, shape=SHAPE_FRESH_KEY,
            trigger=f"outbound write `{write_entry.pattern}` [{write_entry.kind}]",
            trigger_line=write_line,
            detail=(
                f"`{key}` at line {at} is built where it is handed over, fresh "
                f"on every call, in a file whose outbound write "
                f"`{write_entry.pattern}` (line {write_line}) duplicates on "
                f"replay. A retry sends a different key, so the server's dedupe "
                f"cannot match it and the effect applies twice -- the key has "
                f"to be derived from the operation, not from the clock or a "
                f"random source."),
        ))

    # -- a key that does not survive the retry it exists for -------------------
    for a in shape.get("assigns") or []:
        at = a.get("line", 0)
        named = [t for t in a.get("targets") or [] if _is_idempotency_name(t)]
        if not named:
            continue
        if not any(c.get("name") in GO_FRESH_CALLS
                   for c in _go_calls_in(shape, (at, at))):
            continue
        if (at, SHAPE_FRESH_KEY) in done:
            continue
        done.add((at, SHAPE_FRESH_KEY))
        name = named[0]
        findings.append(Finding(
            path=path, line=at, col=0, symbol=_go_scope_at(shape, at) or MODULE_SYMBOL,
            variant=VARIANT_REPLAY, shape=SHAPE_FRESH_KEY,
            trigger=f"outbound write `{write_entry.pattern}` [{write_entry.kind}]",
            trigger_line=write_line,
            detail=(
                f"`{name}` at line {at} is generated fresh on every call, in a "
                f"file whose outbound write `{write_entry.pattern}` (line "
                f"{write_line}) duplicates on replay. A retry sends a different "
                f"key, so the server's dedupe cannot match it and the effect "
                f"applies twice -- the key has to be derived from the "
                f"operation, not from the clock or a random source."),
        ))

    # -- a write the file itself re-executes, with nothing to dedupe on --------
    for line, entry in sensitive:
        fn = _go_scope_at(shape, line)
        span = _go_span(shape, fn)
        if span is None:
            continue
        if any(_is_idempotency_name(t) for a in shape.get("assigns") or []
               if span[0] <= a.get("line", 0) <= span[1]
               for t in a.get("targets") or []):
            continue
        loop = next((l for l in shape.get("loops") or []
                     if l.get("fn") == fn
                     and l.get("line", 0) <= line <= l.get("end", 0)), None)
        if loop is None or not _go_is_retry_loop(shape, loop):
            continue
        if (line, SHAPE_RETRY_NO_KEY) in done:
            continue
        done.add((line, SHAPE_RETRY_NO_KEY))
        findings.append(Finding(
            path=path, line=line, col=0, symbol=fn or MODULE_SYMBOL,
            variant=VARIANT_REPLAY, shape=SHAPE_RETRY_NO_KEY,
            trigger=f"outbound write `{entry.pattern}` [{entry.kind}]",
            trigger_line=line,
            detail=(
                f"the retry loop at line {loop.get('line')} runs `{entry.pattern}` "
                f"again, and nothing in `{fn}` carries an identity the server "
                f"can dedupe on. The second attempt is a second effect."),
        ))
    return findings


def _go_is_retry_loop(shape, loop) -> bool:
    """A retry rather than a fan-out.

    Go has one loop keyword, so `for _, post := range posts` and
    `for attempt := 0; attempt < 3; attempt++` are the same construct. Two
    pieces of evidence tell them apart, and they are the two the Python half
    reads: what the header names, and whether the body sleeps.
    """
    lo, hi = loop.get("line", 0), loop.get("end", 0)
    for r in shape.get("refs") or []:
        if r.get("line") == lo and _words(r.get("name") or "") & RETRY_WORDS:
            return True
    return any(c.get("name") in GO_SLEEP_CALLS
               for c in _go_calls_in(shape, (lo, hi)))


# ── TypeScript and JavaScript ────────────────────────────────────────────────

_TS_FN_START = re.compile(
    r"(?<![\w.$])(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)?\s*\("
    r"|^[ \t]*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*"
    r"(?::[^=\n]+)?=\s*(?:async\s*)?\(", re.M)


#: What may stand between a parameter list and the body it belongs to: an arrow,
#: a return type, whitespace. Not a brace, not a semicolon, not another
#: parameter list -- any of those and this function has no braced body, which is
#: what a concise arrow (`const f = (a) => a + 1`) is, and the next brace in the
#: file belongs to something else entirely.
_TS_BODY_GAP = re.compile(r"[^{};()]*")


def _ts_scopes(source: str, mask: str):
    """`[(name, start_line, end_line)]` for every function body in the file.

    Brace matching, not a parser. What it is for is the same thing
    `_go_span` is for: a write and a readback belong together only when they
    are in the same scope, and a file-wide search would let a readback at the
    bottom excuse a write at the top of an unrelated function.

    The parameter list is skipped by matching its parentheses, not by looking
    for the next `{`. `_TS_FN_START` ends on the opening paren, and
    `mask.find("{", m.end() - 1)` then found the first brace *inside* the
    parameters -- so `function send({ id, body })` reported a body one line
    long, the write in the real body fell outside every scope, and
    `ts_analyse_source` fell back to the file-wide span this function exists to
    prevent. A destructured parameter and an object default are the two
    commonest shapes in the code this rule is pointed at, and both have that
    brace.
    """
    from .test_expectation import _ts_balanced
    out = []
    for m in _TS_FN_START.finditer(mask):
        name = m.group(1) or m.group(2) or "<anonymous>"
        params_end = _ts_balanced(mask, m.end() - 1, "(", ")")
        if params_end is None:
            continue
        brace = mask.find("{", params_end)
        if brace == -1:
            continue
        if not _TS_BODY_GAP.fullmatch(mask[params_end:brace]):
            continue
        end = _ts_balanced(mask, brace, "{", "}")
        if end is None:
            continue
        out.append((name,
                    mask.count("\n", 0, brace) + 1,
                    mask.count("\n", 0, end) + 1))
    return out


def ts_analyse_source(source: str, *, path: str, table) -> list:
    """`analyse_source`, for TypeScript.  Same table, same variant and shape.

    One rule of the three, and the omission is stated rather than left to be
    discovered. **Unobserved write**: an outbound write in a scope where
    nothing reads the outside world back, and whose own return value is
    discarded. That is the shape the Python and Go halves report as
    `SHAPE_UNOBSERVED`, and the table that decides what counts as a write or a
    read is the shared one -- a repo widening its facts widens all three.

    Not here, and each for a reason rather than an oversight:

    * `SHAPE_BLIND_READBACK` -- a readback that cannot inform. Deciding that
      needs to know what the read's result is compared against, which is type
      and dataflow, not brace matching.
    * `SHAPE_FRESH_KEY` and `SHAPE_RETRY_NO_KEY` -- whether an idempotency key
      survives a replay. Same reason: it turns on where the key came from.

    Both gaps are narrower than they sound: the unobserved write is the shape
    that fired in the eval, and a claim this does raise is answered by the same
    checker with the same words in all three languages.
    """
    from . import symbols
    from .test_expectation import _ts_mask
    mask = _ts_mask(source)
    lines = source.splitlines()
    writes = _ts_hit_lines(source, table.outbound_write)
    reads = _ts_hit_lines(source, table.outbound_read)
    scopes = _ts_scopes(source, mask)

    findings = []
    for line in sorted(writes):
        text = lines[line - 1] if 0 < line <= len(lines) else ""
        entries = [e for e in writes[line]
                   if e.kind not in LOCAL_KINDS and is_replay_sensitive(e, text)]
        if not entries:
            continue
        span = next(((a, b) for _n, a, b in scopes if a <= line <= b),
                    (1, len(lines) or 1))
        name = next((n for n, a, b in scopes if a <= line <= b), "")
        if any(span[0] <= r <= span[1] and
               any(e.kind not in LOCAL_KINDS for e in reads[r])
               for r in reads):
            continue                  # this scope already reads the world back
        # A write whose result is kept is a write somebody can test. `const r =
        # await fetch(...)` keeps it; `await fetch(...)` on its own line does
        # not, and that is the site this rule is about.
        stripped = (mask.splitlines()[line - 1] if 0 < line <= len(lines)
                    else "").strip()
        if re.match(r"^(?:const|let|var|return)\b|^[\w$.\[\]]+\s*=[^=]", stripped):
            continue
        entry = entries[0]
        where = f"`{name}`" if name else "this file"
        findings.append(Finding(
            path=path, line=line, col=0, symbol=name or MODULE_SYMBOL,
            variant=VARIANT_READBACK, shape=SHAPE_UNOBSERVED,
            trigger=f"outbound write `{entry.pattern}` [{entry.kind}]",
            trigger_line=line,
            detail=(f"nothing in {where} keeps what this write returned and "
                    f"nothing in it reads the outside world back, so no caller "
                    f"can tell whether the write landed"),
        ))
    return sorted(findings)


def _ts_hit_lines(source: str, entries) -> dict:
    by_pattern = {e.pattern: e for e in entries}
    out: dict = {}
    for pattern, linenos in facts_mod.scan_source(source, entries,
                                                  lang="ts").items():
        for lineno in linenos:
            out.setdefault(lineno, []).append(by_pattern[pattern])
    return out
