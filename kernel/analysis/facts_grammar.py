"""The facts grammar — pure, and therefore in the analysis layer.

Split out of `kernel/facts.py` because that module both **states** the grammar
(what a pattern is, when it matches, how a table validates) and **fetches**
things (reads files, runs git). `kernel/analysis/` says in its own contract that
it does no I/O, and one module here imported the whole of `kernel.facts` for two
pure functions -- so the sentence was false and nothing read it.

Nothing in this file opens a file, runs a process, prints or exits. Callers hand
in text and a table and get back matches. `kernel/facts.py` keeps the half that
touches the world and re-exports this one, so `kernel.facts.scan_source` still
means what it always did.
"""

from __future__ import annotations

import ast
import io
import re
import tokenize
from dataclasses import dataclass, field
# For splitting a declared path, never for touching one.
from pathlib import PurePosixPath as Path

SYMBOL_LISTS = ("outbound_write", "outbound_read", "auth_decision")
PATH_LISTS = ("entrypoint_globs", "ui_globs", "config_files", "protected_paths")

#: Keys a repo may declare that nothing requires.  Three; two are read, and
#: the third says below why it is not:
#:
#:   `public_routes`    -- route handlers deliberately unauthenticated. It lives
#:                         in the table rather than in a docstring the checker
#:                         would have to read, because a rule whose exemption is
#:                         invisible is a rule people route around; declaring one
#:                         is a line in a diff with a name on the commit.
#:   `dal_globs`        -- where the layer that owns the store lives. `dal-write`
#:                         reports UNSUPPORTED without it rather than guessing.
#:   `route_receivers`  -- what a route decorator hangs off here. Nothing reads
#:                         it. The sentence here described `route_auth` falling
#:                         back to eight built-in names "while the ship report
#:                         lists the detector as having run", and there is no
#:                         such detector: `detectors/` has no `route_auth.py`,
#:                         the registry has no entry, and `route_auth.py`'s own
#:                         module docstring says its last importer went with the
#:                         webhook-replay kind. The consequence was true of
#:                         `route_auth.DEFAULT_RECEIVERS` before that kind was
#:                         cut, and was copied here.
#:
#:                         Kept rather than dropped: a repo that declares it is
#:                         saying something true about itself, and the key costs
#:                         nothing until something asks again. What is not kept
#:                         is the claim that something asks now.
#:
#: This comment named the first and stopped, and `docs/FACTS.md` -- the document
#: README makes the owner of this file's format -- opened its Optional fields
#: section with "Two lists a repo may declare" and never mentioned
#: `route_receivers` at all. A key a repo may declare, whose meaning is written
#: down nowhere, is a declaration with no reader: the fallback stays in place and
#: the repo is told nothing.
OPTIONAL_LISTS = ("public_routes", "dal_globs", "route_receivers")

#: Optional.  Names a table this repo has none of, and how that was established.
#:
#: The refusal of an empty table was right about the failure it was aimed at --
#: an adopter leaving `auth_decision` blank got a silently permissive repo -- and
#: wrong about repos where the answer really is none. Measured on a first
#: adoption of a library with no outbound write: it could not adopt at all, and
#: the message told it to write entries that do not exist.
#:
#: So absence stops being silence and becomes a signed statement. `[]` alone is
#: still refused; `[]` plus a line here saying what was searched and what it
#: returned is a claim with an author, in a diff, that a later reader can re-run.
ABSENT_KEY = "absent"

#: Long enough that "n/a" and "none" do not pass.  A reason that cannot be
#: re-run is the same silence wearing a word.
ABSENT_MIN_CHARS = 20

#: Path lists a repo may declare as empty.  The key stays required, so `[]` is a
#: statement -- "this repo has no front end" -- rather than an omission, and the
#: two are different things. `auth_decision` is deliberately not here: an empty
#: one asked whether this repo really decides nothing about who may do what, and
#: the answer was five entries nobody had written down.
#:
#: A detector reading an empty list must treat it as "this surface does not
#: exist here", not fall back to a generic vocabulary -- falling back would make
#: a declaration of absence indistinguishable from a missing table.
MAY_BE_EMPTY = ("ui_globs", "config_files")
REQUIRED_KEYS = ("repo", "generated_from_commit") + SYMBOL_LISTS + PATH_LISTS

