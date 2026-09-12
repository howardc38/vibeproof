"""Pure analysis for the ``fail-closed`` claim kind.  SPEC.md §3.

This module is the one place the rule actually lives.  ``detectors/fail_closed.py``
and ``checkers/fail_closed.py`` are thin argv/exit-code wrappers around it, so the
two can never disagree about what counts as a defect -- a detector that raises a
claim the checker cannot reproduce is a permanently OPEN claim, and that is the
failure mode this shared module exists to prevent.

**No argv, no exit codes, no output.**  No ``print``, no ``sys.exit``, nothing
read off the command line.  Callers hand in source text and get back findings.

**It does read two files, and saying otherwise was the false half.**  This
paragraph said "No I/O here.  No ``open``…" while ``_TABLE`` reads the shipped
``fail_closed.json`` beside this module at import time and :func:`vocabulary_for`
reads the adopter's ``.v4/fail_closed.json`` off disk -- both deliberately, both
the whole point of the vocabulary being data rather than 156 literals in a
``kernel/`` file nobody downstream can edit.  SPEC §12 states the general form:
of the 22 modules in this layer 10 read files and 3 run git, the sentence has
never been true anywhere it was written, and nothing verifies it -- the layer
checker compares import edges and cannot see I/O.  The contract this layer
really holds is *the judgement lives here; argv and exit codes do not*, and
that is what the two lines above now say.

--------------------------------------------------------------------------------
The rule
--------------------------------------------------------------------------------

A ``try`` (or a ``contextlib.suppress`` block) is a *fail-open site* when BOTH:

  (a) the guarded body performs a **risky operation** -- an outbound effect
      (HTTP / SDK client / DB write / file write / message send) or an
      **identity or permission decision** (auth / token / signature / secret); and

  (b) control can **leave the whole try/except without an exception having
      propagated**, so execution continues as if the operation had succeeded.

(b) is an escape-path question, not a keyword search.  "The handler body contains
no ``raise``" is a different and wrong question -- see :func:`exit_kind` for the
real code that proves it wrong.

Everything else is out of scope on purpose.  A bare ``except`` around a
``json.loads`` of a config default is not a safety defect, and a gate that fires
on it is a gate people learn to ignore.  See ``RISK REGISTRY`` below: (a) is
entirely table-driven so narrowing or widening the rule is a table edit, not a
code edit.

--------------------------------------------------------------------------------
Why the claim is identified by symbol, not by line
--------------------------------------------------------------------------------

``Finding`` carries a line for humans.  ``Claim`` does not: claims are deduplicated
to one per ``(file, symbol, variant)``.  Line numbers shift when an unrelated edit
lands above them, and a claim keyed by line would be re-derived as a *new,
unanswered* claim on every such shift.  Symbols move with their code.

--------------------------------------------------------------------------------
Blind spots -- what a green result does NOT mean
--------------------------------------------------------------------------------

Measured against ``adopter_a`` (385 non-test files, 784 handlers, 31 claims).
Each of these is a fail-open defect this rule structurally cannot report, so
"``fail-closed`` answered" must never be read as "this change cannot fail open":

1. **Exception-classification allowlists.**  A predicate over exception *types*
   whose default answer is the unsafe one, read by a handler that does re-raise.
   This is the ``adopter_a`` ``f0060ebb^`` duplicate-post defect, and it is
   reproduced and asserted-missed in
   ``tests/fixtures/fail_closed/known_miss/exception_allowlist_predicate.py``.
   Nothing in an AST says which answer is safe.  The fix is a separate claim kind,
   not a wider rule here.

2. **Destructive work done *before* re-raising.**  ``exit_kind`` only asks whether
   the exception propagates.  A handler that deletes the evidence and then raises
   scores green.  Same root cause as (1).

3. **Callers that ignore an error return.**  ``reports_failure_to_caller.py`` is
   green because the failure leaves as a value.  Whether any caller reads that
   value is a cross-file question this single-file analysis never asks.

4. **Swallows inside a handler that raises.**  ``visit_ExceptHandler`` seals those
   so ``secure_store.py``'s rollback loop scores green; a genuinely dropped side
   effect in the same position is sealed with it.

5. **Anything the registry does not name.**  An outbound effect reached through a
   variable (``getattr(client, verb)(...)``), a locally-named helper outside
   ``OUTBOUND_PREFIXES``, a DB write through an ORM method not in the tables.
   Coverage here is a vocabulary, not a proof.

6. **Other languages.** Go and JS/TS have narrower extractors below; a missing
   extractor/toolchain is unverified, not Python-equivalent semantic coverage.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path

from . import pysource
from . import tables

# ==============================================================================
# RISK REGISTRY -- the vocabulary this rule reads, in one block, so a name is a
# row rather than a branch. Nothing below hard-codes a name.
#
# **An adopter edits `.v4/fail_closed.json`, not this file.**
# `kernel/` is shared, not copied -- SPEC §8.8 -- so for as long as the 156
# literals lived here, "add a row rather than a branch" was an instruction only
# this repo could follow. They are data now, in `fail_closed.json` beside this
# module, and `vocabulary_for(root)` unions the adopter's own on top: a repo
# that names three transports of its own keeps the ten it did not have to think
# about, the way `protected_paths` and the secret families are both decided.
#
# Some tables are deliberately not reachable that way -- `TERMINATING_*`,
# `FALSEY_CONSTANTS`, the predicate affixes. Those are facts about Python, and
# a repo redeclaring what `sys.exit` does is not widening a rule, it is
# switching one off.
# ==============================================================================

#: The vocabulary, as data.
#:
#: It was 156 literals in this file, and this file lives in `kernel/` -- shared,
#: never copied (SPEC §8.8) -- so "add a row rather than a branch" was an
#: instruction only this repo could follow. `secret_patterns.py` made the same
#: move for the same reason and states it: "adding them meant editing the
#: kernel. A regex table is data."
#:
#: Two tables, one rule. The shipped one is beside this module; an adopter's
#: own is `.v4/fail_closed.json` and is *unioned* in, never
#: substituted -- a repo that names three transports of its own must not
#: thereby lose the ten it did not have to think about. `protected_paths` and
#: the secret families are both decided that way.
#:
#: What is not here on purpose: `TERMINATING_*`, `FALSEY_CONSTANTS`, the exit
#: kinds. Those are facts about Python, not about a repo's vocabulary, and an
#: adopter redefining what `sys.exit` does is not extending a rule -- it is
#: turning one off.
_TABLE = json.loads(
    tables.shipped(__file__).read_text(encoding="utf-8"))["vocabulary"]

#: The order `_matches_outbound` reads them in is the order they are declared
#: here, and every one of them is a field of `Vocabulary` below.
_TUPLES = ("outbound_roots", "receiver_hints", "path_hints",
           "outbound_prefixes", "auth_deny_roots")


def _validate_reporting_calls(value):
    if value is not None and not isinstance(value, (list, tuple, set, frozenset)):
        raise ValueError("failure_reporting_calls needs an array of exact call targets")


@dataclass(frozen=True)
class Vocabulary:
    """What counts as an outbound call, a path, or an auth decision here."""
    outbound_roots: tuple
    http_verb_tails: frozenset
    outbound_tails_strong: frozenset
    fs_mutation_tails: frozenset
    outbound_tails_weak: frozenset
    receiver_hints: tuple
    path_hints: tuple
    outbound_prefixes: tuple
    auth_words: frozenset
    auth_deny_tails: frozenset
    auth_deny_roots: tuple
    #: The words a value uses to say the thing did not happen. Vocabulary, not
    #: a fact about Python: a repo may spell it `degraded`, `partial`,
    #: `unavailable` -- and `unreadable`, which is what this framework's own
    #: hooks return when they cannot read the ledger. `degraded` was the one
    #: that made a handler reporting a partial outcome read as a swallow.
    failure_values: frozenset
    #: Exact JS/TS assignment targets the repo has verified as visible failure
    #: channels (e.g. its own UI error state). Empty unless the adopter declares
    #: them; a DOM property by itself is not evidence of error reporting.
    failure_reporting_assignments: frozenset = frozenset()
    #: Exact custom JS/TS reporting calls, verified by the adopter's behavior
    #: tests. No project-specific helper is silently a global reporting word.
    failure_reporting_calls: frozenset = frozenset()

    def __post_init__(self):
        _validate_reporting_calls(self.failure_reporting_calls)
        for target in self.failure_reporting_assignments:
            if not isinstance(target, str) or not re.fullmatch(
                    r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)+", target):
                raise ValueError("failure_reporting_assignments needs exact dotted targets, "
                                 "not patterns or expressions")
        for target in self.failure_reporting_calls:
            if not isinstance(target, str) or not re.fullmatch(
                    r"[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*", target):
                raise ValueError("failure_reporting_calls needs exact bare or dotted call targets, not patterns or expressions")

    @classmethod
    def of(cls, rows: dict) -> "Vocabulary":
        _validate_reporting_calls(rows.get('failure_reporting_calls'))
        return cls(**{name: (tuple(rows.get(name) or ())
                             if name in _TUPLES
                             else frozenset(rows.get(name) or ()))
                      for name in cls.__dataclass_fields__})

    def union(self, rows: dict) -> "Vocabulary":
        """This table plus an adopter's own.  Never a replacement."""
        _validate_reporting_calls(rows.get('failure_reporting_calls'))
        merged = {}
        for name in self.__dataclass_fields__:
            mine = getattr(self, name)
            theirs = rows.get(name) or []
            merged[name] = (tuple(mine) + tuple(t for t in theirs if t not in mine)
                            if name in _TUPLES else frozenset(mine) | frozenset(theirs))
        return Vocabulary(**merged)


