"""Blanking credential shapes before they enter something append-only.

Pure text, and it lives here because three modules in `kernel/` need it and one
of them owned it. `runner` held `redact`; `ledger` and `derive` each reached
for it with a late `from .runner import redact` and a comment explaining that
the import had to be late because `runner` imports them back. That is an import
cycle with two apologies written next to it, and `structural_lint` says so:
`LINT-IMPORT-CYCLE kernel/ledger.py:640 -> kernel.runner`.

The function depends on nothing from `runner` -- regexes and
`analysis.secret_patterns` -- so the cycle was never about what the code needs.
`kernel -> analysis` is a declared edge; `analysis -> kernel` is not, and this
module does not need one.
"""

import re
import sys

#: `(pattern, replacement)`, because the replacement is a property of the
#: pattern and was being guessed from `pat.groups`. Two rules with the same
#: group count would silently take each other's substitution -- and the
#: bare-credential rule added below has two groups, exactly like the URL rule
#: whose replacement keeps the host. Guessing worked while no two rules
#: collided, which is the state a table is in right up until it is not.
_REDACTIONS = (
    (re.compile(r"(://[^:/@\s]+):([^@/\s]+)@"), r"\1:[redacted]@"),   # userinfo in a URL
    (re.compile(r"\b(sk-|ghp_|gho_|github_pat_|xox[baprs]-|AIza)[A-Za-z0-9_\-]{12,}"),
     "[redacted]"),
    # Keyed on the shape of the value, not on what sits beside it. The
    # separator has to be one an assignment uses -- `:`, `=`, quotes -- and the
    # value has to look like a credential rather than merely be long enough.
    #
    # It read `[\"'\s:=]+` followed by twelve or more of `[A-Za-z0-9/+_-]`, so
    # a plain space qualified and any long English word did too. Measured:
    # `check the token specifically.` came out as `check the token [redacted].`
    # -- "specifically" is exactly twelve letters -- and a reviewer chasing the
    # blanked word finds a word. `redact`'s own docstring calls itself "a floor,
    # not a boundary", and a floor that eats prose is one people route around.
    (re.compile(r"(?i)\b(api[_-]?key|secret|token|password|passwd)"
                r"([\"\'\s]*[:=][\"\'\s]*)"
                r"([A-Za-z0-9/+_\-]*(?:[0-9][A-Za-z0-9/+_\-]*[A-Za-z]"
                r"|[A-Za-z][A-Za-z0-9/+_\-]*[0-9])[A-Za-z0-9/+_\-]*)"),
     r"\1\2[redacted]"),
    # The other direction, and the one the keyword rule could never reach: a
    # credential-shaped literal with nothing in front of it. Measured on an
    # adopter's own engagement sentence,
    # `1234567:synthetic-not-a-real-bot-token` survived every pattern above,
    # because they all require a keyword immediately before the value.
    #
    # Narrow on purpose: digits, a colon, then a long run with a hyphen or an
    # underscore in it. A bare hex string or a UUID is not matched -- those are
    # commit hashes and ids, which this ledger is full of, and blanking them
    # would make the record unreadable to buy nothing.
    (re.compile(r"\b(\d{6,}):([A-Za-z0-9_\-]{20,})\b"), r"\1:[redacted]"),
)


#: A field name that says its value is a credential. `redact` reads name and
#: value together in one string; once the JSON is parsed they are two, so the
#: name has to be asked separately or `{"secret": "AKIA..."}` walks through.
_SECRET_KEY = re.compile(
    r"(?i)\b(api[_-]?key|secret|token|password|passwd|credential|private[_-]?key)")


def redact_json(value, key=None, root=None):
    """`redact`, applied to every string inside a parsed `--out` payload.

    Public, unlike the regex tables beside it: `runner` calls this one on the
    checker `--out` payload, and importing another module's underscore name is
    what `LINT-PRIVATE-IMPORT` is about.

    On the raw text it could eat a closing quote and turn valid JSON into a
    parse error, so a checker whose findings mention the word `password` would
    be recorded as broken rather than answered. Parsing first cannot change the
    shape -- and the key is carried down, because splitting `"secret": "AKIA..."`
    into two values leaves neither of them looking like a credential.

    `root` is carried down for the same reason the key is. It had no parameter
    at all, so `redact(value)` ran with none and the adopter-declared credential
    families -- the half of this table a repo supplies about itself -- were
    applied to a checker's stdout and never to its `--out` payload, which lands
    in the same append-only row. `ledger._redact`'s own docstring already said
    "`runner.py` and `derive.py` already thread the root through their own
    calls"; that was true of two of the three calls in `runner.py`.
    """
    if isinstance(value, str):
        if key is not None and _SECRET_KEY.search(str(key)):
            return "[redacted]"
        return redact(value, root)
    if isinstance(value, list):
        return [redact_json(v, key, root) for v in value]
    if isinstance(value, dict):
        return {k: redact_json(v, k, root) for k, v in value.items()}
    return value