# Where the symbol came from.  Free-form would let the file rot into prose, so
# the set is closed and adding one is a deliberate edit to this module.
KINDS = frozenset(
    {
        # What a kind is for, and it is only one thing: `external_write`
        # asks `e.kind not in LOCAL_KINDS` -- is this write local, so that
        # failing reports itself, or remote, so that a 2xx can mean nothing.
        # Everything else about a kind is printed and never read.
        #
        # Six vendor names lived here -- telegram, meta, shopify, openai,
        # gemini, gcs -- and none of them was ever read. They were the stack of
        # the repo this framework was built against, written into the schema of
        # every repo that adopts it, and the closed set then made an adopter on
        # any other vendor edit this module to classify its own writes. That is
        # a cost with nothing on the other side of it.
        "http",       # remote: a call whose failure this process may not see
        "db",         # local: a transactional statement reports its own failure
        "fs",         # local: a filesystem syscall raises
        "secret",     # an OS keychain or equivalent secret store
        "system",     # host state -- schedulers, services
        "authz",      # only for auth_decision entries
        # Written by `kernel.facts propose`, never by a person. It means a
        # row nobody has read yet. The file stays loadable so a repo can be
        # adopted while its table is still being pruned, and `doctor`
        # reports how many rows are still in this state -- the alternative
        # was a proposal that could not validate, which is a proposal that
        # bricks the repo the moment somebody redirects it into place.
        "proposed",
    }
)

MATCH_MODES = frozenset({"symbol", "regex"})

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_SEEN_AT_RE = re.compile(r"^(?P<path>[^:\s][^:]*):(?P<line>[1-9][0-9]*)$")


class FactsError(ValueError):
    """The facts file is unusable.  Callers must not continue without it."""


@dataclass(frozen=True)
class Entry:
    pattern: str
    seen_at: str
    kind: str
    match: str = "symbol"
    note: str = ""

    @property
    def seen_at_path(self) -> str:
        return self.seen_at.split(":", 1)[0]

    @property
    def seen_at_line(self) -> int:
        return int(self.seen_at.split(":", 1)[1])


@dataclass(frozen=True)
class Facts:
    repo: str
    generated_from_commit: str
    outbound_write: tuple[Entry, ...]
    outbound_read: tuple[Entry, ...]
    auth_decision: tuple[Entry, ...]
    entrypoint_globs: tuple[str, ...]
    ui_globs: tuple[str, ...]
    config_files: tuple[str, ...]
    protected_paths: tuple[str, ...]
    absent: tuple[tuple[str, str], ...] = ()
    source: str = field(default="", compare=False)

    def entries(self, name: str) -> tuple[Entry, ...]:
        if name not in SYMBOL_LISTS:
            raise KeyError(name)
        return getattr(self, name)

    def absence_reason(self, name: str) -> str:
        """Why this table is empty, or "" if nobody said.

        A detector with an empty table and no reason is looking at a repo that
        never answered; one with a reason is looking at a repo that did. Only
        the second is safe to report clean about.
        """
        return dict(self.absent).get(name, "")


def build(obj: object, *, source: str = "<memory>") -> Facts:
    """Validate an already-parsed object and return `Facts`."""

    validate(obj, source=source)
    assert isinstance(obj, dict)  # validate() guarantees this
    return Facts(
        repo=obj["repo"],
        generated_from_commit=obj["generated_from_commit"],
        outbound_write=_entries(obj["outbound_write"]),
        outbound_read=_entries(obj["outbound_read"]),
        auth_decision=_entries(obj["auth_decision"]),
        entrypoint_globs=tuple(obj["entrypoint_globs"]),
        ui_globs=tuple(obj["ui_globs"]),
        config_files=tuple(obj["config_files"]),
        protected_paths=tuple(obj["protected_paths"]),
        absent=tuple(sorted((obj.get(ABSENT_KEY) or {}).items())),
        source=source,
    )