#: The shipped table.  Every rule below defaults to it.
SHIPPED = Vocabulary.of(_TABLE)

#: Kept as module names, and the reason written here was not the reason.
#:
#: It said `checkers/facts_coverage.py` imports two of them by name and this
#: docstring cites four. That checker is not in this repo -- SPEC records
#: facts-coverage as removed on 2026-08-24 -- and the docstring cites exactly
#: one, `OUTBOUND_PREFIXES`. Counted across `kernel/`, `checkers/`,
#: `detectors/`, `tests/`, `docs/` and `.claude/`: `OUTBOUND_TAILS_WEAK` and
#: `AUTH_DENY_ROOTS` appear nowhere but this file, and the rest are reached only
#: by `test_a_repo_can_widen_the_rule_it_is_judged_by.py`, which asserts the
#: names resolve to the table.
#:
#: They stay, and this is the reason that holds: they are the vocabulary this
#: module is *about*, and a table whose rows have no names is one a reader has
#: to reconstruct from `_TABLE` every time. The values come from one place --
#: `SHIPPED` -- so the names cannot drift from the data; what they can do is go
#: unused, and two have.
OUTBOUND_ROOTS = SHIPPED.outbound_roots
HTTP_VERB_TAILS = SHIPPED.http_verb_tails
OUTBOUND_TAILS_STRONG = SHIPPED.outbound_tails_strong
FS_MUTATION_TAILS = SHIPPED.fs_mutation_tails
OUTBOUND_TAILS_WEAK = SHIPPED.outbound_tails_weak
RECEIVER_HINTS = SHIPPED.receiver_hints
PATH_HINTS = SHIPPED.path_hints
OUTBOUND_PREFIXES = SHIPPED.outbound_prefixes
AUTH_WORDS = SHIPPED.auth_words
AUTH_DENY_TAILS = SHIPPED.auth_deny_tails
AUTH_DENY_ROOTS = SHIPPED.auth_deny_roots

#: Where an adopter puts its own rows: the shipped table's name, under `.v4/`.
#: One name on both sides, so `hashing.program_sha` can find this file by the
#: same rule this module finds it by -- see `analysis/tables.py`. It was
#: `.v4/fail_closed_vocabulary.json`, which nothing structural could have
#: guessed, and so a repo could widen the rule it is judged by without expiring
#: a single answer the narrower rule had given.
VOCABULARY_FILE = str(tables.own("", __file__))


def vocabulary_for(root) -> Vocabulary:
    """The table this repo is judged by.  Raises on one it cannot read.

    Unreadable is never "the shipped one": a repo that declared its own
    transports and then broke the file would be judged by a narrower rule than
    it asked for, and nothing would say so. `secret_patterns.table_for` refuses
    the same way and for the same reason.
    """
    own = tables.own(root, __file__)
    if not own.is_file():
        return SHIPPED
    rows = json.loads(own.read_text(encoding="utf-8"))
    return SHIPPED.union(rows.get("vocabulary") or rows)


#: A bare *read* (no call) counts as a permission decision only when it also
#: reads like a predicate -- ``ctx.is_authenticated`` yes, ``settings.token`` no.
PREDICATE_PREFIXES: tuple[str, ...] = ("is_", "has_", "can_", "may_", "should_")
PREDICATE_SUFFIXES: tuple[str, ...] = ("_ok", "_valid", "_allowed", "_permitted", "_granted")

#: Calls that end the process.  A handler reaching one of these fails closed even
#: though it never re-raises.
#:
#: Split in two, because matching the bare tail of any dotted callee made this
#: table say the opposite of what it means. `logging.Logger.fatal` is a stdlib
#: alias for `critical()` -- it writes a line and returns -- so
#: `except Exception: log.fatal("upload failed")` was read as fail-closed while
#: the exception was swallowed. `stats.abort()`, `job.fail()` and `queue.die()`
#: are the same shape, and every one of them is a *bookkeeping* call inside a
#: handler that goes on to return normally. A rule that seals a handler on the
#: strength of a method name is a fail-open in the checker that exists to find
#: fail-opens.
#:
#: A bare `die()` or `fail()` is left in: it is the repo's own helper, called
#: with nothing in front of it, and the convention is unambiguous.
TERMINATING_BARE: frozenset[str] = frozenset({
    "exit", "_exit", "abort", "die", "fail", "fatal", "panic", "quit",
})

#: Dotted calls that really do end the block. Named in full rather than by tail:
#: `sys.exit` raises SystemExit, `self.fail` raises AssertionError, and
#: `log.fatal` does neither.
TERMINATING_DOTTED: frozenset[str] = frozenset({
    "sys.exit", "os._exit", "os.abort", "posix._exit",
    "self.fail", "pytest.fail", "unittest.fail",
})

#: Kept as the union so a caller asking "is this name ever terminating" gets an
#: answer, and so the risk registry still reads as one table.
TERMINATING_TAILS: frozenset[str] = TERMINATING_BARE | frozenset(
    d.split(".")[-1] for d in TERMINATING_DOTTED)

#: Stems -- not spellings -- of the words in a ``return <call>`` that mean the
#: handler handed the caller an explicit failure.  ``return
#: ToolResult.failure(...)`` is fail-closed by any reading: the caller is told,
#: in a value it cannot mistake for success.
#:
#: This was a list of whole words, and a list of whole words has to enumerate
#: every inflection of every concept.  It did not: an adopter renamed a helper
#: and two findings went from FAIL to PASS with no logic changed, because
#: ``failed`` was in the list and ``fail`` was not.  Measured across the rest of
#: it, the same hole was open in three more places -- ``denied`` without
#: ``deny``, ``refuse`` without ``refused``, while ``reject``/``rejected``
#: happened to have both.  Four concepts, seven spellings, and which four of the
#: seven you got decided the verdict.
#:
#: A stem plus ``startswith`` covers the forms nobody thought to type. The price
#: is over-matching a word that merely begins the same way -- ``failsafe`` reads
#: as ``fail`` -- and that is the right side to be wrong on: this test is an
#: exemption from being reported, and a rule that exempts one handler too many
#: costs less than one that hid four.
ERROR_RESULT_STEMS: frozenset[str] = frozenset({
    # `deni`/`deny` is one concept whose forms share no prefix -- `denied` does
    # not start with `deny`. Two entries beats a stemmer nobody can predict.
    "error", "fail", "deni", "deny", "reject", "refus",
})

#: Dict keys that carry a verdict, for the ``return {"ok": False, "error": ...}``
#: idiom.  ``ERROR_KEYS`` mean failure by their presence; ``VERDICT_KEYS`` mean
#: failure only when their value is falsey or a word in ``FAILURE_VALUES``.
ERROR_DICT_KEYS: frozenset[str] = frozenset({"error", "errors", "failure", "exception"})
VERDICT_DICT_KEYS: frozenset[str] = frozenset({"ok", "success", "status", "state", "result"})
#: Kept as a module name for the same reason as the tables above: two checkers
#: cite these by name, and the values now come from one place.
FAILURE_VALUES: frozenset[str] = SHIPPED.failure_values

#: ``with <this>(...)`` swallows exceptions with no ``except`` clause anywhere.
SUPPRESSOR_TAILS: frozenset[str] = frozenset({"suppress"})

#: Expressions a handler may ``return`` that mean "nothing happened" to the
#: caller.  Returning one of these is the ``falsey`` variant.
FALSEY_CONSTANTS: tuple[object, ...] = (False, None, 0, "", b"")

# ==============================================================================
# Findings
# ==============================================================================

VARIANT_SWALLOW = "swallow"   # logs or passes, then carries straight on
VARIANT_FALSEY = "falsey"     # hands the caller False / None / empty

MODULE_SYMBOL = "<module>"


