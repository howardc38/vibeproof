"""The four documents that tell a session what will happen to it, checked.

    python3 -m unittest tests.test_a_brief_names_a_mechanism_that_exists -v

`a9ae5fb` (2026-08-24) removed eleven claim kinds and the checkers and detectors
that answered them. Three days later a lens sweep raised six findings that were
one fact: six documents and one module still described the removed half in the
present tense. The worst of them told a monitor session that an overdue sweep
holds `v4 ship`, naming two files that are not in the repo, while `v4 sweep`
reported DUE and nothing anywhere raised a claim.

Repairing six sentences is repairing six instances. The class is: **a document
that names a mechanism, and nothing asks whether the mechanism is there.**
`checkers/spec_coverage.py` asks exactly that -- of `docs/SPEC.md` and of no
other document. Its `user_facing_docs` reads `docs/*.md`, `CLAUDE.md` and
`.github/monitor/*.md`, but only for the two questions about `v4` invocations;
the path check and the kind check run against the spec text alone. So the four
briefs a session is actually handed were unchecked, and `docs/FACTS.md` was
checked for the commands it prints and not for the kinds it names.

This is that check, for those documents. Three questions:

  1. a path it names in code font is a path this repo has;
  2. a hyphen-shaped name in code font is a claim kind this repo registers, an
     agent or command this repo ships, or one of a short list of names that are
     none of those and say why;
  3. the two documents that dispatch reviewers do not write down how many there
     are, because `.v4/lenses/` decides that and it moved twice in one week
     while four hard-coded copies of `11` did not.

**Where this does not reach**, stated rather than left to be found: it reads
code font only, so a mechanism named in plain prose is invisible to it, and
question 3 reads a number sitting next to one of four nouns -- `11 行` and
`開晒 11 個` are the same claim wearing a different noun and this does not see
them. The right home for questions 1 and 2 is `checkers/spec_coverage.py`, whose
`user_facing_docs` already names most of this set; that file is a protected path
and re-registering it changes a `checker_sha` every open task is judged against,
so it is not this task's to move. Written down here so the next reader has the
argument rather than the conclusion.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The documents a session is handed, or told to paste, or sent to for the
#: shape of the facts table.  `docs/SPEC.md` is deliberately absent: it has a
#: checker, and a second opinion about the same file is a second answer.
BRIEFS = (
    ".github/monitor/PROMPT.md",
    ".github/monitor/SCOPE.md",
    ".claude/commands/sweep.md",
    "docs/README.md",
    "docs/FACTS.md",
    # The four role definitions and the two commands that instantiate them.
    # This module's docstring says "the four documents that tell a session what
    # will happen to it", and none of the four a role is actually built from was
    # in the tuple. `spec_coverage.user_facing_docs` does read them, for
    # `launcher_is_reachable` and `flags_resolve` -- and `flags_resolve` says in
    # its own docstring that it is "shape only: the flag has to be declared for
    # that subcommand", so a role prompt could name a command that parses and
    # still cannot run. One did: `reviewer.md` line 10 carried
    # `--task $V4_TASK` for a role that has no task by design.
    ".claude/agents/reviewer.md",
    ".claude/agents/worker.md",
    ".claude/agents/task-splitter.md",
    ".claude/agents/checker-author.md",
    ".claude/commands/run.md",
    ".claude/commands/wave.md",
)

#: The two that tell an orchestrator how many reviewers to open.
DISPATCHERS = (".claude/commands/sweep.md", ".github/monitor/PROMPT.md")

CODE_SPAN = re.compile(r"`([^`\n]+)`")

#: `sweep-current`, `route-auth`, `dal-write` -- and `external-write`, which is
#: still here. Underscores are excluded on purpose: `auth_decision` is a facts
#: key, not a kind, and the two are spelled differently everywhere.
KIND_SHAPED = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$")

#: A number this repo can settle, next to the noun it counts.
LENS_COUNT = re.compile(
    r"\b(\d+|[Ee]leven|[Tt]welve|[Tt]hirteen)\s*(?:個\s*)?"
    r"(reviewers?|lens|lenses|written checks)\b")

#: A line carrying a year is a record of a measurement, not a claim about the
#: tree now. The same distinction `checkers/spec_coverage.py` draws with `當時`.
A_DATED_LINE = re.compile(r"\b20\d\d\b")

#: Hyphen-shaped names in these documents that are not claim kinds, each with
#: the reason it is not one. A name that is none of these and not registered is
#: a document describing a mechanism this repo does not have -- which is the
#: whole failure -- so the way past this test is to add a line here and mean it.
NOT_A_KIND = {
    "repo-review": "the standing task findings attach to, not a kind",
    "google-genai": "a PyPI package, named in a facts-table example row",
    "facts-current": "named in PROMPT.md in order to say no such kind exists",
}


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _code_spans(rel: str):
    return CODE_SPAN.findall(_read(rel))


def _registered_kinds() -> set:
    return set(json.loads((ROOT / ".v4/claim_kinds.json").read_text()))


def _shipped_roles() -> set:
    """Agents and commands, by file stem.  `checker-author` is one of these."""
    out = set()
    for sub in ("agents", "commands"):
        d = ROOT / ".claude" / sub
        if d.is_dir():
            out |= {p.stem for p in d.glob("*.md")}
    return out


def _lens_slugs() -> set:
    """The reviewer lenses, by file stem.

    A fourth vocabulary this repo has and this check did not know: `v4 review
    lens` reads `.v4/lenses/`, `review add --lens` refuses a slug that is not
    there, and the slug reaches a claim id. Found by widening `BRIEFS` to the
    role prompts: `reviewer.md` names `near-miss`, which is a lens here and was
    read as a name this system does not have.
    """
    d = ROOT / ".v4" / "lenses"
    return {p.stem for p in d.glob("*.json")} if d.is_dir() else set()


def _top_level_dirs() -> set:
    return {p.name for p in ROOT.iterdir() if p.is_dir()}


class EveryPathTheseBriefsNameIsHere(unittest.TestCase):
    """SCOPE.md named `detectors/always_sweep.py` and `checkers/sweep_current.py`
    for three days after both were deleted, in the paragraph explaining what
    holds `v4 ship`."""

    def test_it(self):
        tops = _top_level_dirs()
        missing = []
        for rel in BRIEFS:
            for span in _code_spans(rel):
                if "/" not in span or "*" in span or "<" in span or " " in span:
                    continue
                # `kernel/sweep.py::due` names a file and a symbol in it.
                # `checkers/design_pins.py` owns the symbol half and answers it
                # for every `<!-- pinned: -->` in the repo; asking it here as
                # well would be a second answer to one question, and asking it
                # badly -- this reads no Python. The file half is this one's.
                span = span.split("::", 1)[0]
                head = span.split("/", 1)[0]
                # A path whose first segment is not a directory here is about
                # another repo -- `docs/FACTS.md` quotes the reference adopter's
                # `core/integrations/blobstore.py`, and this repo cannot answer for it.
                if head not in tops:
                    continue
                # Adopter fixture roots are installed elsewhere. Validate the
                # mapping against its producer instead of inventing an empty
                # duplicate directory in this framework checkout.
                from kernel import install
                if span.rstrip("/") == install.ADOPTER_FIXTURES:
                    registry = json.loads((ROOT / ".v4/checkers.json").read_text())
                    fixtures = [e["fixtures"] for e in registry.values() if e.get("fixtures")]
                    self.assertTrue(fixtures)
                    self.assertTrue(all((ROOT / f).is_dir() and
                                        install.fixture_dest(f).startswith(install.ADOPTER_FIXTURES + "/")
                                        for f in fixtures))
                    continue
                if not (ROOT / span).exists():
                    missing.append(f"{rel} names {span}, which is not here")
        self.assertEqual(missing, [], "\n".join(missing))


class EveryKindTheseBriefsNameIsRegistered(unittest.TestCase):
    """`sweep-current` in three of them, `route-auth` in two, `dal-write` in one
    -- every one of them cut in the same commit, every one still written as a
    thing that runs."""

    def test_it(self):
        allowed = (_registered_kinds() | _shipped_roles() | _lens_slugs()
                   | set(NOT_A_KIND))
        unknown = []
        for rel in BRIEFS:
            for span in _code_spans(rel):
                if KIND_SHAPED.match(span) and span not in allowed:
                    unknown.append(
                        f"{rel} writes `{span}` as a name this system has, and "
                        f"it is not a registered kind, a lens, an agent, or a "
                        f"command")
        self.assertEqual(unknown, [], "\n".join(unknown))

    def test_the_exception_list_is_not_a_dumping_ground(self):
        """Every reason is a sentence, and a name that became a kind leaves."""
        kinds = _registered_kinds()
        for name, why in NOT_A_KIND.items():
            self.assertNotIn(name, kinds,
                             f"{name} is a registered kind now; take it out of "
                             f"NOT_A_KIND rather than exempting a real one")
            self.assertGreater(len(why), 20, f"{name} has no reason")


class TheDispatchersDoNotWriteDownHowManyLensesThereAre(unittest.TestCase):
    """The count lived in four places in `sweep.md` and once more in
    `PROMPT.md`. `request-fidelity` (2026-08-25) and `near-miss` (2026-08-26)
    were added to `.v4/lenses/` and appeared in none of the five."""

    def test_it(self):
        hard_coded = []
        for rel in DISPATCHERS:
            for i, line in enumerate(_read(rel).splitlines(), 1):
                if A_DATED_LINE.search(line):
                    continue
                for n, noun in LENS_COUNT.findall(line):
                    hard_coded.append(
                        f"{rel}:{i} writes '{n} {noun}'. `.v4/lenses/` decides "
                        f"that and `v4 sweep` prints it; a copy here goes stale "
                        f"silently. Say it without the number, or put the date "
                        f"on the line if it is a record of one run.")
        self.assertEqual(hard_coded, [], "\n".join(hard_coded))

    def test_and_the_tree_still_has_lenses_to_count(self):
        """The control: a rule against writing the number down is worth nothing
        if there is nothing to have written down."""
        self.assertTrue(sorted((ROOT / ".v4" / "lenses").glob("*.json")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