def _entries(items: list) -> tuple[Entry, ...]:
    return tuple(
        Entry(
            pattern=i["pattern"],
            seen_at=i["seen_at"],
            kind=i["kind"],
            match=i.get("match", "symbol"),
            note=i.get("note", ""),
        )
        for i in items
    )


def validate(obj: object, *, source: str = "<memory>") -> None:
    """Raise `FactsError` naming the first thing wrong with `obj`."""

    def bad(msg: str) -> None:
        raise FactsError(f"{source}: {msg}")

    if not isinstance(obj, dict):
        bad(f"top level must be a JSON object, got {type(obj).__name__}")
    assert isinstance(obj, dict)

    missing = [k for k in REQUIRED_KEYS if k not in obj]
    if missing:
        bad(f"missing required key(s): {', '.join(sorted(missing))}")
    unknown = [k for k in obj
               if k not in REQUIRED_KEYS + OPTIONAL_LISTS + (ABSENT_KEY,)]
    if unknown:
        bad(f"unknown key(s): {', '.join(sorted(unknown))}")

    declared_absent = _validate_absent(obj, bad=bad)

    if not isinstance(obj["repo"], str) or not obj["repo"].strip():
        bad("repo must be a non-empty string")
    commit = obj["generated_from_commit"]
    if not isinstance(commit, str) or not _COMMIT_RE.match(commit):
        bad(f"generated_from_commit must be a 40-char lowercase git sha, got {commit!r}")

    for name in SYMBOL_LISTS:
        _validate_symbol_list(obj[name], name=name, bad=bad,
                              absent=declared_absent)
    for name in PATH_LISTS:
        _validate_path_list(obj[name], name=name, bad=bad,
                            absent=declared_absent)

    _check_write_read_disjoint(obj["outbound_write"], obj["outbound_read"], bad=bad)


def _validate_absent(obj: dict, *, bad) -> frozenset:
    """The `absent` map, if any.  Returns the set of names it covers.

    Everything it can say has to be re-runnable by the next person: which table,
    and what was searched to establish it. A name that also carries entries is
    the one thing it must refuse -- "this repo has none" alongside three of them
    is not an oversight, it is a table that outlived its reason.
    """
    raw = obj.get(ABSENT_KEY)
    if raw is None:
        return frozenset()
    if not isinstance(raw, dict):
        bad(f"{ABSENT_KEY} must be an object mapping a table name to the reason "
            f"it is empty, got {type(raw).__name__}")
    assert isinstance(raw, dict)

    declarable = tuple(n for n in SYMBOL_LISTS + PATH_LISTS
                       if n not in MAY_BE_EMPTY)
    for key, reason in sorted(raw.items()):
        if key not in declarable:
            extra = (" — it may already be empty without saying so"
                     if key in MAY_BE_EMPTY else "")
            bad(f"{ABSENT_KEY}[{key!r}] is not a table absence can be declared "
                f"for{extra}; expected one of {sorted(declarable)}")
        if not isinstance(reason, str) or len(reason.strip()) < ABSENT_MIN_CHARS:
            bad(f"{ABSENT_KEY}[{key!r}] must say how the absence was established "
                f"— at least {ABSENT_MIN_CHARS} characters, and something a "
                f"reader can re-run, not 'n/a'")
        if obj.get(key):
            bad(f"{ABSENT_KEY}[{key!r}] says this repo has none and {key} lists "
                f"{len(obj[key])}. One of the two is out of date.")
    return frozenset(raw)


def _empty_table_message(name: str) -> str:
    return (f"{name} is empty — an empty table makes every detector report a "
            f"clean repo. If this repo really has none, say so and say how you "
            f"know: \"{ABSENT_KEY}\": {{\"{name}\": \"grep … at <commit> → 0 "
            f"hits\"}}")