@dataclass(frozen=True, order=True)
class Finding:
    """One fail-open handler.  Ordered so output is deterministic."""

    path: str          # repo-relative
    line: int          # the `except` / `with suppress` line -- display only
    col: int
    symbol: str        # innermost enclosing def/class, or "<module>"
    variant: str       # VARIANT_SWALLOW | VARIANT_FALSEY
    trigger: str       # what made the body risky, e.g. "outbound requests.post"
    trigger_line: int
    handler: str       # human-readable handler shape, e.g. "except Exception"
    detail: str        # the sentence a human reads in the checker's stdout


@dataclass(frozen=True, order=True)
class Claim:
    """A finding collapsed to its claim identity: (file, symbol, variant)."""

    path: str
    symbol: str
    variant: str
    line: int              # smallest line of the findings behind it
    findings: tuple[Finding, ...]


# ==============================================================================
# Name resolution
# ==============================================================================

def dotted_name(node: ast.AST) -> str:
    """``self.session.post`` -> "self.session.post"; unresolvable heads -> "()"."""
    parts, cur = pysource.attribute_chain(node)
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
    elif isinstance(cur, ast.Call):
        parts.append("()")
    elif isinstance(cur, ast.Subscript):
        parts.append("[]")
    else:
        parts.append("?")
    return ".".join(reversed(parts))


def _word_list(identifier: str) -> list[str]:
    """``is_authenticated`` -> [is, authenticated]; ``getUserToken`` -> [get,user,token].

    In order, because two callers need two different things from the same
    split: `_words` asks which words are present and `_normal` asks how the
    name reads when it is spelled the way the tables are.
    """
    out: list[str] = []
    for chunk in identifier.replace("-", "_").split("_"):
        cur = ""
        for ch in chunk:
            if ch.isupper() and cur and not cur[-1].isupper():
                out.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            out.append(cur)
    return [w.lower() for w in out if w]


def _words(identifier: str) -> set[str]:
    """``is_authenticated`` -> {is, authenticated}; ``getUserToken`` -> {get,user,token}."""
    return set(_word_list(identifier))


def _normal(identifier: str) -> str:
    """The name as the tables spell it: lowercase words joined by underscores.

    The tables are vocabulary -- `send_message`, `write_bytes`, `post` -- and
    they were compared against the raw tail, so they only ever matched a name
    written in Python's convention. Go writes the same three as `SendMessage`,
    `WriteBytes` and `Post`, and every one of them classified as nothing: a
    checker whose whole job is to notice an outbound call, reading a language
    where the convention is different, and answering "no outbound call here".

    `_receiver_says` already lowered the receiver for the same reason. This is
    the other half of that, and it costs Python nothing -- a snake_case name
    normalises to itself.
    """
    return "_".join(_word_list(identifier))


def _prefix_match(parts: list[str], roots) -> bool:
    for root in roots:
        rp = root.split(".")
        if parts[: len(rp)] == rp:
            return True
    return False


def _matches_auth(dotted: str, *, is_call: bool, vocab: Vocabulary = None) -> str | None:
    parts = dotted.split(".")
    tail = parts[-1]
    vocab = vocab or SHIPPED
    if tail in vocab.auth_deny_tails or _prefix_match(parts, vocab.auth_deny_roots):
        return None
    if not (_words(tail) & vocab.auth_words):
        # `settings.meta_access_token.strip()` -- the auth word is on the receiver.
        if not any(_words(p) & vocab.auth_words for p in parts[:-1]):
            return None
        if not is_call:
            return None
    if not is_call:
        low = tail.lower()
        predicate = low.startswith(PREDICATE_PREFIXES) or low.endswith(PREDICATE_SUFFIXES)
        if not predicate:
            return None
    return f"auth/permission decision `{dotted}`"


def _receiver_says(receiver: str, hints) -> bool:
    """Does the receiver *name* one of these, rather than merely contain it?

    `receiver` is a dotted name already lowered. It is split on the characters
    a Python name uses to join words, so `self.host.replace` offers `self`,
    `host`; `hostname.replace` offers `hostname`; and `os.replace` offers `os`.
    A hint matches a whole word, or a word that starts or ends with it when the
    hint is long enough to mean something on its own -- `filepath` is a path and
    `cost` is not an `os`.
    """
    words = [w for w in re.split(r"[^a-z0-9]+", receiver) if w]
    for hint in hints:
        for word in words:
            if word == hint:
                return True
            if len(hint) > 3 and (word.startswith(hint) or word.endswith(hint)):
                return True
    return False


def _matches_outbound(dotted: str, vocab: Vocabulary = None) -> str | None:
    vocab = vocab or SHIPPED
    parts = dotted.split(".")
    tail = _normal(parts[-1])
    receiver = ".".join(parts[:-1]).lower()

    if _prefix_match(parts, vocab.outbound_roots) and tail in vocab.http_verb_tails:
        return f"outbound call `{dotted}`"

    if tail in vocab.outbound_tails_strong:
        return f"outbound call `{dotted}`"

    for prefix in vocab.outbound_prefixes:
        if tail.startswith(prefix) and len(tail) > len(prefix):
            return f"outbound call `{dotted}`"

    if tail in vocab.fs_mutation_tails and _receiver_says(receiver, vocab.path_hints):
        return f"filesystem write `{dotted}`"

    if tail in vocab.outbound_tails_weak and _receiver_says(receiver, vocab.receiver_hints):
        return f"outbound call `{dotted}`"

    return None


def classify_call(dotted: str, vocab: Vocabulary = None) -> str | None:
    """Reason string if this callee is risky, else None.  Auth wins ties.

    `vocab` is the table to judge by -- `vocabulary_for(root)` when the caller
    knows which repo, the shipped one otherwise. It is a parameter rather than
    module state for the reason `external_write.analyse_source` gives about
    its own: one rule, one input, no hidden state.
    """
    vocab = vocab or SHIPPED
    return (_matches_auth(dotted, is_call=True, vocab=vocab)
            or _matches_outbound(dotted, vocab))


# ==============================================================================
# Walking, but never into a nested scope
# ==============================================================================

_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _walk_same_scope(nodes):
    """Yield every node under ``nodes`` without descending into a nested def.

    A ``requests.post`` inside a closure defined in the ``try`` body does not run
    inside the ``try`` -- defining a function is not calling it -- so counting it
    would be a false positive.  The nested ``def`` is filtered at the top level
    too, not only among descendants.
    """
    stack = [n for n in nodes if not isinstance(n, _SCOPES)]
    while stack:
        node = stack.pop()
        yield node
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _SCOPES):
                continue
            stack.append(child)


def risky_operations(body, vocab: Vocabulary = None) -> list[tuple[int, str]]:
    """[(line, reason)] for every risky operation the guarded body performs."""
    hits: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for node in _walk_same_scope(body):
        if isinstance(node, ast.Call):
            # `func` is the dispatcher; the callable it dispatches can be an
            # argument. `await asyncio.to_thread(do_the_write, x)` reads as
            # `asyncio.to_thread` and the write is invisible -- measured on the
            # reference adopter, 39 handlers hidden that way. The rule is in
            # `pysource.handed_over`, once: the same blindness was in
            # `route_auth`, and a failure appearing twice means the first
            # repair was made in the wrong place.
            dotted = dotted_name(node.func)
            reason = classify_call(dotted, vocab)
            if reason is None:
                for arg in pysource.handed_over(node):
                    passed = dotted_name(arg)
                    reason = classify_call(passed, vocab)
                    if reason is not None:
                        dotted = passed
                        break
        elif isinstance(node, (ast.Attribute, ast.Name)) and isinstance(
            getattr(node, "ctx", None), ast.Load
        ):
            # A bare read is only ever evidence of an auth decision, never of an
            # outbound effect -- reading `client.post` writes nothing.
            dotted = dotted_name(node)
            reason = _matches_auth(dotted, is_call=False, vocab=vocab)
        else:
            continue
        if reason is None:
            continue
        key = (node.lineno, reason)
        if key in seen:
            continue
        seen.add(key)
        hits.append(key)
    hits.sort()
    return hits


# ==============================================================================
# Handler classification
# ==============================================================================

#: ``except*`` (PEP 654) is a Try for every purpose this module has.
_TRY_STATEMENTS: tuple[type, ...] = (ast.Try,) + (
    (ast.TryStar,) if hasattr(ast, "TryStar") else ()
)

EXIT_RAISE = "raise"    # control can only leave by an exception or process exit
EXIT_RETURN = "return"  # control leaves normally, carrying a value
EXIT_FALL = "fall"      # control may reach the end of the block


def _is_terminating_call(node: ast.expr) -> bool:
    if not isinstance(node, ast.Call):
        return False
    name = dotted_name(node.func)
    if "." not in name:
        return name in TERMINATING_BARE
    return name in TERMINATING_DOTTED


