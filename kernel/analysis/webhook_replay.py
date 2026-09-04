"""A webhook that verifies a signature and can still be replayed.  SPEC.md §3.

Rejected once for the right observation and the wrong conclusion: neither
available repo has a webhook handler, so no red instance could be found. That is
what a rule against replayable webhooks looks like in a repo with no webhooks --
the same Wald error the prevention lens exists to correct, and the reason this
is built anyway.

Two questions, because the first without the second is the failure this catches.
A signature proves the body came from the sender. It proves nothing about
*when*, so a captured request replays forever: the signature is still valid,
because it was valid. What closes it is a timestamp window, a nonce store, or a
delivery id checked against what has already been processed.

Handlers are found by route registration, the same way `route-auth` finds them,
and narrowed to those whose path or name says webhook, callback or hook. A
checker that asked this of every route would be asking most routes a question
that does not apply to them.
"""

import ast
import re
from pathlib import Path

from . import pysource, route_auth

WEBHOOK_WORDS = ("webhook", "callback", "hook", "notification", "ipn")

#: Verifying the sender.
SIGNATURE = ("compare_digest", "verify_signature", "verify_webhook", "hmac",
             "new_hmac", "check_signature", "validate_signature")

#: Closing the replay window.  Any one of them is enough -- deciding whether a
#: given window is long enough is a reviewer's judgement, not a checker's.
REPLAY = ("timestamp", "nonce", "delivery_id", "event_id", "idempotency",
          "seen_before", "already_processed", "replay", "max_age", "tolerance",
          "expires", "issued_at", "iat")

#: Matched on word boundaries rather than as substrings.
#:
#: `has_sig` and `has_replay` were substring tests over every name and string
#: literal the handler can reach, and `iat` is three letters. Measured: a
#: handler with no timestamp window, no nonce and no delivery-id check, whose
#: body logged `"initiated"`, came back as bounding replay -- and deleting that
#: one log line turned the same handler into a finding. A vocabulary matched by
#: substring answers about spelling, not about what the code does.
_WORD = re.compile(r"[^A-Za-z0-9]+")

#: The other word boundary: the one Go writes.
#:
#: The vocabulary above is spelled the way Python names are -- `delivery_id`,
#: `already_processed` -- and a separator is what `_WORD` splits on. Go has no
#: separator: the same two names are `deliveryID` and `AlreadyProcessed`, which
#: split into one word each and match nothing. Measured on the first Go green
#: fixture written for this rule: a handler that reads `X-GitHub-Delivery` into
#: `deliveryID` and asks `store.AlreadyProcessed(deliveryID)` was reported as
#: bounding nothing.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _says(seen, vocabulary) -> bool:
    """Does anything the handler reaches name one of these?"""
    words = set()
    for name in seen:
        parts = []
        for chunk in _WORD.split(str(name)):
            parts.extend(w.lower() for w in _CAMEL.split(chunk) if w)
        if not parts:
            continue
        words.update(parts)
        # `verify_signature` is one name and two words; `verifysignature` is
        # one of each. Both have to answer the same, so the joined form is
        # offered too and a vocabulary entry may span the split.
        words.add("_".join(parts))
        words.add("".join(parts))
    return any(v in words for v in vocabulary)


def _names(fn, docstrings):
    """Names and literals control can actually reach.

    Three bypass fixtures walked through earlier versions: a docstring saying
    "Timestamp checking happens upstream", a replay guard written after the
    handler had already returned, and -- once those were fixed -- a parameter
    merely *named* `nonce`. The last is the sharpest: accepting parameter names
    was meant to catch `def handler(..., timestamp)`, and a name is not a guard.
    What counts is what the body does with it.
    """
    out = set()
    for n in pysource.reachable_nodes(fn):
        if isinstance(n, ast.Name):
            out.add(n.id.lower())
        elif isinstance(n, ast.Attribute):
            out.add(n.attr.lower())

        elif isinstance(n, ast.Constant) and isinstance(n.value, str) \
                and id(n) not in docstrings:
            out.add(n.value.lower())
    return out


def is_webhook(fn) -> bool:
    if any(w in fn.name.lower() for w in WEBHOOK_WORDS):
        return True
    for dec in getattr(fn, "decorator_list", []):
        for n in ast.walk(dec):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) \
                    and any(w in n.value.lower() for w in WEBHOOK_WORDS):
                return True
    return False