def _validate_symbol_list(items: object, *, name: str, bad,
                          absent=frozenset()) -> None:
    if not isinstance(items, list):
        bad(f"{name} must be a list, got {type(items).__name__}")
    assert isinstance(items, list)
    if not items and name not in absent:
        bad(_empty_table_message(name))
    seen: set[str] = set()
    for index, item in enumerate(items):
        where = f"{name}[{index}]"
        if not isinstance(item, dict):
            bad(f"{where} must be an object, got {type(item).__name__}")
        unknown = set(item) - {"pattern", "seen_at", "kind", "match", "note"}
        if unknown:
            bad(f"{where} has unknown field(s): {', '.join(sorted(unknown))}")
        for required in ("pattern", "seen_at", "kind"):
            if required not in item:
                bad(f"{where} is missing {required!r}")
            if not isinstance(item[required], str) or not item[required].strip():
                bad(f"{where}.{required} must be a non-empty string")

        pattern = item["pattern"]
        if pattern in seen:
            bad(f"{where}.pattern {pattern!r} is a duplicate within {name}")
        seen.add(pattern)

        match = item.get("match", "symbol")
        if match not in MATCH_MODES:
            bad(f"{where}.match must be one of {sorted(MATCH_MODES)}, got {match!r}")
        if match == "regex":
            try:
                re.compile(pattern)
            except re.error as exc:
                bad(f"{where}.pattern is not a valid regex: {exc}")
        elif not re.fullmatch(r"\.?[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*", pattern):
            bad(
                f"{where}.pattern {pattern!r} is not a dotted symbol; "
                "use \"match\": \"regex\" for anything that is not a callee name"
            )

        kind = item["kind"]
        if kind not in KINDS:
            bad(f"{where}.kind {kind!r} is not one of {sorted(KINDS)}")
        if (kind == "authz") != (name == "auth_decision"):
            bad(f"{where}.kind {kind!r} does not belong in {name}")

        if not _SEEN_AT_RE.match(item["seen_at"]):
            bad(f"{where}.seen_at {item['seen_at']!r} must look like 'relative/path.py:123'")
        if item["seen_at"].startswith("/"):
            bad(f"{where}.seen_at must be repo-relative, got {item['seen_at']!r}")
        if "note" in item and not isinstance(item["note"], str):
            bad(f"{where}.note must be a string")


def _validate_path_list(items: object, *, name: str, bad,
                        absent=frozenset()) -> None:
    if not isinstance(items, list):
        bad(f"{name} must be a list, got {type(items).__name__}")
    assert isinstance(items, list)
    if not items and name not in MAY_BE_EMPTY and name not in absent:
        bad(_empty_table_message(name))
    seen: set[str] = set()
    for index, item in enumerate(items):
        where = f"{name}[{index}]"
        if not isinstance(item, str) or not item.strip():
            bad(f"{where} must be a non-empty string")
        if item.startswith("/"):
            bad(f"{where} {item!r} must be repo-relative, not absolute")
        if ".." in Path(item).parts:
            bad(f"{where} {item!r} must not escape the repo with '..'")
        if item in seen:
            bad(f"{where} {item!r} is a duplicate within {name}")
        seen.add(item)