def exit_kind(stmts) -> str:
    """How control leaves this block: ``EXIT_RAISE`` / ``EXIT_RETURN`` / ``EXIT_FALL``.

    **This is the actual question the rule asks.**  "Does the handler body contain
    a ``raise`` statement?" is the wrong one, and gets ``core/config/secure_store.py``
    exactly backwards::

        except Exception as exc:
            for account in reversed(applied):
                try:
                    write_provider_secret(account, snapshots[account])
                except Exception:
                    rollback_failures.append(account)     # no raise in this body
            if rollback_failures:
                raise SecureStoreRollbackError(...) from exc
            raise SecureStoreError(...) from exc          # <- unconditional

    The inner handler contains no ``raise``, yet no control path leaves the outer
    handler without one.  That code fails closed and must score green.  So the
    analysis walks statements in order and asks whether an escape exists, rather
    than searching the subtree for a keyword.

    Reachability-insensitive on purpose in one direction: a ``raise`` guarded by an
    ``if`` with no ``else`` does not settle the block, because the else-path
    escapes.  Statements after a settled statement are unreachable and ignored.
    """
    for stmt in stmts:
        if isinstance(stmt, ast.Raise):
            return EXIT_RAISE
        if isinstance(stmt, ast.Expr) and _is_terminating_call(stmt.value):
            return EXIT_RAISE
        if isinstance(stmt, (ast.Return, ast.Break, ast.Continue)):
            return EXIT_RETURN
        if isinstance(stmt, ast.If) and stmt.orelse:
            taken, other = exit_kind(stmt.body), exit_kind(stmt.orelse)
            if EXIT_FALL not in (taken, other):
                return EXIT_RAISE if taken == other == EXIT_RAISE else EXIT_RETURN
        if isinstance(stmt, (ast.With, ast.AsyncWith)):
            kind = exit_kind(stmt.body)
            if kind != EXIT_FALL:
                return kind
        if isinstance(stmt, _TRY_STATEMENTS):
            # `finally` runs on every path out, so it decides first.
            if exit_kind(stmt.finalbody) == EXIT_RAISE:
                return EXIT_RAISE
            kinds = {exit_kind(stmt.body + stmt.orelse)}
            kinds |= {exit_kind(h.body) for h in stmt.handlers}
            if kinds == {EXIT_RAISE}:
                return EXIT_RAISE
    return EXIT_FALL


def _terminates(body) -> bool:
    """No path leaves ``body`` without an exception or a process exit."""
    return exit_kind(body) == EXIT_RAISE


def _contains_raise(stmts) -> bool:
    """A ``raise`` anywhere in this block, reachable or not.

    Used **only** for ``finally``, where the construct is rare enough that any
    occurrence means the author put it there to force propagation.  ``finally: if
    failed: raise`` has a normal exit -- the success path -- and asking
    :func:`exit_kind` about it would call that code fail-open.
    """
    for node in _walk_same_scope(stmts):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Expr) and _is_terminating_call(node.value):
            return True
    return False


def _is_falsey_expr(node: ast.expr | None) -> bool:
    if node is None:
        return True                                    # bare `return`
    if isinstance(node, ast.Constant):
        return any(
            node.value is c or (type(node.value) is type(c) and node.value == c)
            for c in FALSEY_CONSTANTS
        )
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return not node.elts
    if isinstance(node, ast.Dict):
        return not node.keys
    return False


def _returns_falsey(body) -> bool:
    for node in _walk_same_scope(body):
        if isinstance(node, ast.Return) and _is_falsey_expr(node.value):
            return True
    return False


def _is_failure_value(node: ast.expr, vocab: Vocabulary = None) -> bool:
    values = (vocab or SHIPPED).failure_values
    if isinstance(node, ast.Constant):
        if node.value is False or node.value is None:
            return True
        if isinstance(node.value, str) and (_words(node.value) & values):
            return True
    return False


def _says_no_error(value) -> bool:
    """`error=None`, `error=""`, `error=False`, `errors=[]` -- the key is there
    and it is saying there was none.

    The presence of the key was the whole test, so
    `return {"ok": True, "error": None}` -- a handler reporting *success* --
    was read as an explicit error report and the swallow it sits on was
    exempted. Measured on this module: a function whose try body is
    `requests.post(url, json=body)` is reported with `except Exception: pass`
    and was not with `except Exception: return {"ok": True, "error": None}`.
    """
    if isinstance(value, ast.Constant):
        return value.value in (None, False, "", 0)
    if isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return not value.elts
    if isinstance(value, ast.Dict):
        return not value.keys
    return False


def _verdict_dict_says_failure(node: ast.Dict) -> bool:
    for key, value in zip(node.keys, node.values):
        if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
            continue
        name = key.value.lower()
        if name in ERROR_DICT_KEYS and not _says_no_error(value):
            return True
        if name in VERDICT_DICT_KEYS and _is_failure_value(value):
            return True
    return False


def _explicit_error_value(value) -> bool:
    """A failure result keeps its meaning when carried in another result."""
    if isinstance(value, ast.Call):
        tail = dotted_name(value.func).split(".")[-1]
        return any(w.startswith(s) for w in _words(tail) for s in ERROR_RESULT_STEMS)
    return isinstance(value, ast.Dict) and _verdict_dict_says_failure(value)


def _failure_pair(value) -> bool:
    """A boolean refusal, or absent data accompanied by an explicit error."""
    if not (isinstance(value, ast.Tuple) and len(value.elts) == 2
            and isinstance(value.elts[0], ast.Constant)):
        return False
    payload, error = value.elts
    if payload.value is False:
        return not _says_no_error(error)
    # None alone (or with arbitrary data/text) is ambiguous. A result already
    # recognised as failure outside a tuple remains failure inside one.
    return payload.value is None and _explicit_error_value(error)


def _returns_explicit_error(body) -> bool:
    """The handler handed the caller a value it cannot mistake for success.

    Explicit failure results can use a constructor, dict or boolean verdict pair::

        return ToolResult.failure(...)              # a constructor that says so
        return {"ok": False, "error": str(exc)}     # a verdict dict
        return False, f"Could not store: {exc}"     # an (ok, error) pair
        return None, Result.failure(str(exc))       # a (data, error) pair
        summary["status"] = "failed"; ...; return summary

    This is the single biggest source of false positives on real code, and it is
    also the one place where "the handler does not re-raise" is simply the wrong
    question: the caller *is* told.
    """
    nodes = list(_walk_same_scope(body))
    # Every returning path must report refusal, with no fall-through path. A
    # dead/conditional pair beside a success return must not excuse a swallow.
    if (body and isinstance(body[-1], ast.Return) and _failure_pair(body[-1].value)
            and all(_failure_pair(n.value) for n in nodes if isinstance(n, ast.Return))
            and not any(isinstance(n, (ast.Break, ast.Continue)) for n in nodes)):
        return True

    assigned_failure = False
    for node in nodes:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            if not isinstance(target, ast.Subscript):
                continue
            key = target.slice
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            name = key.value.lower()
            if name in ERROR_DICT_KEYS and not _says_no_error(node.value):
                # Same rule one construct over: `meta["error"] = None` followed
                # by `return cached` was read as reporting a failure, and line
                # 588 below accepts *any* `return <name>` once this is set.
                assigned_failure = True
            elif name in VERDICT_DICT_KEYS and node.value is not None:
                assigned_failure = assigned_failure or _is_failure_value(node.value)

    for node in nodes:
        # `result = {"ok": False, "error": ...}` reports just as much as returning
        # it does; the enclosing function hands `result` back either way.
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            if _verdict_dict_says_failure(node.value):
                return True
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        value = node.value
        if _explicit_error_value(value):
            return True
        if assigned_failure and isinstance(value, (ast.Name, ast.Attribute)):
            return True
    return False


def loops_sealed_by_trailing_raise(tree: ast.AST) -> set:
    """Loops whose block raises once the loop is done -- the retry idiom::

        for attempt in range(3):
            try:
                return upload(path)
            except Exception as exc:
                last_error = exc          # looks like a swallow, in isolation
        raise last_error                  # it is not: the caller still gets it

    Without this the rule reports every bounded-retry loop in a codebase, which
    is the fastest way to teach people to ignore the gate.
    """
    sealed = set()
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for index, stmt in enumerate(block):
                if not isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
                    continue
                if any(isinstance(s, ast.Raise) for s in block[index + 1:]):
                    sealed.add(stmt)
    return sealed


def _falls_through(body) -> bool:
    """Does this handler hand control back to the code after the try?"""
    return not any(isinstance(st, (ast.Return, ast.Raise, ast.Break, ast.Continue))
                   for st in body)