def redact(text: str, root=None) -> str:
    """Blank credential shapes before they enter a table nothing can delete from.

    A checker's stdout goes into the ledger verbatim, the ledger is append-only,
    and checkers are written by an LLM. `secret_scan.py` already prints its
    matches redacted -- but that is one checker being careful, and the argument
    this project makes about anything an agent writes is that eventually one of
    them will not be. A checker that prints its `--facts` input, or a config it
    just read, leaks without ever meaning to.

    A floor, not a boundary: it catches shapes, and something unusual gets
    through. The alternative is nothing at the kernel layer at all.
    """
    if not text:
        return text
    for pat, replacement in _REDACTIONS:
        text = pat.sub(replacement, text)
    # Then the detection table. `_REDACTIONS` carried five vendor prefixes
    # while `kernel/analysis/secret_patterns.json` ships twelve patterns for the
    # same question, and the five did not include `AKIA`, `shpat_`, `shpss_`,
    # `sk_live_` or a PEM block. This function is the last thing between a
    # checker printing a credential and an append-only table that is exported to
    # a committed file, so the table guarding the durable artefact was the
    # narrower of the two.
    #
    # After `_REDACTIONS`, not before: its URI rule keeps the host --
    # `postgres://u:[redacted]@db.prod/app` -- and the wider table would have
    # eaten the whole authority, which is a worse thing to read in a ledger.
    # Best-effort: this is a floor at the
    # kernel layer and a floor that raises an exception is worse than a narrow
    # one. `secret_patterns` is pure analysis with no I/O once imported.
    #
    # Best-effort is not the same as best-effort-in-silence, and one
    # `except Exception: pass` around the whole block made them the same thing.
    # Two different failures were being caught, one of them recoverable and
    # neither of them said out loud:
    #
    #   the module will not import -- nothing to fall back to but the three
    #   regexes above, which is the narrowest this function has;
    #   `.v4/secret_patterns.json` will not read -- and `table_for` raises on
    #   that *by contract*: "a table that does not parse is an error, never an
    #   empty one: reading it as empty disarms the scan that the file exists to
    #   widen". Catching it and dropping to five vendor prefixes is reading it
    #   as empty by another route, one layer down from where it was refused.
    #
    # So the second one falls back to `PATTERNS` -- the shipped twelve, which
    # is what a caller with no `root` already gets and is never narrower than
    # what the swallow produced -- and both of them say so on stderr. The text
    # still goes out: raising here would lose the whole attempt row rather than
    # some credential family, and losing the record is not the safer failure.
    # What must not happen is that a repo whose table is malformed writes into
    # an append-only, committed export with nothing anywhere saying the last
    # filter ran narrow.
    try:
        from . import secret_patterns as _sp
    except Exception as exc:                                    # noqa: BLE001
        print(f"v4: kernel.analysis.secret_patterns did not import "
              f"({type(exc).__name__}: {exc}), so the only thing blanking "
              f"credentials in this text was the three shapes in _REDACTIONS. "
              f"Whatever is written from here was filtered by less than this "
              f"repo's own table.", file=sys.stderr)
        return text
    # The repo's table when the caller knows which repo, the shipped one
    # otherwise. `checkers/secret_scan.py` unions `.v4/secret_patterns.json`
    # in and this did not, so the four families an adopter declares -- measured
    # on one: a social graph token, a chat-platform bot token, a
    # 32-hex `api_hash`, `whsec_` -- were detected by the checker and left
    # in the clear by the thing that writes to the append-only table.
    table = _sp.PATTERNS
    if root:
        try:
            table = _sp.table_for(root)
        except Exception as exc:                                # noqa: BLE001
            from . import tables as _tables
            own = _tables.own(root, _sp.__file__)
            print(f"v4: {own} did not read ({type(exc).__name__}: {exc}), so "
                  f"this text was blanked by the {len(table)} shipped families "
                  f"and by none of the ones this repo added -- on its way into "
                  f"a table nothing can delete from.", file=sys.stderr)
    for entry in table:
        # `KIND_URI` is the one shape `_REDACTIONS` above already owns, and
        # owns better: it keeps the host. Running it again matches the
        # `[redacted]` that pass just wrote and eats the authority with it.
        if entry.kind == _sp.KIND_URI:
            continue
        text = entry.regex.sub("[redacted]", text)
    return text