def _check_write_read_disjoint(writes: list, reads: list, *, bad) -> None:
    """The anti-loop invariant.

    A read-back is the evidence a write claim asks for.  If a write pattern
    also matches the read-back's own symbol, answering the claim creates a new
    one, forever.  So: no write pattern may match a read pattern's symbol, and
    the two lists may not share a pattern string.

    The original shipped form of the bug — an outbound list written as
    `requests.*`, which contains `requests.get` — is unrepresentable here in
    the first place: the symbol grammar has no wildcard, `requests.post` and
    `requests.get` are separate entries, and `_validate_symbol_list` rejects
    anything with a `*` in it unless it declares `match: regex`.  This check is
    the backstop for the version that is still expressible, a bare suffix like
    `.get` on the write list.
    """

    write_patterns = {w["pattern"] for w in writes if isinstance(w, dict) and "pattern" in w}
    read_patterns = {r["pattern"] for r in reads if isinstance(r, dict) and "pattern" in r}
    both = write_patterns & read_patterns
    if both:
        bad(f"pattern(s) listed as both outbound_write and outbound_read: {', '.join(sorted(both))}")

    symbol_writes = [
        w["pattern"]
        for w in writes
        if isinstance(w, dict) and w.get("match", "symbol") == "symbol" and "pattern" in w
    ]
    for read in reads:
        if not isinstance(read, dict) or read.get("match", "symbol") != "symbol":
            continue
        read_pattern = read.get("pattern", "")
        for write_pattern in symbol_writes:
            overlaps = matches_symbol(write_pattern, read_pattern.lstrip(".")) or matches_symbol(
                read_pattern, write_pattern.lstrip(".")
            )
            if overlaps:
                bad(
                    f"outbound_write pattern {write_pattern!r} also matches the "
                    f"outbound_read symbol {read['pattern']!r} — a read-back would "
                    "derive its own write claim (infinite claim loop)"
                )


# --------------------------------------------------------------------------
# matching
# --------------------------------------------------------------------------


def matches_symbol(pattern: str, dotted: str) -> bool:
    """Does `pattern` name the callee `dotted`?

    `requests.post`   matches `requests.post`
    `.send_message`   matches `self.client.send_message` (any receiver)
    `.publish`        does NOT match `result.publish_id` — segments, not substrings
    `.publish`        does NOT match the bare local variable `publish`; a
                      leading dot means "an attribute of something", and
                      `publish.ig_post_url` in core/workers/dispatch.py:111 is a
                      local named after the result, not a call to publish
    """

    if pattern.startswith("."):
        return "." in dotted and ("." + dotted).endswith(pattern)
    return dotted == pattern or dotted.endswith("." + pattern)


def _glob_to_regex(glob: str) -> re.Pattern[str]:
    out = ["(?s)\\A"]
    index = 0
    while index < len(glob):
        char = glob[index]
        if glob.startswith("**/", index):
            out.append("(?:.*/)?")
            index += 3
        elif glob.startswith("**", index):
            out.append(".*")
            index += 2
        elif char == "*":
            out.append("[^/]*")
            index += 1
        elif char == "?":
            out.append("[^/]")
            index += 1
        else:
            out.append(re.escape(char))
            index += 1
    out.append("\\Z")
    return re.compile("".join(out))


def path_matches(rel_path: str, globs) -> bool:
    """True when `rel_path` is covered by any glob (`**` crosses directories)."""

    return any(_glob_to_regex(glob).match(rel_path) for glob in globs)


def dotted_names(tree: ast.AST):
    """Yield `(dotted_name, lineno)` for every attribute/name chain in `tree`.

    Every prefix of a chain is yielded, so `TOOL_PERMISSIONS.get(...)` offers
    both `TOOL_PERMISSIONS.get` and `TOOL_PERMISSIONS` — a module-level table
    used as a receiver is still a reference to that table.

    Bare references count: `asyncio.to_thread(self.client.send_message, ...)`
    passes the method without calling it, and that is still the send.  Import
    statements and `def` headers produce no Name node, so a pattern never
    matches its own definition or its import line.

    A chain whose base is not a name — `(folder / name).write_bytes(...)` — is
    rendered with a `?` base, so suffix patterns still match it.
    """

    inner: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, (ast.Attribute, ast.Name)):
            inner.add(id(node.value))
    for node in ast.walk(tree):
        if id(node) in inner:
            continue
        if isinstance(node, ast.Attribute):
            parts = _chain(node)
            for end in range(1, len(parts) + 1):
                yield ".".join(parts[:end]), node.lineno
        elif isinstance(node, ast.Name):
            yield node.id, node.lineno