def _blocks_of(stmt) -> list:
    """The statements a compound statement holds, flattened.

    Not `ast.walk`: a nested `def` is a different scope and a `raise` inside it
    does not seal anything here -- which is what `_walk_same_scope` is for, one
    level in.
    """
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return []
    out = []
    for field in ("body", "orelse", "finalbody"):
        block = getattr(stmt, field, None)
        if isinstance(block, list):
            out += block
    for h in getattr(stmt, "handlers", []):
        out += h.body
    return out


def _names_used(nodes) -> set:
    """Every identifier mentioned in these statements.

    Attribute receivers included: `keyring.delete_password(service, account)`
    contributes `keyring`, `service` and `account`, which is what makes a later
    `read_provider_secret(account)` recognisable as being about the same thing.
    """
    out = set()
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                out.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                out.add(sub.attr)
    return out


def trys_sealed_by_a_later_check(tree: ast.AST) -> set:
    """Trys whose swallow is settled by a check further down -- the verify idiom::

        try:
            keyring.delete_password(service, account)
        except PasswordDeleteError:
            pass                          # looks like a swallow, in isolation
        if read_provider_secret(account) is not None:
            raise SecureStoreError(...)   # it is not: the read-back decides

    The retry rule above is the same idea one construct over, and this case was
    missing. Measured on `adopter_a`: `delete_provider_secret` was reported with
    "execution continues as if the operation had succeeded", which the next two
    lines of that function make false. A gate whose message is false about the
    code it points at is worse than one that says nothing.

    Narrow on purpose. The raise has to be reachable from the handler -- nothing
    between them may return -- and only a handler that actually falls through is
    exempted, which is checked per handler at the call site.
    """
    sealed = set()
    try_types = (ast.Try, ast.TryStar) if hasattr(ast, "TryStar") else (ast.Try,)
    for node in ast.walk(tree):
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if not isinstance(block, list):
                continue
            for index, stmt in enumerate(block):
                if not isinstance(stmt, try_types):
                    continue
                swallowed = _names_used(stmt.body)
                for later in block[index + 1:]:
                    if isinstance(later, ast.Return):
                        break            # the handler's path leaves before it
                    # And about the operation that was swallowed. Reachability
                    # was the only narrowing there was, so an ordinary guard
                    # clause -- `if payload is None: raise ValueError(...)`, in
                    # almost every function that takes an argument -- sealed a
                    # `requests.post` swallow two hundred lines above it. The
                    # read-back idiom this exists for has the raise reading what
                    # the write wrote, and that is what a shared name is.
                    if not (_names_used([later]) & swallowed):
                        continue
                    if isinstance(later, ast.Raise):
                        sealed.add(stmt)
                        break
                    # Any compound statement, not `if` alone. The read-back
                    # idiom is written with `if` most often and not only:
                    # `for account in failed: raise`, `with lock(): if bad:
                    # raise`, and `if ok: log() else: raise` were all read as
                    # "nothing seals this", so the handler above them was
                    # reported as failing open when the next lines make that
                    # false -- and a gate whose message is false about the code
                    # it points at is worse than one that says nothing, which
                    # is why this function exists.
                    if _contains_raise(_blocks_of(later)):
                        sealed.add(stmt)
                        break
    return sealed


def _returns_only_exit_codes(fn) -> bool:
    """Is this function's return value an exit code?

    True when every `return` it owns hands back an integer literal -- which is
    what a checker's or a detector's `main` does, because the exit code *is*
    the verdict. Returns in nested functions belong to those functions and are
    not counted.
    """
    if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    seen = 0
    for node in _walk_same_scope(fn.body):
        if not isinstance(node, ast.Return):
            continue
        seen += 1
        v = node.value
        if not (isinstance(v, ast.Constant) and isinstance(v.value, int)
                and not isinstance(v.value, bool)):
            return False
    return seen > 1


def _nonzero_exit_literal(value) -> bool:
    # POSIX exposes the low byte: 256 is an integer but exits successfully.
    # A nonzero low byte also stays nonzero on wider exit-status platforms.
    return (isinstance(value, ast.Constant) and type(value.value) is int
            and value.value % 256 != 0)


def _reports_by_exit_code(body, fn) -> bool:
    """`print(...); return 5` in a program whose answer is its exit code.

    SPEC §3 makes the exit code the whole contract: 0 answered, 1 a finding,
    4 cannot answer, 5 broke. A handler that returns 4 or 5 has told the
    kernel exactly what happened -- `lifecycle` records it, `status` shows it,
    and the claim stays unanswered -- which is the same act as returning
    `{"ok": False}` and was read as a swallow because the value is an int
    rather than a dict. Measured: 14 of this repo's own checkers and detectors
    were reported for the handler that reports their failure.

    Zero is not it. `return 0` from a handler says the checker passed, which
    is the failure this rule is about.
    """
    if not _returns_only_exit_codes(fn):
        return False
    for node in _walk_same_scope(body):
        if isinstance(node, ast.Return) and _nonzero_exit_literal(node.value):
            return True
    return False


def handlers_returning_cli_status(tree) -> set:
    """A handler's literal failure code reaches the CLI's final return.

    This is a bounded value-flow proof, not a rule about variables named code.
    Require a direct, unshadowed __main__ exit call, a simple final assignment
    in the handler and a straight-line tail without rebindings/early exits.
    Loops, enclosing try/with blocks, decorators and nonlocal state stay out.
    """
    definitions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    rebound, attributes, declarations = set(), set(), {}
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            declarations.setdefault(node.name, []).append(node)
            continue
        if isinstance(node, ast.Lambda):
            continue
        pending.extend(ast.iter_child_nodes(node))
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            rebound.add(node.id)
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            attributes.add(dotted_name(node))
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                if isinstance(node, ast.Import) and alias.name == 'sys' and alias.asname in (None, 'sys'):
                    continue
                rebound.add(alias.asname or alias.name.split('.')[0])
        if isinstance(node, ast.ExceptHandler) and node.name:
            rebound.add(node.name)
    bound_exit = 'SystemExit' in rebound or 'SystemExit' in declarations
    imported_sys = any(isinstance(n, ast.Import) and any(
        a.name == 'sys' and a.asname in (None, 'sys') for a in n.names) for n in tree.body)
    entries = set()
    for guard in tree.body:
        if not isinstance(guard, ast.If):
            continue
        test = guard.test
        if not (isinstance(test, ast.Compare) and len(test.ops) == 1
                and isinstance(test.ops[0], ast.Eq) and len(test.comparators) == 1):
            continue
        sides = [test.left, test.comparators[0]]
        if not (any(isinstance(n, ast.Name) and n.id == '__name__' for n in sides)
                and any(isinstance(n, ast.Constant) and n.value == '__main__' for n in sides)):
            continue
        for statement in guard.body:
            call = statement.exc if isinstance(statement, ast.Raise) else statement.value if isinstance(statement, ast.Expr) else None
            if not isinstance(call, ast.Call) or len(call.args) != 1 or call.keywords:
                continue
            target = dotted_name(call.func)
            if not ((isinstance(statement, ast.Raise) and target == 'SystemExit' and not bound_exit)
                    or (isinstance(statement, ast.Expr) and target == 'sys.exit' and imported_sys
                        and 'sys' not in rebound and 'sys' not in declarations and 'sys.exit' not in attributes)):
                continue
            value = call.args[0]
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id not in rebound:
                matches = [fn for fn in definitions if fn.name == value.func.id]
                if len(matches) == 1 and len(declarations.get(value.func.id, [])) == 1 and not matches[0].decorator_list:
                    entries.add(matches[0])

    sealed = set()
    linear = (ast.Assign, ast.AnnAssign, ast.Expr, ast.Pass)

    def walk(block, tail):
        for index, node in enumerate(block):
            after = block[index + 1:] + tail
            if isinstance(node, ast.If):
                walk(node.body, after)
                walk(node.orelse, after)
            if not isinstance(node, ast.Try):
                continue
            following = node.finalbody + after
            for handler in node.handlers:
                if not handler.body or not all(isinstance(s, linear) for s in handler.body):
                    continue
                assignment = handler.body[-1]
                if not (isinstance(assignment, ast.Assign) and len(assignment.targets) == 1
                        and isinstance(assignment.targets[0], ast.Name)
                        and _nonzero_exit_literal(assignment.value)):
                    continue
                name = assignment.targets[0].id
                if not following or not (isinstance(following[-1], ast.Return)
                    and isinstance(following[-1].value, ast.Name) and following[-1].value.id == name):
                    continue
                if not all(isinstance(s, linear) for s in following[:-1]):
                    continue
                if any(isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, (ast.Store, ast.Del))
                       for n in _walk_same_scope(following[:-1])):
                    continue
                if any(isinstance(n, ast.Call) and _is_terminating_call(n)
                       for n in _walk_same_scope(handler.body[:-1] + following[:-1])):
                    continue
                sealed.add(handler)

    for fn in entries:
        if not any(isinstance(n, (ast.Global, ast.Nonlocal, ast.Yield, ast.YieldFrom)) for n in ast.walk(fn)):
            walk(fn.body, [])
    return sealed


