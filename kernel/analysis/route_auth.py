"""Is this Python function a route handler.  SPEC.md §3.

**This module no longer decides anything about auth, and the name is older than
what is left in it.** It used to answer "which route handlers reach no
`auth_decision` pattern", and `checkers/route_auth.py` and
`detectors/route_auth.py` turned that answer into a verdict. Commit `a9ae5fb`
(2026-08-24) cut the `route-auth` kind and both of those programs on measured
grounds -- 703 claims raised in one task, run 8 times -- and left `scan`,
`go_scan`, `go_touches` and `_touches` here with no caller in `kernel/`,
`checkers/` or `detectors/`. Those four are now gone too. What is left is the
half that was never about auth: recognising a route decorator.

What answers the question the verdict used to ask:

  * layer ① of `CLAUDE.md`, where `a9ae5fb` moved the rule text -- an entry
    with no auth call is a decision or an omission, and every session is asked
    which. `kernel/doctrine.py` owns that sentence.
  * the `security-permission` lens, read by a reviewer during `v4 sweep`.
  * `v4 coverage`, which reports operational-risk class #7 (Authorization
    guard tiering) as having no mechanism here. That is the durable record of
    the gap, and it is a command rather than a paragraph.

None of those is a gate, which is the honest description: the class of defect
that lost its program is "a route handler that reaches no auth decision, in a
repo that declared both where its routes live and what an auth decision looks
like".

`facts.auth_decision` is still refused empty by `kernel/analysis/facts_grammar.py`,
and after this nothing in the system turns it into a verdict. `docs/FACTS.md`
says so where the field is documented, because the reason it is still refused
empty -- being made to enumerate who may do what -- never depended on this file.

Still open, and named rather than left to be rediscovered: the only importer of
what is left here is `kernel/analysis/webhook_replay.py::scan`, which has no
caller of its own either -- the `webhook-replay` kind went in the same commit.
Rehoming route recognition into its one consumer, or cutting both, is a change
to a module this task was not given; it is written down here so the next reader
does not have to measure it again.
"""

import ast

from . import pysource

#: Attribute names that register an HTTP route when used as a decorator.
#: Deliberately not `.route`-anything: `router.post` and `requests.post` are
#: different symbols, and matching on the verb alone catches both.
VERBS = ("get", "post", "put", "patch", "delete", "head", "options", "route")

#: Receivers that register routes.  A decorator is a route registration when the
#: receiver looks like a router and the attribute is a verb, which keeps
#: `@retry.post_hook` and `@cache.get` out.
#: What a route decorator hangs off.  A built-in default, and a repo can say
#: otherwise: `facts.route_receivers`.
#:
#: Hard-coded, this was the shape SPEC §2 names -- "change the suffix set from
#: `.py` to `.pyx` and every task passes". A repo whose app object is
#: `application`, `server` or `admin_api` gets zero routes found, silently,
#: while the ship report lists this detector as having run.
DEFAULT_RECEIVERS = ("app", "router", "bp", "blueprint", "api", "route",
                     "v1", "v2")


def receivers(facts=None):
    """The repo's own list, or the built-in one.

    Declared rather than merged: a repo that names its app object is saying
    which names are routes here, and quietly adding eight more would make the
    declaration mean less than it says.
    """
    declared = (facts or {}).get("route_receivers") if facts else None
    if isinstance(declared, (list, tuple)) and declared:
        return tuple(str(d).lower() for d in declared)
    return DEFAULT_RECEIVERS


def _dotted(node) -> str:
    """`a.b.c(...)` -> 'a.b.c'.  Anything unrenderable -> ''."""
    parts, node = pysource.attribute_chain(node)
    if isinstance(node, ast.Name):
        parts.append(node.id)
    elif isinstance(node, ast.Call):
        return ""
    return ".".join(reversed(parts))


def is_route(fn: ast.AST, known=DEFAULT_RECEIVERS) -> bool:
    for dec in getattr(fn, "decorator_list", []):
        target = dec.func if isinstance(dec, ast.Call) else dec
        name = _dotted(target)
        if "." not in name:
            continue
        recv, _, attr = name.rpartition(".")
        if attr.lower() in VERBS and recv.split(".")[-1].lower() in known:
            return True
    return False