def _chain(node: ast.AST) -> list[str]:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    else:
        # A call, a subscript, a parenthesised expression: the receiver has no
        # name, but the tail of the chain is still the symbol we care about.
        parts.append("?")
    return list(reversed(parts))


def go_dotted_names(shape):
    """Yield `(dotted_name, lineno)` for every chain in a Go file's shape.

    Every prefix comes for free and not by accident: the emitter's walk records
    a selector and then descends into it, so `client.Session.Do` arrives as
    three refs on the same line. That is the property `dotted_names` above
    builds deliberately, and it is what lets a table row name a receiver as
    well as a call.

    Go has no equivalent of the `?` base. `dotted` drops an unresolvable head
    -- `f().Post` renders as `Post` -- so a leading-dot pattern still matches
    it and a fully-qualified one does not, which is the same answer.
    """
    for r in shape.get("refs") or []:
        name = r.get("name") or ""
        if name:
            yield name, r.get("line", 0)
    for c in shape.get("calls") or []:
        name = c.get("name") or ""
        if name:
            yield name, c.get("line", 0)


#: A Go comment, for blanking before a regex entry is matched.
#:
#: `_regex_lines` uses `tokenize`, which is Python's. The reason it blanks
#: comments at all holds in either language -- prose describing a write is not
#: a write -- and Go's two comment forms are simple enough to blank without a
#: tokeniser, as long as a `//` inside a string stays a string. That is what
#: the scanner below is for; `"https://example.com"` is the case it exists for.
def ts_dotted_names(source: str):
    """Yield `(dotted_name, lineno)` for every chain in a TypeScript file.

    Same contract as `dotted_names` and `go_dotted_names`: every prefix of a
    chain is yielded, so a table row can name a receiver as well as a call --
    `client.messages.create` arrives as three names on the same line, and a row
    for `client.messages` matches the site as surely as one for the full chain.

    A bare name is yielded too, because `fetch(url)` has no receiver and is
    still the outbound call TypeScript is most often written with.

    Comments and strings are blanked first. A chain named inside either is not
    a call, and counting one is how a table row starts matching prose.
    """
    from . import symbols
    masked = symbols._TS_STRING.sub(
        lambda m: '"' + " " * max(0, len(m.group(0)) - 2) + '"',
        symbols._TS_LINE_COMMENT.sub(
            " ", symbols._TS_BLOCK_COMMENT.sub(" ", source)))
    for m in re.finditer(
            r"(?<![\w.$])([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)\s*[(<]",
            masked):
        parts = [p.strip() for p in m.group(1).split(".")]
        line = masked.count("\n", 0, m.start()) + 1
        for end in range(1, len(parts) + 1):
            yield ".".join(parts[:end]), line


def _ts_regex_lines(source: str) -> dict[int, str]:
    """Source lines with TypeScript comments blanked, string literals kept."""
    from . import symbols
    masked = symbols._TS_LINE_COMMENT.sub(
        " ", symbols._TS_BLOCK_COMMENT.sub(" ", source))
    return {i: line for i, line in enumerate(masked.splitlines(), start=1)}


def _go_regex_lines(source: str) -> dict[int, str]:
    """Source lines with Go comments blanked, string literals kept."""
    out, buf = {}, list(source)
    i, n = 0, len(source)
    in_str, quote, in_block = False, "", False
    while i < n:
        ch = source[i]
        nxt = source[i + 1] if i + 1 < n else ""
        if in_block:
            if ch == "*" and nxt == "/":
                buf[i] = buf[i + 1] = " "
                i += 2
                in_block = False
                continue
            if ch != "\n":
                buf[i] = " "
            i += 1
            continue
        if in_str:
            if ch == "\\" and quote != "`":
                i += 2
                continue
            if ch == quote:
                in_str = False
            i += 1
            continue
        if ch in ("\"", "'", "`"):
            in_str, quote = True, ch
            i += 1
            continue
        if ch == "/" and nxt == "/":
            while i < n and source[i] != "\n":
                buf[i] = " "
                i += 1
            continue
        if ch == "/" and nxt == "*":
            in_block = True
            buf[i] = buf[i + 1] = " "
            i += 2
            continue
        i += 1
    for lineno, text in enumerate("".join(buf).splitlines(), start=1):
        out[lineno] = text
    return out