def _branches_on_the_failure(handler, fn) -> bool:
    """`unreadable = f"{exc}"` … `if unreadable:` -- the third spelling.

    The handler stores the failure in a name and the function reports
    differently because of it. `v4 doctor` does this where the value has to
    reach a row built further down, and it is the same act as appending: the
    caller is told, in the output this function exists to produce.
    """
    name = getattr(handler, "name", None)
    if not name or not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    held = set()
    for node in ast.walk(handler):
        if isinstance(node, ast.Assign):
            uses_exc = any(isinstance(x, ast.Name) and x.id == name
                           for x in ast.walk(node.value))
            if uses_exc:
                held |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    if not held:
        return False
    for node in ast.walk(fn):
        if not isinstance(node, (ast.If, ast.IfExp, ast.While)):
            continue
        if any(isinstance(x, ast.Name) and x.id in held
               for x in ast.walk(node.test)):
            return True
    return False


def _emits_the_failure(handler) -> bool:
    """Does this handler send the exception somewhere a reader will see it?

    Two spellings, both of which mean the failure left the handler as data:
    `print(f"... {exc}")`, and `out.append(...)` / `.add` / `.extend` carrying
    it -- the second is how `v4 doctor` turns an unreadable ledger into a row
    rather than a silence, which is the whole subject of that command.

    Narrow on purpose. `rows = []`, `return problems`, `return ""` are *not*
    this: they hand back the value a clean run hands back, and the caller
    cannot tell the two apart. Those are the shape this rule is about, and
    three of them in this repo were real -- `doctrine.uptake` read an
    unreadable ledger as "no rule was ever engaged with", and both ledger
    reconcilers read a database error as "no disagreements".

    The exception has to be bound (`except ... as exc`) and has to appear
    inside the emitting call. A handler that prints a constant says something
    happened without saying what, and cannot be told from one that prints on
    every path.
    """
    name = getattr(handler, "name", None)
    if not name:
        return False
    for node in ast.walk(handler):
        if not isinstance(node, ast.Call):
            continue
        fn = dotted_name(node.func)
        tail = fn.split(".")[-1]
        if tail not in ("print", "append", "add", "extend"):
            continue
        for arg in ast.walk(node):
            if isinstance(arg, ast.Name) and arg.id == name:
                return True
    return False


#: Caught here, and the handler has not established what went wrong.  A
#: ``False`` under one of these cannot be read as an answer, because the thing
#: it is an answer to may never have run.
CATCH_ALL: frozenset[str] = frozenset({"Exception", "BaseException"})


def _returns_its_declared_verdict(body, fn, handler) -> bool:
    """``-> bool``, a named exception, and the handler returns ``False``.

    ``_returns_explicit_error`` asks whether the caller was handed something it
    cannot mistake for success, and takes ``return {"ok": False}`` as a yes. A
    function whose whole return type is ``bool`` has said as much in the
    signature: ``False`` is the answer it exists to give, not a value that
    leaked out of a handler.

    Measured by an adopter: five handlers in ``-> bool`` helpers, every one
    returning the verdict its signature promises, every one reported
    ``[falsey]``, all five baselined.

    **The signature is not the whole answer, and this repo's own fixtures are
    where that was measured.** Written as "``-> bool`` and ``return False``",
    this exempted three ``bypass/`` cases at once -- all three
    ``verify_signature(request, secret_store) -> bool`` with ``except
    Exception: return False`` -- and a ``bypass/`` is a shape that must keep
    being reported. It must, and the reason is the whole distinction: a bare
    ``except Exception`` catches "the secret store is unreachable" too, so
    ``False`` there says "the signature did not match" about a check that never
    ran. The caller cannot tell the two apart, which is exactly the hiding this
    rule is about.

    So the exemption needs the handler to have caught something it named.
    ``except ConnectionError: return False`` in ``is_reachable`` establishes
    what happened and answers it; ``except Exception`` establishes nothing.

    Exactly ``bool``. ``Optional[bool]`` and ``bool | None`` have a third value
    to confuse with the second, so the signature stops being the answer.
    """
    ann = getattr(fn, "returns", None)
    if not isinstance(ann, ast.Name) or ann.id != "bool":
        return False
    caught = getattr(handler, "type", None)
    if caught is None:                       # bare `except:`
        return False
    named = (caught.elts if isinstance(caught, ast.Tuple) else [caught])
    if any(dotted_name(c).split(".")[-1] in CATCH_ALL for c in named):
        return False
    for node in _walk_same_scope(body):
        if (isinstance(node, ast.Return)
                and isinstance(node.value, ast.Constant)
                and node.value.value is False):
            return True
    return False


def classify_handler(body, enclosing=None, handler=None) -> str | None:
    """``VARIANT_*`` for a handler that fails open, or None when it fails closed.

    Two ways to fail closed.  The first is structural: no escape path
    (:func:`exit_kind`).  The second is by value: the handler leaves normally but
    hands the caller something it cannot read as success -- a documented,
    separately-tabled exemption, because the caller *is* told and treating those
    as defects buries the real ones.  Measured on ``adopter_a`` HEAD, that one
    exemption is the difference between 41 claims and 30.
    """
    if _terminates(body):
        return None
    if _returns_explicit_error(body):
        return None
    if _returns_its_declared_verdict(body, enclosing, handler):
        return None
    if _reports_by_exit_code(body, enclosing):
        return None
    if handler is not None and (_emits_the_failure(handler)
                                or _branches_on_the_failure(handler, enclosing)):
        return None
    if _returns_falsey(body):
        return VARIANT_FALSEY
    return VARIANT_SWALLOW


def _handler_label(handler: ast.ExceptHandler) -> str:
    if handler.type is None:
        return "bare except"
    return f"except {dotted_name(handler.type) if not isinstance(handler.type, ast.Tuple) else ', '.join(dotted_name(e) for e in handler.type.elts)}"


# ==============================================================================
# The visitor
# ==============================================================================