def scan(root: Path, facts: dict, files=None):
    """[(file, symbol, line, what)] -- webhook handlers missing one of the two."""
    root = Path(root)
    globs = list(facts.get("entrypoint_globs", []))
    if not globs and not files:
        return None
    # The repo's own list, the way `route_auth.scan` reads it. Without this,
    # `is_route` fell back to `DEFAULT_RECEIVERS` -- and `route_auth.py` states
    # the cost of exactly that: a repo whose app object is `application`,
    # `server` or `admin_api` gets zero routes found, silently, while the ship
    # report lists this detector as having run. One declared value, two
    # consumers, and one of them never read it.
    known = route_auth.receivers(facts)

    if files:
        cands = [root / f for f in files if str(f).endswith(".py")]
    else:
        cands = [p for g in globs for p in root.glob(g)
                 if p.is_file() and p.suffix == ".py"]

    out = []
    for path in sorted(set(cands)):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, SyntaxError):
            continue
        docs = pysource.docstring_ids(tree)
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = str(path)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not route_auth.is_route(fn, known=known) or not is_webhook(fn):
                continue
            seen = _names(fn, docs)
            has_sig = _says(seen, SIGNATURE)
            has_replay = _says(seen, REPLAY)
            if not has_sig:
                out.append((rel, fn.name, fn.lineno, "verifies no signature"))
            elif not has_replay:
                out.append((rel, fn.name, fn.lineno,
                            "verifies a signature and nothing bounds replay"))
    return out


#: The two parameters that make a Go function an HTTP handler.
#:
#: Python has to find a route by its decorator, and `route_auth.py` records
#: what that costs: a repo whose app object is `application` or `admin_api`
#: gets zero routes found unless it declared the name. Go needs no declared
#: name. `func(w http.ResponseWriter, r *http.Request)` is the handler
#: signature the language itself defines, and a function with those two
#: parameters is one whatever it is called and wherever it is registered.
_GO_HANDLER_PARAMS = ("http.ResponseWriter", "http.Request")


def _go_alive(item, dead) -> bool:
    """Is this line one control can still reach."""
    limit = dead.get(item.get("fn"))
    return limit is None or item.get("line", 0) <= limit


def go_handlers(shape) -> set:
    """Every function in this file with the HTTP handler signature."""
    by_fn = {}
    for b in shape.get("binds") or []:
        if b.get("fn"):
            by_fn.setdefault(b["fn"], set()).add(b.get("type") or "")
    out = set()
    for fn, types in by_fn.items():
        if any(t.endswith(_GO_HANDLER_PARAMS[0]) for t in types) and \
                any(t.endswith(_GO_HANDLER_PARAMS[1]) for t in types):
            out.add(fn)
    return out


def go_is_webhook(shape, fn: str) -> bool:
    """Its name says so, or the path it was registered with does."""
    if any(w in fn.lower() for w in WEBHOOK_WORDS):
        return True
    for c in shape.get("calls") or []:
        if fn not in (c.get("names") or []):
            continue
        if any(any(w in str(lit).lower() for w in WEBHOOK_WORDS)
               for lit in c.get("strings") or []):
            return True
    return False


def go_names(shape, fn: str) -> set:
    """Names and literals control can actually reach inside `fn`.

    The Python half filters docstrings out of the strings it counts; Go has
    none to filter -- its documentation is comments, and the emitter parses
    without them. What it does have is code below a `return`, which compiles,
    so `dead_after` is read here for the same reason `reachable_nodes` is read
    there.
    """
    dead = shape.get("dead_after") or {}
    out = set()

    def add(name):
        # Not lowered here. `_says` lowers, and it splits on the case boundary
        # first -- `deliveryID` is two words and `deliveryid` is one.
        for part in str(name).split("."):
            if part:
                out.add(part)

    for c in shape.get("calls") or []:
        if c.get("fn") == fn and _go_alive(c, dead):
            add(c.get("name"))
            for lit in c.get("strings") or []:
                out.add(str(lit))
    for r in shape.get("refs") or []:
        if r.get("fn") == fn and _go_alive(r, dead):
            add(r.get("name"))
    for lit in shape.get("strs") or []:
        if lit.get("fn") == fn and _go_alive(lit, dead):
            out.add(str(lit.get("value") or ""))
    for a in shape.get("assigns") or []:
        if a.get("fn") != fn or not _go_alive(a, dead):
            continue
        for t in (a.get("targets") or []) + (a.get("names") or []):
            add(t)
        for lit in a.get("strings") or []:
            out.add(str(lit))
    return out


def go_scan(root: Path, facts: dict, files=None):
    """`scan`, for Go.  Same two questions, same vocabulary, same order."""
    from . import gosource
    root = Path(root)
    globs = list(facts.get("entrypoint_globs", []))
    if not globs and not files:
        return None
    if files:
        cands = [root / f for f in files if str(f).endswith(".go")]
    else:
        cands = [p for g in globs for p in root.glob(g)
                 if p.is_file() and p.suffix == ".go"]

    out = []
    for path in sorted(set(cands)):
        if not path.is_file():
            continue
        shape = gosource.shape(path)
        if shape is None:
            continue
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = str(path)
        lines = {f.get("name"): f.get("line", 0) for f in shape.get("funcs") or []}
        for fn in sorted(go_handlers(shape)):
            if not go_is_webhook(shape, fn):
                continue
            seen = go_names(shape, fn)
            if not _says(seen, SIGNATURE):
                out.append((rel, fn, lines.get(fn, 0), "verifies no signature"))
            elif not _says(seen, REPLAY):
                out.append((rel, fn, lines.get(fn, 0),
                            "verifies a signature and nothing bounds replay"))
    return out
