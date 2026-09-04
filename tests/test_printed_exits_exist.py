"""Every escape a checker prints has to be one the CLI accepts.

    python3 -m unittest tests.test_printed_exits_exist -v

Found by trying to use one.  ``checkers/sweep_current.py`` printed

    v4 risk accept --claim <id> --kind sweep-current --scope repo --why '…'

and ``--kind`` takes four fixed values, none of them a claim kind.  Running it
gets an argparse usage error, so the gate's stated way out did not exist: a
worker follows the instruction, is refused by the tool the instruction named,
and the only remaining ending is the one nobody wrote down.

That is the shape the doctrine already names -- *一個在這個 repo 裡跑不起來的閘，
比沒有閘更差* -- one level in.  A gate that runs and prints an exit that does not
is the same failure wearing the gate's clothes.

Two checks, both mechanical:

* Any ``--kind`` a checker prints beside ``risk accept`` is in ``risk.KINDS``.
* Any of those printed with ``--scope repo`` is in ``risk.REPO_SCOPABLE``.

Placeholders in angle brackets are skipped -- ``--kind <kind>`` is telling the
reader to substitute, not naming a value.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from kernel import risk  # noqa: E402

SOURCES = sorted((ROOT / "checkers").glob("*.py")) + \
          sorted((ROOT / "detectors").glob("*.py")) + \
          sorted((ROOT / "kernel").glob("*.py"))

#: `--kind X` and `--scope repo`, matched inside one printed invocation rather
#: than anywhere in the file.  Scoped because `kernel/cli.py` is 1,700 lines and
#: carries `--kind` for `engage`, `review add` and `cover` as well: an unscoped
#: version flagged `v4 engage --task X --kind Y` and a sentence reading
#: "--kind needs --task", neither of which is a risk signature.  A check that
#: cries wolf about prose is a check somebody switches off.
#: Everything after `--kind` up to whitespace, quote or backslash -- not just
#: what looks like a literal. The first version required `[A-Za-z_]` to start,
#: so `--kind {KIND}` matched nothing at all and
#: `checkers/facts_coverage.py` shipped `--kind facts-coverage` under a
#: perfectly green run of this file. A scanner that skips what it cannot read
#: reports on the subset it happened to understand; this one resolves the
#: f-string placeholder against the module's own constants, and anything still
#: unresolved is a finding rather than a pass.
KIND = re.compile(r"--kind[\s\\'\"]+([^\s\\'\"]+)")
PLACEHOLDER = re.compile(r"^\{(\w+)\}$")
SCOPE_REPO = re.compile(r"--scope[\s\\'\"]+repo")

#: How far past `risk accept` its own flags can be.  The invocations this checks
#: are wrapped across at most four printed lines.
WINDOW = 320


def _constants(text: str) -> dict:
    """Module-level `NAME = "literal"` bindings, for resolving `{NAME}`."""
    return {m.group(1): m.group(2) for m in
            re.finditer(r'^([A-Z_][A-Z0-9_]*)\s*=\s*"([^"]+)"', text, re.M)}


def printed_kinds(text: str):
    """Every `--kind` in a `risk accept` invocation: (line, value, span).

    `<placeholder>` is skipped -- angle brackets tell the reader to substitute.
    `{NAME}` is resolved, because it tells *Python* to substitute and the
    reader sees the result. A value that resolves to nothing is returned as it
    was written, so the caller reports it rather than passing over it.
    """
    consts = _constants(text)
    out = []
    for m in re.finditer(r"risk accept", text):
        span = text[m.end():m.end() + WINDOW]
        for k in KIND.finditer(span):
            value = k.group(1)
            if value.startswith("<"):
                continue
            ph = PLACEHOLDER.match(value)
            if ph:
                value = consts.get(ph.group(1), value)
            line_no = text.count("\n", 0, m.end() + k.start()) + 1
            out.append((line_no, value, span))
    return out


#: A sentence about the terminal check, as opposed to a name for it.
#: `was_tty` is a ledger column, `"| tty="` is a format string, and
#: `"--no-tty-check"` is the flag's own name in `add_argument` -- none of them
#: is telling anybody anything. Six words is what separates the two, and the
#: literals rather than the file text is what keeps `empty` from matching `pty`
#: and a comment from counting as something a reader is shown.
_TTY_PROSE = re.compile(r"\btty\b|not a terminal|--no-tty-check")


def _texts(tree):
    """Every string in `tree`, as the reader of it sees it: `(line, text)`.

    An f-string is one `JoinedStr` holding several `Constant` fragments, and
    any plain literal written beside one is folded into the same node -- so
    walking `Constant` chops a printed message into pieces at every `{...}`,
    and a rule about the message quietly becomes a rule about whichever piece
    happens to be longest.

    Measured while this was being written: `risk.accept`'s terminal refusal was
    a single `Constant` for as long as it was all plain text, and became four
    fragments the moment three of them interpolated a route name. Only the
    first fragment carried `--no-tty-check`, so the sweep below went on passing
    -- on a quarter of the sentence it was reading.

    `ast.walk` is breadth-first, so a `JoinedStr` is always seen before its own
    fragments and can claim them.
    """
    import ast
    inside = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for part in ast.walk(node):
                inside.add(id(part))
            yield node.lineno, "".join(
                v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str))
        elif (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and id(node) not in inside):
            yield node.lineno, node.value


def _shown(tree):
    """The subset of `_texts` a reader is actually handed: `print` and `raise`.

    A module docstring explaining the check is written for somebody reading the
    source; a refusal is written for somebody who has just been stopped by it,
    and only the second one is a signpost that can send them the wrong way. The
    rule that every route is named applies to signposts, because that is where
    naming a subset costs something -- an adopter read one and abandoned a task
    whose fix was already written.
    """
    import ast
    out, seen = [], set()
    for node in ast.walk(tree):
        is_signpost = (isinstance(node, ast.Raise)
                       or (isinstance(node, ast.Call)
                           and isinstance(node.func, ast.Name)
                           and node.func.id == "print"))
        if not is_signpost:
            continue
        for line, text in _texts(node):
            if (line, text) not in seen:
                seen.add((line, text))
                out.append((line, text))
    return out


def _parse(path):
    import ast
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError):
        return None


def _is_prose(text):
    return bool(_TTY_PROSE.search(text)) and len(text.split()) >= 6


def _names(text, token):
    """Is this route named here.

    A flag is matched literally; `pty` is matched on word boundaries, because
    the population it is looked for in is full of `empty` and `--no-tty-check`
    contains neither. The comment above `_TTY_PROSE` names the same trap.
    """
    if token.startswith("--"):
        return token in text
    return re.search(rf"\b{re.escape(token)}\b", text) is not None


def _tty_prose(path):
    """`(line, text)` for every string in `path` that explains the check."""
    tree = _parse(path)
    if tree is None:
        return
    for line, text in _texts(tree):
        if _is_prose(text):
            yield line, text


def _tty_signposts(path):
    """`(line, text)` for every printed or raised text that explains the check."""
    tree = _parse(path)
    if tree is None:
        return
    for line, text in _shown(tree):
        if _is_prose(text):
            yield line, text


class PrintedRiskKindsAreReal(unittest.TestCase):
    def test_every_printed_kind_is_one_risk_accept_takes(self):
        bad = []
        for path in SOURCES:
            text = path.read_text(encoding="utf-8", errors="replace")
            if "risk accept" not in text:
                continue
            for line_no, value, _span in printed_kinds(text):
                if value not in risk.KINDS:
                    bad.append(f"{path.relative_to(ROOT)}:{line_no} "
                               f"prints --kind {value}, and risk.KINDS is "
                               f"{risk.KINDS}")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_a_text_that_lists_the_ways_past_the_check_lists_all_of_them(self):
        """`risk.TTY_ROUTES` is the list; a sentence is not.

        `checkers/scope.py` told a worker "the two ways past it are not equal"
        and named `--no-tty-check` and a pty, on 2026-08-27, the day after
        `--as-monitor` became the third and waived the same check. Two of three
        is worse than none: the reader believes they have seen the set.

        Narrow on purpose. It fires on a text that *enumerates* -- two or more
        routes named -- because that is the text making a claim about the whole
        set. A refusal that names one route as the way forward is pointing, not
        listing, and `test_every_place_that_mentions_a_tty_also_names_the_flag`
        already keeps that one honest. An obligation aimed at enumerations that
        grew into "name every flag in every sentence" would be the blanket this
        project's own rules refuse.

        Signposts only -- `print` and `raise`. A module docstring is read by
        somebody working on the code, who has the constant in front of them; a
        refusal is read by somebody who has just been stopped and is looking
        for the way out.
        """
        bad = []
        for path in SOURCES:
            for lineno, text in _tty_signposts(path):
                named = [tok for tok, _ in risk.TTY_ROUTES if _names(text, tok)]
                missing = [tok for tok, _ in risk.TTY_ROUTES
                           if not _names(text, tok)]
                if len(named) >= 2 and missing:
                    bad.append(
                        f"{path.relative_to(ROOT)}:{lineno} lists "
                        f"{len(named)} of {len(risk.TTY_ROUTES)} ways past the "
                        f"terminal check and never names {', '.join(missing)}: "
                        f"{' '.join(text.split())[:90]}")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_every_place_that_mentions_a_tty_also_names_the_flag(self):
        """Two ways past the terminal check, and they record opposite things.

        `--no-tty-check` writes `signed_by: agent` and a note saying `who` came
        from git config; a pty makes `isatty` true, so the record says `person`
        for a signature no person gave. A text that names the pty and not the
        flag points the reader at the one that makes the record lie.

        Swept rather than pinned to a file, because it was pinned to a file
        once. `kernel/risk.py` was repaired on 2026-08-26 and
        `checkers/scope.py:153` -- "That signature needs a real tty, so a person
        runs it" -- was found the same day, after that task had gone terminal.
        Measured on the adopter: a worker read the first text, concluded a human
        at a terminal was the only route, and abandoned a task whose fix was
        already written; the adopter hit the second text that afternoon.
        """
        bad = []
        for path in SOURCES:
            for lineno, text in _tty_prose(path):
                if "--no-tty-check" not in text:
                    bad.append(f"{path.relative_to(ROOT)}:{lineno} explains the "
                               f"terminal check and never names --no-tty-check: "
                               f"{' '.join(text.split())[:90]}")
        self.assertEqual(bad, [], "\n".join(bad))

    def test_a_kind_printed_with_scope_repo_is_repo_scopable(self):
        bad = []
        for path in SOURCES:
            text = path.read_text(encoding="utf-8", errors="replace")
            if "risk accept" not in text:
                continue
            for line_no, value, span in printed_kinds(text):
                if value in risk.KINDS and value not in risk.REPO_SCOPABLE \
                        and SCOPE_REPO.search(span):
                    bad.append(f"{path.relative_to(ROOT)}:{line_no} "
                               f"prints --kind {value} with --scope repo, and "
                               f"risk.REPO_SCOPABLE is {risk.REPO_SCOPABLE}")
        self.assertEqual(bad, [], "\n".join(bad))


class TheScopeRepoHalfIsExercised(unittest.TestCase):
    """`SCOPE_REPO` was mutated to a pattern that never matches and the suite
    stayed green: no case in this file required it to fire. A matcher nothing
    exercises is the same as no matcher."""

    def test_a_non_repo_scopable_kind_with_scope_repo_is_flagged(self):
        text = ("risk accept --claim <id> --kind baseline_raise "
                "--scope repo --why '…'")
        found = [(v, span) for _, v, span in printed_kinds(text)]
        self.assertEqual([v for v, _ in found], ["baseline_raise"])
        self.assertNotIn("baseline_raise", risk.REPO_SCOPABLE)
        self.assertTrue(SCOPE_REPO.search(found[0][1]),
                        "SCOPE_REPO must match inside the invocation span")

    def test_a_repo_scopable_kind_with_scope_repo_is_not_flagged(self):
        text = ("risk accept --claim <id> --kind unprovable "
                "--scope repo --why '…'")
        v = [v for _, v, _ in printed_kinds(text)]
        self.assertEqual(v, ["unprovable"])
        self.assertIn("unprovable", risk.REPO_SCOPABLE)

    def test_scope_repo_does_not_match_a_task_scoped_invocation(self):
        text = "risk accept --claim <id> --kind baseline_raise --why '…'"
        spans = [span for _, _, span in printed_kinds(text)]
        self.assertTrue(spans)
        self.assertIsNone(SCOPE_REPO.search(spans[0]))


class TheCheckWouldHaveCaughtIt(unittest.TestCase):
    def test_the_original_defect_is_detected(self):
        """The string that shipped, run through the same check."""
        shipped = ("  or sign     `v4 risk accept --claim <id> "
                   "--kind sweep-current --scope repo --why '…'`")
        found = [v for _, v, _ in printed_kinds(f"risk accept\n{shipped}")]
        self.assertIn("sweep-current", found)
        self.assertNotIn("sweep-current", risk.KINDS)

    def test_the_tty_sentence_that_shipped_is_detected(self):
        """`checkers/scope.py:153` as it read until 2026-08-27.

        Written out rather than reached for on disk, because a red-green whose
        red is "whatever the file said before" stops being a red the moment
        somebody fixes the file. `kernel/risk.py` was repaired the day before
        and this sentence was found after that task had gone terminal -- one
        fact, two places, and the sweep exists because the first repair only
        reached one of them.
        """
        import ast as _ast
        import shutil
        import tempfile
        shipped = ('print("    That signature needs a real tty, '
                   'so a person runs it.")\n')
        tmp = Path(tempfile.mkdtemp()) / "was.py"
        tmp.write_text(shipped, encoding="utf-8")
        self.addCleanup(shutil.rmtree, tmp.parent, ignore_errors=True)
        prose = list(_tty_prose(tmp))
        self.assertTrue(prose, "the sentence was not seen as prose at all")
        self.assertTrue(all("--no-tty-check" not in t for _, t in prose),
                        "the shipped sentence would have passed the sweep")
        _ast.parse(shipped)          # and it is the literal a reader was shown

    def test_the_two_of_three_sentence_that_shipped_is_detected(self):
        """`checkers/scope.py:153` as it read until 2026-08-27, the other way.

        It names `--no-tty-check`, so the sweep above passed it. What it got
        wrong is the count: it says "the two ways past it" on a day when there
        were three, and a reader who has just been refused believes the list.
        """
        import shutil
        import tempfile
        shipped = ('print("    That refuses without a terminal, and the two "\n'
                   '      "ways past it are not equal: `--no-tty-check` records "\n'
                   '      "`signed_by: agent`, while a pty records `person` for "\n'
                   '      "a signature no person gave.")\n')
        tmp = Path(tempfile.mkdtemp()) / "was.py"
        tmp.write_text(shipped, encoding="utf-8")
        self.addCleanup(shutil.rmtree, tmp.parent, ignore_errors=True)
        found = list(_tty_signposts(tmp))
        self.assertTrue(found, "the sentence was not seen as a signpost at all")
        line, text = found[0]
        named = [tok for tok, _ in risk.TTY_ROUTES if _names(text, tok)]
        missing = [tok for tok, _ in risk.TTY_ROUTES if not _names(text, tok)]
        self.assertGreaterEqual(len(named), 2, "it does enumerate")
        self.assertEqual(missing, ["--as-monitor"],
                         "and the one it leaves out is the route added that day")

    def test_a_refusal_that_points_at_one_route_is_not_an_enumeration(self):
        """The other side of the same rule, which is what keeps it narrow.

        `risk.accept`'s origin refusal names `--no-tty-check` as the exit and
        nothing else, and it should not have to recite the pty -- the record a
        pty leaves is a lie, and reciting it there would be noise a reader has
        to step over. A rule that flagged this would be the blanket version.
        """
        import shutil
        import tempfile
        one = ('raise RefusedToSign("a monitor signs what a program raised, so "\n'
               '                    "whoever is answerable signs this one with "\n'
               '                    "`--no-tty-check` instead.")\n')
        tmp = Path(tempfile.mkdtemp()) / "one.py"
        tmp.write_text(one, encoding="utf-8")
        self.addCleanup(shutil.rmtree, tmp.parent, ignore_errors=True)
        found = list(_tty_signposts(tmp))
        self.assertTrue(found, "it is prose about the check")
        named = [tok for tok, _ in risk.TTY_ROUTES if _names(text := found[0][1], tok)]
        self.assertEqual(named, ["--no-tty-check"])
        self.assertNotIn("pty", text)

    def test_an_f_string_refusal_is_read_whole_rather_than_in_fragments(self):
        """What `_texts` exists for, as the shape that would have slipped past.

        Three routes, each interpolated from `risk`'s constants, so the message
        is one `JoinedStr` of four `Constant` fragments. Walking `Constant`
        sees four strings and grades the longest; only the first carries
        `--no-tty-check`, so a message naming two of three routes would have
        been read as a fragment naming one -- exempt from the enumeration rule
        by the very fragmentation that hid the problem.
        """
        import shutil
        import tempfile
        src = ('AGENT = "agent"\n'
               'PERSON = "person"\n'
               'raise RefusedToSign(\n'
               '    "stdin is not a terminal, so this is refused. Run it again "\n'
               '    "with `--no-tty-check` and it signs it "\n'
               '    f"`{AGENT}`, which says no person read this claim. "\n'
               '    f"Allocating a pty records `{PERSON}` for a signature "\n'
               '    "no person gave.")\n')
        tmp = Path(tempfile.mkdtemp()) / "frag.py"
        tmp.write_text(src, encoding="utf-8")
        self.addCleanup(shutil.rmtree, tmp.parent, ignore_errors=True)
        whole = [t for _, t in _tty_signposts(tmp)]
        self.assertEqual(len(whole), 1, "one message, not four fragments")
        self.assertIn("--no-tty-check", whole[0])
        self.assertIn("pty", whole[0])
        named = [tok for tok, _ in risk.TTY_ROUTES if _names(whole[0], tok)]
        self.assertEqual(named, ["--no-tty-check", "pty"],
                         "read whole, it enumerates two of three and is caught")


if __name__ == "__main__":
    unittest.main()
