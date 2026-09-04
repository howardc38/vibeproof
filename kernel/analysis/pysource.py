"""Two questions about Python source that four checkers each answered alone.

**Is this string a docstring?** Prose describing a write is not a write, prose
naming a config key is not a read, prose mentioning a bound is not a bound. This
was learned first by `dead_wiring`, on itself -- its own comment explaining how a
key had slipped past made the key look wired, so the check passed by talking
about the thing it was meant to find. Then again by `dal_write`, `test_shape`,
and `webhook_replay`, each rediscovering it from its own bypass fixture.

**Can this statement run?** A `raise` after an unconditional `return`, a replay
guard after the handler has returned, a `Semaphore` in a branch nothing reaches
-- all present, none reachable. Every checker that asks "does the body contain
X" is really asking "can X happen", and the two differ by exactly this.

Four copies of an answer is four chances to fix three of them.
"""

import ast


def docstring_ids(tree) -> set:
    """`id()` of every Constant that is a docstring, so callers can skip them."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                out.add(id(first.value))
    return out


def reachable(body) -> list:
    """The statements in this body that control can reach.

    Stops at an unconditional exit -- `return`, `raise`, `continue`, `break` --
    because everything after it is text. Nested definitions are kept: a function
    defined after a return is unreachable *as this body's flow* but it is still
    a definition, and dropping it would hide a class from anything walking for
    them.
    """
    out = []
    for stmt in body:
        out.append(stmt)
        if isinstance(stmt, (ast.Return, ast.Raise, ast.Continue, ast.Break)):
            break
    return out


#: The fields of a statement that hold *other statements*.  `reachable` decides
#: what happens to those, so nothing else may walk them.
_STATEMENT_FIELDS = ("body", "orelse", "finalbody", "handlers")


def reachable_nodes(fn):
    """Every node under `fn` that control can reach, decorators included.

    The filtering has to happen at every level, and it used to happen at one.
    The old shape collected reachable statements per body -- correctly -- and
    then finished with `ast.walk(stmt)` over each of them, which takes the whole
    subtree back, including the statements `reachable` had just excluded. So a
    dead statement was dropped only when its *own* block was the outermost one::

        if body:
            return process(body)
            if store.seen_before(nonce):   # dead, and inside a live `if`
                return "dup"

    Measured on `webhook_replay.scan`: that handler reported nothing -- a
    webhook with no replay bound at all read as bounded, because the guard was
    "in the function". The existing bypass fixture catches the same move one
    level further out, which is why it looked covered.

    So: walk each reachable statement's own expressions -- its test, its value,
    its targets -- and leave every statement list to `walk_body`, which asks
    `reachable` about it.
    """
    out = []
    for dec in getattr(fn, "decorator_list", []):
        out += list(ast.walk(dec))

    def expressions(node):
        """Everything under `node` except the statements it contains."""
        for field, value in ast.iter_fields(node):
            if field in _STATEMENT_FIELDS:
                continue
            for item in (value if isinstance(value, list) else [value]):
                if isinstance(item, ast.AST):
                    out.extend(ast.walk(item))

    def walk_body(body):
        for stmt in reachable(body):
            out.append(stmt)
            expressions(stmt)
            for name in ("body", "orelse", "finalbody"):
                inner = getattr(stmt, name, None)
                if isinstance(inner, list):
                    walk_body(inner)
            for h in getattr(stmt, "handlers", []):
                out.append(h)
                expressions(h)          # the exception class it catches
                walk_body(h.body)

    walk_body(getattr(fn, "body", []))
    return out


def imported_modules(src: str, package: str = "") -> set:
    """Dotted module names this source imports.

    One owner, because two callers wanted it within a day of each other:
    `derive._reads_facts` follows a detector's imports to see whether it reaches
    the facts table, and `register` compares a detector's imports with its
    checker's to decide whether they can disagree. Two AST walks answering the
    same question is two answers.

    `package` is the dotted package the source lives in, and it is what a
    relative import needs to become a name. Without it they are skipped, which
    is right for a caller that does not know where the file sits and wrong for
    one that does: `layers.scan` arrived as a third caller, reading a file it
    located itself, and every `from ..kernel import x` in the repo was invisible
    to `layer-boundary` -- the checker whose whole subject is which direction an
    import goes. The parameter is how a caller that knows says so.
    """
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return set()
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = _resolve(node, package)
            if base is None:
                continue
            if base:
                out.add(base)
            out.update(f"{base}.{a.name}" if base else a.name
                       for a in node.names)
    return out


def _resolve(node, package: str):
    """The dotted module an `ImportFrom` names, or None when it cannot be said.

    `from x import y` is `x`. `from . import y` inside `a.b` is `a.b`, and
    `from ..c import y` there is `a.c`. A relative import that climbs past the
    root, or one in a file whose package the caller did not give, cannot be
    resolved -- and a guess would be worse than a skip, because the answer is
    fed to a rule about which layer a name belongs to.
    """
    if not node.level:
        return node.module or None
    if not package:
        return None
    parts = package.split(".")
    if node.level > len(parts):
        return None
    base = parts[:len(parts) - node.level + 1]
    if node.module:
        base += node.module.split(".")
    return ".".join(base)


def handed_over(call: ast.Call):
    """The nodes this call hands to something that will call them.

    A symbol in `func` position is the dispatcher; the one that actually runs
    can be an argument.  `Depends(resolve_brand_from_slug)`,
    `asyncio.to_thread(do_the_write, x)`, `loop.run_in_executor(None, f)`,
    `partial(f, x)` -- in every one of them the name that matters is in `args`,
    and a scan reading `call.func` alone sees `Depends`, `asyncio.to_thread`
    and nothing else.

    Measured on the reference adopter: `route_auth._touches` recognised
    `Depends` and not `resolve_brand_from_slug`, so 22 of 61 FastAPI handlers
    passed on `Depends` being in the table rather than on the function it
    names, and the facts table had grown three pre-auth patterns registered as
    auth to compensate -- which the doctrine forbids in as many words. The same
    blindness in `fail_closed` hid 39 handlers behind `asyncio.to_thread`.

    One function rather than one per checker, because it was the same defect
    twice: this repo's own doctrine says a failure appearing twice means the
    first repair was made in the wrong place.

    Names and attribute chains only.  A literal, a call, a lambda or a
    comprehension is not a symbol this file names.  That leaves one real false
    positive -- `log.info(require_permission)` counts -- and it is bounded by
    the callers: both compare against a table the repo declared, so a name has
    to have been called an auth decision or a transport already before this can
    say anything at all about it.

    (Rendering is the caller's, deliberately.  Five modules here carry their own
    `dotted_name`, and unifying those is a separate change with its own
    fixtures; this adds the missing rule in one place without pretending to
    have made that one too.)
    """
    for node in list(call.args) + [k.value for k in call.keywords]:
        if isinstance(node, (ast.Name, ast.Attribute)):
            yield node


def attribute_chain(node):
    """(["a", "b", "c"], head) for `a.b.c` -- the walk, without the verdict.

    Four modules here render a dotted name and every one of them walks
    `ast.Attribute` the same way. What they do at the *head* is not the same
    and is load-bearing in each:

        fail_closed     a call head is `()` and a subscript is `[]`, because
                        its vocabulary distinguishes them
        external_write  anything unresolvable is `?`
        route_auth      a call head discards the whole name -- `_unused =
                        f().check` decides nothing
        test_shape      an unresolvable head leaves the tail alone

    So this is the shared half and only the shared half. Collapsing the other
    half into one function with a marker parameter would be four behaviours
    behind one signature, which reads as agreement and is not.

    Returns the attributes outermost-last and the node the chain rests on, so a
    caller renders `".".join(reversed(parts))` after deciding what the head is.
    """
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    return parts, cur