def _regex_lines(source: str) -> dict[int, str]:
    """Source lines with `#` comments blanked, string literals kept.

    SQL text, argv literals and `open(path, "w")` modes all live inside string
    literals, so they cannot be blanked; comments can, and prose in a comment
    is the main source of regex false positives.
    """

    lines = source.splitlines()
    out = {i + 1: list(text) for i, text in enumerate(lines)}
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return {i + 1: text for i, text in enumerate(lines)}
    for token in tokens:
        if token.type != tokenize.COMMENT:
            continue
        (srow, scol), (erow, ecol) = token.start, token.end
        for row in range(srow, erow + 1):
            if row not in out:
                continue
            buf = out[row]
            lo = scol if row == srow else 0
            hi = ecol if row == erow else len(buf)
            for col in range(lo, min(hi, len(buf))):
                buf[col] = " "
    return {row: "".join(buf) for row, buf in out.items()}


def scan_source(source: str, entries, *, lang: str = "py") -> dict[str, list[int]]:
    """Return `{pattern: [lineno, ...]}` for one source string.

    `lang` picks the extractor and nothing else. The table is the rule -- one
    set of patterns, one `matches_symbol`, one answer about what a row means --
    and a second copy of it per language is the drift `dal_write` and
    `webhook_replay` both record paying for.
    """

    hits: dict[str, list[int]] = {e.pattern: [] for e in entries}
    symbol_entries = [e for e in entries if e.match == "symbol"]
    regex_entries = [e for e in entries if e.match == "regex"]

    if symbol_entries:
        chains = ()
        if lang == "go":
            from . import gosource
            shape = gosource.shape_source(source)
            chains = go_dotted_names(shape) if shape is not None else ()
        elif lang == "ts":
            chains = ts_dotted_names(source)
        else:
            try:
                tree = ast.parse(source)
            except SyntaxError:
                tree = None
            chains = dotted_names(tree) if tree is not None else ()
        for dotted, lineno in chains:
            for entry in symbol_entries:
                if matches_symbol(entry.pattern, dotted):
                    hits[entry.pattern].append(lineno)

    if regex_entries:
        lines = (_go_regex_lines(source) if lang == "go"
                 else _ts_regex_lines(source) if lang == "ts"
                 else _regex_lines(source))
        for entry in regex_entries:
            compiled = re.compile(entry.pattern)
            for lineno in sorted(lines):
                if compiled.search(lines[lineno]):
                    hits[entry.pattern].append(lineno)

    return {pattern: sorted(set(linenos)) for pattern, linenos in hits.items()}




#: Where a client bundle lives when the repo has not said.
#:
#: Read by the `bundle-secret` detector and its checker, which is the point:
#: they had a list each -- three globs in one, four in the other -- and disagreed
#: about `[]`. The checker did `facts.get("ui_globs") or [...]`, so a repo
#: declaring `"ui_globs": []` (a statement that it has no client surface, which
#: `MAY_BE_EMPTY` exists to make sayable) was handed four guessed globs, while
#: the detector branched on `"ui_globs" in facts` and handed back nothing. One
#: rule, two answers, and the wrong one belonged to the program that decides
#: the verdict.
UI_GLOBS_DEFAULT = ("web/**", "frontend/**", "client/**")


def ui_globs(facts) -> list:
    """Where this repo's client code is, or the default when it has not said.

    `[]` is an answer: this repo has no front end. A missing key is a repo that
    has not been catalogued. Falling back for both makes a declaration of
    absence look identical to a missing table -- `kernel/derive.py` names that
    expression as the shape that turns a detector off in silence.
    """
    if isinstance(facts, dict) and "ui_globs" in facts:
        return list(facts["ui_globs"] or [])
    return list(UI_GLOBS_DEFAULT)