class _Collector(ast.NodeVisitor):
    def __init__(self, path: str, sealed_loops: set, sealed_trys=(),
                 vocab: Vocabulary = None) -> None:
        self.path = path
        #: The table this file is judged by, carried rather than looked up, so
        #: one scan cannot answer differently from another in the same process.
        self.vocab = vocab or SHIPPED
        self.sealed_loops = sealed_loops
        self.sealed_trys = set(sealed_trys)
        self.scope: list[str] = []
        #: The function a handler is inside, for the rules that need more than
        #: its name -- `_reports_by_exit_code` asks what else it returns.
        self.fns: list = []
        self.findings: list[Finding] = []
        self.cli_status_handlers = set()
        # >0 while inside a region every path leaves by raising or exiting.
        self.sealed = 0

    def visit_Module(self, node):
        self.cli_status_handlers = handlers_returning_cli_status(node)
        self.generic_visit(node)

    def _loop(self, node) -> None:
        sealed = node in self.sealed_loops
        self.sealed += sealed
        self.generic_visit(node)
        self.sealed -= sealed

    visit_For = _loop
    visit_AsyncFor = _loop
    visit_While = _loop

    # -- scope tracking ---------------------------------------------------
    def _enter(self, node) -> None:
        self.scope.append(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.fns.append(node)
            self.generic_visit(node)
            self.fns.pop()
        else:
            self.generic_visit(node)
        self.scope.pop()

    visit_FunctionDef = _enter
    visit_AsyncFunctionDef = _enter
    visit_ClassDef = _enter

    @property
    def enclosing_fn(self):
        return self.fns[-1] if self.fns else None

    @property
    def symbol(self) -> str:
        return self.scope[-1] if self.scope else MODULE_SYMBOL

    # -- sealed regions ---------------------------------------------------
    def visit_ExceptHandler(self, node) -> None:
        """A ``try`` nested inside a handler that re-raises is not fail-open.

        ``core/config/secure_store.py`` is the shape: a rollback loop swallows
        each individual restore failure into a list, then the handler raises
        ``SecureStoreRollbackError`` if the list is non-empty.  The inner
        ``except`` collects, it does not conceal, and flagging it would be
        telling a reviewer to fix code that already fails closed.

        Cost, stated plainly: a genuinely swallowed side effect inside such a
        handler (a best-effort alert that never sends) is invisible to this
        rule.  See the module docstring's blind-spot list.
        """
        sealed = _terminates(node.body)
        self.sealed += sealed
        self.generic_visit(node)
        self.sealed -= sealed

    # -- try/except -------------------------------------------------------
    def visit_Try(self, node) -> None:
        self._try(node)
        # `raise` in `finally` runs on every path out of the try, including the
        # handled one, so it seals the whole statement -- body, handlers and all.
        sealed = _contains_raise(node.finalbody)
        self.sealed += sealed
        self.generic_visit(node)
        self.sealed -= sealed

    visit_TryStar = visit_Try

    def _try(self, node) -> None:
        if self.sealed:
            return
        risks = risky_operations(node.body, self.vocab)
        if not risks:
            return
        if _contains_raise(node.finalbody):
            return
        trigger_line, trigger = risks[0]
        settled_later = node in self.sealed_trys
        for handler in node.handlers:
            if handler in self.cli_status_handlers:
                continue
            variant = classify_handler(handler.body, self.enclosing_fn, handler)
            if variant is None:
                continue
            if settled_later and _falls_through(handler.body):
                continue
            label = _handler_label(handler)
            self.findings.append(
                Finding(
                    path=self.path,
                    line=handler.lineno,
                    col=handler.col_offset,
                    symbol=self.symbol,
                    variant=variant,
                    trigger=trigger,
                    trigger_line=trigger_line,
                    handler=label,
                    detail=(
                        f"{label} guards {trigger} (line {trigger_line}), and control "
                        f"can leave this try/except with no exception having "
                        f"propagated; "
                        + (
                            "it returns a falsey value, so the caller reads a failed "
                            "operation as 'nothing to do'"
                            if variant == VARIANT_FALSEY
                            else "execution continues as if the operation had succeeded"
                        )
                    ),
                )
            )

    # -- contextlib.suppress ----------------------------------------------
    def visit_With(self, node) -> None:
        self._with(node)
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def _with(self, node) -> None:
        if self.sealed:
            return
        suppressed = [
            item
            for item in node.items
            if isinstance(item.context_expr, ast.Call)
            and dotted_name(item.context_expr.func).split(".")[-1] in SUPPRESSOR_TAILS
        ]
        if not suppressed:
            return
        risks = risky_operations(node.body, self.vocab)
        if not risks:
            return
        trigger_line, trigger = risks[0]
        label = f"with {dotted_name(suppressed[0].context_expr.func)}(...)"
        self.findings.append(
            Finding(
                path=self.path,
                line=node.lineno,
                col=node.col_offset,
                symbol=self.symbol,
                variant=VARIANT_SWALLOW,
                trigger=trigger,
                trigger_line=trigger_line,
                handler=label,
                detail=(
                    f"{label} around {trigger} (line {trigger_line}) discards the "
                    "exception with no handler at all; control continues as if the "
                    "operation had succeeded"
                ),
            )
        )


# ==============================================================================
# Public API
# ==============================================================================

def analyse_source(source: str, *, path: str, vocab: Vocabulary = None) -> list[Finding]:
    """Findings for one file's text.  Raises ``SyntaxError`` if it will not parse."""
    tree = ast.parse(source)
    collector = _Collector(path, loops_sealed_by_trailing_raise(tree),
                           trys_sealed_by_a_later_check(tree), vocab)
    collector.visit(tree)
    return sorted(collector.findings)


#: Go's variants.  Named apart from the Python ones because they are different
#: shapes, not translations: Go has no exception to swallow, so there is no
#: `except` to point at and no falsey-return to catch.
VARIANT_GO_EMPTY = "go-empty-handler"
VARIANT_GO_DISCARDED = "go-discarded-error"

#: Calls inside an `if err != nil` body that mean somebody finds out.  Presence
#: is enough -- proving the message reaches an operator is a reviewer's job, and
#: a checker that tried would flag every careful handler.
#:
#: `log.Fatal` and `panic` end the process, which is the strongest form of
#: answering for a failure; `recover` is deliberately absent, because catching
#: a panic and continuing is the swallow this rule is about.
#:
#: Read by `_go_reports` below, one segment of the dotted callee at a time.
GO_REPORTS = ("log", "Log", "print", "Print", "Fatal", "Error", "Errorf",
              "Warn", "Wrap", "Errorf", "report", "Report", "record", "Record",
              "metric", "Metric", "trace", "Trace", "span", "Span", "Notify",
              "notify", "t.Fatal", "t.Error", "Retry", "retry")


#: Calls that end the process. The table above says of `log.Fatal` and `panic`
#: that ending the process is "the strongest form of answering for a failure",
#: and `os.Exit` does exactly that -- harder, in fact, since no deferred
#: function runs and no `recover` can catch it. It was in neither list: not a
#: `return`, `panic` or `break`, and `GO_REPORTS` has no `Exit`.
#:
#: Measured here, on this repo's own Go: `kernel/analysis/_go/shape.go` and
#: `symbols.go` write the error to stderr and exit non-zero at every failure
#: branch, and both were reported as handlers that "report to nobody".
#:
#: Matched on the whole dotted callee, not a segment: `os.Exit` is the call,
#: while a segment test on `Exit` would also seal a handler whose only action
#: is reading an `ExitCode` field.
GO_TERMINATES = ("os.Exit", "syscall.Exit", "runtime.Goexit")


def _go_terminates(callee: str) -> bool:
    """Does this callee end the process rather than fall through?"""
    return callee in GO_TERMINATES


def _go_reports(callee: str) -> bool:
    """Does this callee name mean somebody finds out?

    A *segment* of the dotted name has to begin with one of `GO_REPORTS`.  The
    test was `any(r in c for r in GO_REPORTS)` -- the name containing the word
    anywhere -- and the table above says what it is for, "calls that mean
    somebody finds out", which that is not. Measured: `fmt.Sprintf` contains
    `print` (S-*print*-f) and `catalog.Get` contains `log` (cata-*log*), so an
    `if err != nil` body that only formats a string into a variable was sealed
    as having reported the failure, by this module's own table, in the rule
    that exists to find swallows.

    Beginning rather than equalling, because that is what Go's own convention
    gives: the formatted variant appends a letter (`Printf`, `Errorf`,
    `Warnf`), and a receiver is named for what it does (`logger.Info`,
    `metrics.Inc`, `tracer.Start`). Every one of those is still a report; the
    two measured accidents are not, because no entry starts either segment.
    The dotted entries (`t.Fatal`) are matched against the whole name, which is
    where they were written to be matched.
    """
    for part in (callee, *callee.split(".")):
        if any(part.startswith(r) for r in GO_REPORTS):
            return True
    return False


def go_findings(path: str, shape: dict) -> list[Finding]:
    """Fail-open handlers in one Go file.

    Go has no exceptions, so `try/except` has no equivalent and this is a new
    rule rather than a port. What it asks is the same question the Python half
    asks -- *can control leave here with the failure unanswered* -- of the two
    shapes Go actually has:

      **The empty handler.**  `if err != nil { }`.  The error was checked, and
      then nothing happened.  This is the strongest form: the author wrote the
      test and left the body out, so the failure is known and dropped.

      **The discarded error.**  `_ = err`.  Go has one name for throwing a
      value away and this is it.

    A body that returns, panics, breaks, ends the process (`GO_TERMINATES`) or
    calls anything in `GO_REPORTS` answers for the failure and is not a finding. What is deliberately *not*
    here is `_ = os.WriteFile(...)` -- discarding a call's return rather than a
    named error. Without type information there is no way to tell an error
    return from any other, and `_ = fmt.Fprintf(...)` is idiomatic. That gap is
    a fixture under `known_miss/`, not a sentence.
    """
    out = []
    if not isinstance(shape, dict):
        return out
    for e in shape.get("errs") or []:
        if e.get("returns") or e.get("panics") or e.get("breaks"):
            continue
        calls = e.get("calls") or []
        if any(_go_reports(c) or _go_terminates(c) for c in calls):
            continue
        empty = e.get("stmts", 0) == 0
        detail = (f"`if {e.get('name')} != nil` with an empty body — the "
                  f"failure was checked and then dropped"
                  if empty else
                  f"`if {e.get('name')} != nil` returns nothing, panics at "
                  f"nothing and reports to nobody — control leaves with the "
                  f"failure unanswered")
        out.append(Finding(
            path=path, line=e.get("line", 0), col=0,
            symbol=e.get("fn") or "<file>", variant=VARIANT_GO_EMPTY,
            trigger=f"error `{e.get('name')}`", trigger_line=e.get("line", 0),
            handler=f"if {e.get('name')} != nil", detail=detail))
    for b in shape.get("blanks") or []:
        out.append(Finding(
            path=path, line=b.get("line", 0), col=0,
            symbol=b.get("fn") or "<file>", variant=VARIANT_GO_DISCARDED,
            trigger=f"error `{b.get('name')}`", trigger_line=b.get("line", 0),
            handler=f"_ = {b.get('name')}",
            detail=f"`_ = {b.get('name')}` — Go has one name for throwing a "
                   f"value away, and this is an error going into it"))
    return sorted(out)


def to_claims(findings) -> list[Claim]:
    """Collapse findings to one claim per ``(file, symbol, variant)``.

    Deliberately lossy: a function with four swallowing handlers around outbound
    calls is one question to answer, not four.  The findings ride along so the
    checker can still name every line.
    """
    buckets: dict[tuple[str, str, str], list[Finding]] = {}
    for f in findings:
        buckets.setdefault((f.path, f.symbol, f.variant), []).append(f)
    claims = [
        Claim(
            path=path,
            symbol=symbol,
            variant=variant,
            line=min(f.line for f in group),
            findings=tuple(sorted(group)),
        )
        for (path, symbol, variant), group in buckets.items()
    ]
    return sorted(claims)


# ── TypeScript and JavaScript ────────────────────────────────────────────────

#: What answers for a failure in a TypeScript catch. `throw` re-raises,
#: `return` hands the caller something to test, `process.exit` ends it, and a
#: reporter tells somebody. Anything else and control walks out of the catch as
#: if the operation had succeeded -- the question this rule asks, in the shape
#: TypeScript has for it.
TS_ANSWERS = ("throw", "return", "process.exit", "reject", "next(")

#: TypeScript's own outbound names, beside the shared table rather than inside
#: it. `GO_REPORTS` sets the precedent: a table that has to stay true for three
#: languages does not grow a name that means something in one of them.
#:
#: The shared tails came from Python and Go call sites -- `post`, `put`,
#: `send`. Measured here: a `try { await fetch(url) } catch (e) {}` matched
#: none of them, so the strongest fail-open shape TypeScript has was invisible
#: to a rule that had just been pointed at TypeScript files. `fetch` in Python
#: is almost always `cursor.fetchall`, whose tail is `fetchall`, which is why
#: this is not simply added to `http_verb_tails`.
TS_OUTBOUND = frozenset({
    "fetch", "axios", "request", "got", "ky",
    "writeFile", "writeFileSync", "appendFile", "appendFileSync",
    "unlink", "unlinkSync", "rm", "rmSync", "rename", "renameSync",
    "publish", "sendMessage", "sendMail", "send",
})

#: Reporting a failure is answering for it, the same way `GO_REPORTS` is.
TS_REPORTS = ("console.error", "console.warn", "logger.", "log.error",
              "log.warn", "captureException", "reportError", "Sentry.")


def _ts_reporting_assignment(handler_code: str, vocab: Vocabulary) -> bool:
    """Match a declared target's real assignment in comment/string-masked code."""
    for match in re.finditer(
            r"(?<![\w.$])([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)+)\s*=(?!=|>)",
            handler_code):
        if handler_code[:match.start()].rstrip().endswith("."):
            continue  # A suffix after a computed/optional receiver is not this target.
        target = re.sub(r"\s+", "", match.group(1))
        if target in vocab.failure_reporting_assignments:
            return True
    return False


def _ts_reporting_call(handler_code: str, vocab: Vocabulary) -> bool:
    """A real call to an exact declared channel; mentions/declarations do not count."""
    from .test_expectation import _ts_balanced
    for match in re.finditer(
            r"(?<![\w.$])([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*\(", handler_code):
        target = re.sub(r"\s+", "", match.group(1))
        if target not in vocab.failure_reporting_calls or target in {"if", "for", "while", "switch", "catch", "with", "function"}:
            continue
        prefix = handler_code[:match.start()].rstrip()
        if prefix.endswith('.') or re.search(r'\b(?:function\s*\*?|new)\s*$', prefix):
            continue
        end = _ts_balanced(handler_code, match.end()-1, '(', ')')
        if end is None or re.match(r'\s*(?::[^;{}=]*)?\{', handler_code[end:]):
            continue
        return True
    return False

_TS_TRY = re.compile(r"(?<![\w.$])try\s*\{")
_TS_CATCH = re.compile(r"\}\s*catch\s*(?:\(\s*([\w$]*)[^)]*\))?\s*\{")


def _ts_throw_leaves_boundary(mask: str, end: int) -> bool:
    """An immediate throw propagates unless surrounding try/finally can intercept.

    This covers best-effort cleanup inside a handler that rethrows its primary
    error. Unknown intervening statements/control flow remain unproved here.
    """
    from .test_expectation import _ts_balanced
    if not re.match(r"\s*;?\s*throw\b", mask[end:]):
        return False
    for enclosing in _TS_TRY.finditer(mask, 0, end):
        brace = enclosing.end() - 1
        body_end = _ts_balanced(mask, brace, "{", "}")
        if body_end is None:
            return False
        if brace < end < body_end:
            return False  # A containing try body may catch/finalize this throw.
        handler = _TS_CATCH.match(mask, body_end - 1)
        if handler:
            handler_end = _ts_balanced(mask, handler.end() - 1, "{", "}")
            if handler_end is None:
                return False
            if handler.end() <= end < handler_end and re.match(
                    r"\s*finally\b", mask[handler_end:]):
                return False
    return True


def ts_findings(path: str, source: str, vocab: Vocabulary = None) -> list[Finding]:
    """Fail-open handlers in one TypeScript or JavaScript file.

    Closer to the Python half than the Go one, because TypeScript has
    exceptions: the shape is a `try` whose body does something that reaches
    outside or decides an identity, and a `catch` that lets control continue as
    if it had worked.

    What answers for the failure -- and is therefore not a finding -- is
    `throw`, `return`, `process.exit`, a rejected promise, or a call in
    `TS_REPORTS`, or an exact assignment/call target declared by the repo as a
    verified failure-reporting channel. An empty catch is the strongest form:
    the author wrote the handler and left the body out. Reporting declarations
    are vocabulary, not proof of the message or subsequent control flow.

    The vocabulary is the shared one. What makes a try body risky is the same
    table `analyse_source` reads, so a repo that widens it in its own facts
    widens all three languages at once -- the point of keeping the rule in one
    place and the extractors in three.
    """
    from .test_expectation import _ts_mask, _ts_balanced
    vocab = vocab or SHIPPED
    mask = _ts_mask(source)
    out = []

    for m in _TS_TRY.finditer(mask):
        brace = m.end() - 1
        body_end = _ts_balanced(mask, brace, "{", "}")
        if body_end is None:
            continue
        body = source[brace + 1:body_end - 1]
        catch = _TS_CATCH.match(mask, body_end - 1)
        if not catch:
            continue                      # `try/finally` answers nothing here
        cbrace = catch.end() - 1
        cend = _ts_balanced(mask, cbrace, "{", "}")
        if cend is None:
            continue
        handler = source[cbrace + 1:cend - 1]
        # Comment- and string-stripped, because a bound is code and never a
        # mention. A bypass fixture put `// we should throw e here` in an
        # otherwise empty catch and this read the words as the answer -- the
        # exact shape the rule exists to refuse, one level up.
        handler_code = mask[cbrace + 1:cend - 1]

        # Does the try body reach outside, or decide an identity? The tails are
        # the shared vocabulary; a call is `something.tail(`.
        risky = ""
        for tail in sorted(vocab.http_verb_tails | vocab.outbound_tails_strong
                           | vocab.fs_mutation_tails | vocab.auth_deny_tails
                           | TS_OUTBOUND):
            if re.search(r"(?<![\w$])" + re.escape(tail) + r"\s*\(", body):
                risky = tail
                break
        if not risky:
            for word in sorted(vocab.auth_words):
                if re.search(r"(?<![\w$])\w*" + re.escape(word) + r"\w*\s*\(",
                             body, re.IGNORECASE):
                    risky = word
                    break
        if not risky:
            continue

        stripped = handler.strip()
        if any(a in handler_code for a in TS_ANSWERS) or \
                any(r in handler_code for r in TS_REPORTS) or \
                _ts_reporting_assignment(handler_code, vocab) or \
                _ts_reporting_call(handler_code, vocab) or \
                _ts_throw_leaves_boundary(mask, cend):
            continue
        line = source.count("\n", 0, catch.start()) + 1
        name = catch.group(1) or "e"
        out.append(Finding(
            path=path, line=line, col=0, symbol="<module>",
            variant=VARIANT_SWALLOW,
            trigger=f"outbound/auth `{risky}`",
            trigger_line=source.count("\n", 0, brace) + 1,
            handler=(f"`catch ({name})` with an empty body" if not stripped
                     else f"`catch ({name})`"),
            detail=(f"`catch ({name})` neither rethrows, returns, exits nor "
                    f"reports -- control leaves as if `{risky}` had succeeded"),
        ))
    return sorted(out, key=lambda f: (f.line, f.trigger))
