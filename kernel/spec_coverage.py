"""Is every mechanism the spec describes actually there?  SPEC.md §0.

The question this answers is one nobody should have to take on trust: "did you
check all of it?" A person saying yes is worth nothing, because the last three
times somebody asked, the honest answer turned out to be no -- once because a
whole section had been left in the other document, once because a command the
spec describes in detail had never been written, and once because a table the
spec names had no code writing to it.

So the spec is read as a set of claims about the repo, and each is resolved:

    every `v4 <command>` it mentions        exists in the CLI, and back
    every path it names                     exists on disk
    every `x` claim it names                is in claim_kinds.json
    every registered checker and detector   is mentioned somewhere
    every section describing a mechanism    carries at least one pin

This list used to say "every checker and detector it names is registered, or is
on disk", which is the other direction and is not here. It matters, and on
2026-08-25 it cost something: SPEC named six checkers the trim had removed --
one of them described in the present tense, with its floor and its algorithm --
and nothing reported it. The claim kind line was overstated the same way: the
pattern is a backtick-quoted name followed by the word `claim`, so a
kind named any other way is invisible.

The missing direction was then built and measured rather than assumed, because
`dead_references` already has the technique that would carry it: `git log
--diff-filter=D` names every checker this project ever deleted, so no hardcoded
list is needed. Against SPEC at the commit before the repair it reported 30
lines; against the repaired SPEC, 29. **One signal in thirty**, because this
document's job includes explaining what was removed and why, and those
paragraphs name the same six on purpose. Filters for the historical markers
this file already honours (`當時`, `前身`, blockquote) and for table rows moved
it from 61 to 30 and no further.

A gate that fires 30 times to be right once is worse than the absence, so it is
absent, and this paragraph is the record of that -- not an omission somebody
should re-derive. What would be precise is the inverse (a checker removed and
never mentioned anywhere: four candidates today), and that is a different
question from the one this list used to claim.

That last one is the important one and the weakest. A pin says "this paragraph
is about that symbol, and the symbol is there". A section with no pin at all is
a section nobody has connected to anything -- which is exactly the shape of the
engagement layer that shipped with no rules in it, and of the four-layer
structure that stayed behind in the rationale.

It cannot tell you whether a paragraph is *correct*. It can tell you whether
anybody ever wired it up.

Exit: 0 everything resolves | 1 something does not | 4 no spec | >=5 broke.

Moved here from `checkers/spec_coverage.py` and not otherwise changed. It held
1052 of that file's 1344 lines -- 2.8x the next-largest checker -- so no
detector and no test could reach any of it except by spawning a process and
reading an exit code.

`kernel/` and not `kernel/analysis/`, which is where SPEC §12 step 1 sends a
judgement, and the reason is the layer declaration: `.v4/layers.json` allows
`analysis` no imports at all, and this judgement's inputs *are* the kernel --
`config.facts_path_for` (whose own docstring counts nine places that spelled
that glob differently, so re-deriving it here is the thing it exists to stop)
and the subject repo's `kernel.doctrine`, which
`layer_one_has_what_it_was_assigned` loads to read the DOCTRINE it assigns.
The declaration's comment records the one `analysis -> kernel` edge this repo
ever had as fixed rather than declared, so declaring a second one would be
answering an enforced rule with an exception. §12's substance -- the judgement
is importable, not behind argv and four exit codes -- holds here exactly as it
would one directory down.
"""
import ast
import json
import re
import subprocess
import sys
from pathlib import Path


from .analysis import spec_pins
# One owner for "which file is this repo's facts table". `facts_path_for`'s own
# docstring counts nine places that spelled that glob differently and two that
# took `sorted(...)[0]`, which judges a repo carrying a second table against
# another project's vocabulary. Imported at module scope, beside `spec_pins`,
# so that a tree where `kernel` is unreachable fails here and loudly rather than
# inside `_facts_rows`, where the honest empty result and the broken one look
# the same.
# The three registry paths, from the layer that owns them rather than spelled
# again here. `kernel/layout.py` exists for exactly this -- its docstring
# records that `config` declared these four and nothing imported them while
# twelve modules wrote `.v4/config.json` as a literal -- and this module was
# still opening all three registries by hand at six sites. `config`
# re-exports them, and this file already imported `config`, so the owner was
# one name away the whole time. `kernel/register.py` carried the same two and
# was repaired; these six were not, which is what "fix the fact everywhere it
# appears, not where it was reported" means when it is skipped.
from .config import CHECKERS, CLAIM_KINDS, DETECTORS, facts_path_for

CMD = re.compile(r"`v4 ([a-z][a-z-]*)")
#: A path this repo owns, named in backticks.
#:
#: `hooks`, `bin` and `tools` were absent for the whole of this checker's life,
#: so the promise `docs/README.md` makes -- rename something and SPEC fails a
#: check -- did not hold for three directories. SPEC names
#: `hooks/stop_gate.py`, `hooks/write_block.py`, `hooks/bash_guard.py`,
#: `bin/v4` and `tools/runtime_probe.sh`, and none of them was resolved: the
#: hook loop below runs the other direction only, disk -> spec.
PATH = re.compile(r"`((?:kernel|checkers|detectors|tests|docs|hooks|bin|tools"
                  r"|\.v4|\.github|\.claude|\.codex|\.agents)/[\w./*-]+)`")
#: One grammar, shared with `design_pins`. Counting every `<!-- pinned:`
#: here while that checker required a `::symbol` is why this file said 149
#: and it said 136 -- two answers to "how many pins does SPEC carry", and
#: `docs/README.md` printed a third.
PIN = spec_pins.MARKER
HEADING = re.compile(r"^##+ (.+)$", re.M)

#: Headings that are argument or inventory rather than mechanism, so a missing
#: pin says nothing.  Kept short and explicit: a long list here would turn this
#: checker into a way of not being checked.
NO_MECHANISM = (
    "讀呢份嘢之前", "未起", "明確唔起", "落手之前", "點加一個 checker",
    "完整走一次", "資料模型",
)


def cli_commands(root: Path):
    src = (root / "kernel" / "cli.py").read_text()
    return set(re.findall(r'sub\.add_parser\(\s*"([a-z][a-z-]*)"', src))


#: A row in a table of commands: a leading `|`, then `` `v4 <cmd>` `` alone in
#: the first cell. Not a mention -- a mention is any backtick anywhere, and a
#: sentence that happens to name a command is what let five commands sit outside
#: §13's table while the substring check stayed green.
COMMAND_ROW = re.compile(r"^\|\s*`v4 ([a-z][a-z-]*)`\s*\|", re.M)


def command_rows(spec_text: str):
    """The commands the spec keeps a table row for.  Empty when it keeps none.

    Conditional on the table existing, and that is deliberate rather than a
    softening. `engaged_kinds_explained` two hundred lines down learned the same
    thing the expensive way: demanding a list from a repo that owes none failed
    every green fixture, because an adopter's SPEC is a page and a half and has
    no inventory in it. A document with no command table cannot have an
    incomplete one, so for those the older question -- is it mentioned at all --
    is the whole of what can be asked. A document that has one has declared what
    the complete list is, and is held to it.
    """
    return set(COMMAND_ROW.findall(spec_text))


def sections(text):
    """[(heading, body)] -- so a missing pin can be reported where it belongs."""
    parts = HEADING.split(text)
    out = []
    for i in range(1, len(parts), 2):
        out.append((parts[i].strip(), parts[i + 1]))
    return out


def _contract_text(spec_text: str) -> str:
    """The spec minus its blockquotes.

    A blockquote in this document is a measurement or a note about what once
    went wrong, and those quote paths precisely because they did not exist --
    `config.py` looking for `.v4/facts.json` when the repo ships
    `.v4/facts.<name>.json` is the example. Reading those as promises turns the
    record of a bug into a bug.
    """
    return "\n".join(l for l in spec_text.splitlines() if not l.lstrip().startswith(">"))


#: Where `v4 install` puts the fixtures, and where they live here. `install`
#: renames `tests/fixtures/<case>` to `.v4/fixtures/<case>` (`fixture_dest`),
#: so the spec's adopter-side name for that directory is a true sentence about
#: a path this repo has under a different name. Spelled out rather than
#: imported: a checker reaching into `kernel/install.py` for one string is a
#: layer this file does not otherwise cross.
ADOPTER_FIXTURES = ".v4/fixtures"
FRAMEWORK_FIXTURES = "tests/fixtures"


#: Paths the spec names that `v4 ship` writes, not that the repo ships. Nothing
#: creates them at install time -- `kernel/install.py` mentions neither -- so a
#: repo that has installed and not yet shipped does not have them, and asking
#: "is this path here" of a file the framework has not had occasion to write
#: reads a fresh adopter as a spec that describes something absent. Measured on
#: a freshly exported tree: `v4 accept` at 6/7, on these two and nothing else.
#:
#: Narrow on purpose. The question this checker asks is whether the spec
#: describes a repo that could not be built from it; whether a repo that *has*
#: shipped is missing its export is a different question, and `.github/workflows`
#: already refuses that one.
WRITTEN_BY_SHIP = frozenset({
    ".v4/chain_head.json",
    ".v4/ledger_export.jsonl",
})


def _exists(root: Path, rel: str) -> bool:
    if (root / rel).exists():
        return True
    if rel.rstrip("/") in WRITTEN_BY_SHIP:
        return True
    clean = rel.rstrip("/")
    if clean == ADOPTER_FIXTURES or clean.startswith(ADOPTER_FIXTURES + "/"):
        # This used to be answered by accident. The branch below looks inside
        # each fixture case for a nested `.v4/`, and one case happened to carry
        # a `.v4/fixtures/` -- so a true sentence in the spec was confirmed by
        # an unrelated fixture, and deleting sixteen fixture directories in a
        # trim made the spec read as naming a path that is not there. It is
        # there; `install` is what changes its name.
        here = root / FRAMEWORK_FIXTURES / clean[len(ADOPTER_FIXTURES):].lstrip("/")
        return here.exists()
    # A fixture case carries its own .v4/, one per case, so the path is real
    # without being real at the repo root.
    if rel.startswith(".v4/"):
        return any((d / rel).exists() for d in (root / FRAMEWORK_FIXTURES).glob("*/*/*")
                   if d.is_dir())
    return False


def check(root: Path, spec_text: str):
    problems = []
    commands = cli_commands(root)
    spec_text = _contract_text(spec_text)

    for cmd in sorted(set(CMD.findall(spec_text))):
        if cmd not in commands:
            problems.append(
                f"the spec describes `v4 {cmd}` and the CLI has no such command. "
                f"Reading the spec and building it would produce a system missing "
                f"a command the spec explains how to use.")

    for rel in sorted(set(PATH.findall(spec_text))):
        if "*" in rel:
            continue
        if not _exists(root, rel):
            problems.append(f"the spec names {rel}, which is not there")

    kinds_path = root / ".v4" / "claim_kinds.json"
    if kinds_path.is_file():
        kinds = json.loads(kinds_path.read_text())
        for name in sorted(set(re.findall(r"`([a-z][a-z-]+)` claim", spec_text))):
            if name not in kinds and name not in ("review", "surface", "runtime"):
                problems.append(
                    f"the spec talks about a `{name}` claim and no such kind is "
                    f"registered")

    # Commands, hooks and engagement rules, in the direction that actually
    # rots. Seven things landed in one session -- a Stop hook, `v4 export`,
    # `v4 audit --events`, a settings template, a chain scheme bump, an
    # interpreter fix, a schema migration -- and five more engagement rules, and
    # the six documents mentioned none of them. Three of the four meta-checkers
    # passed, because every one of these was checked only in the direction
    # "the spec names it, so it must exist". Nothing asked the repo what it had
    # grown. That is the direction things move in: code gains a command, and a
    # document that never mentioned it cannot go stale.
    #
    # "Mentioned anywhere" was the whole of it, and anywhere is a large place.
    # §13 opens by declaring 「每一個都跑得,所以每一個都要喺度」 -- a promise
    # about a table -- and the table was short by `trend`, `foresee`, `remerge`,
    # `sweep` and `cover`, five of thirty-two, while this stayed green because
    # each of the five is discussed in some other section. A document that keeps
    # an inventory is judged against the inventory; the substring test cannot
    # tell an entry from a passing reference to one.
    table = command_rows(spec_text)
    for cmd in sorted(commands):
        if table:
            if cmd not in table:
                problems.append(
                    f"`v4 {cmd}` is a command anyone can run and the spec's "
                    f"command table has no row for it. That table says every "
                    f"command is in it, so a reader takes its absence as the "
                    f"command not existing.")
        elif f"`v4 {cmd}" not in spec_text:
            problems.append(
                f"`v4 {cmd}` is a command anyone can run and the spec never "
                f"mentions it. Someone building from this document produces a "
                f"system without it and has no way to know.")

    # Agents and commands, both directions. They were outside the contract for
    # the whole of this project's life -- the comparison table lived in the
    # rationale, so `spec-coverage` never governed them and a reader following
    # SPEC built a kernel with nothing to drive it.
    for kind, sub in (("agent", "agents"), ("command", "commands")):
        d = root / ".claude" / sub
        if not d.is_dir():
            continue
        for f in sorted(p.stem for p in d.glob("*.md")):
            # Matched in backticks. `run` on its own appears inside
            # `run-checker` and half a dozen sentences, so a bare substring
            # would report a command as documented because the word occurs.
            if f"`{f}`" not in spec_text and f"`/{f}`" not in spec_text:
                problems.append(
                    f".claude/{sub}/{f}.md is a {kind} anybody can invoke and the "
                    f"spec never mentions it")

    # The Codex projections must be documented and match the maintained prompt sources.
    if (root / ".codex/agents").is_dir():
        from . import hosts
        for rel, wanted in hosts.codex_assets(root).items():
            p = root / rel
            if not p.is_file() or p.read_text() != wanted:
                problems.append(f"{rel} differs from the maintained host projection; run tools/render_host_assets.py --write")
    hooks_dir = root / "hooks"
    if hooks_dir.is_dir():
        for h in sorted(p.name for p in hooks_dir.glob("*.py")
                        if not p.name.startswith("_")):
            if h not in spec_text and h.replace(".py", "") not in spec_text:
                problems.append(
                    f"hooks/{h} runs on every matching tool call and the spec "
                    f"never mentions it")

    kinds_path2 = root / ".v4" / "claim_kinds.json"
    if kinds_path2.is_file():
        kinds2 = json.loads(kinds_path2.read_text())
        engaged = sorted(k for k, v in kinds2.items() if v.get("engagement"))
        # One line naming them all satisfies this. The point is not ceremony:
        # engagement is the only layer that stops a worker before it starts, and
        # a reader who does not know which kinds carry it cannot predict when
        # the system will refuse to move.
        # Proximity is not enough: any kind name sits near the word
        # "engagement" somewhere, because §5 explains that widening triggers
        # one. Measured -- marking `scope` engaged passed a proximity check
        # untouched. So the spec carries one explicit list and the two sets
        # have to match exactly, in both directions.
        m = re.search(r"<!--\s*engaged-kinds\s*-->(.*?)(?:\n\n|\Z)",
                      spec_text, re.S)
        if not engaged:
            # A repo where nothing engages owes no list. Demanding one anyway
            # failed every green fixture, which is how this was found: the
            # registration gate refused the checker rather than let it through.
            pass
        elif not m:
            problems.append(
                "the spec has no <!-- engaged-kinds --> list. Engagement is the "
                "only layer that stops a worker before it starts, and a reader "
                "who cannot tell which kinds carry it cannot predict when the "
                "system refuses to move.")
        else:
            declared = set(re.findall(r"`([a-z][a-z-]*)`", m.group(1)))
            actual = set(engaged)
            for k in sorted(actual - declared):
                problems.append(
                    f"claim kind {k!r} blocks work until the worker writes a "
                    f"sentence, and the spec's engaged-kinds list leaves it out")
            for k in sorted(declared - actual):
                problems.append(
                    f"the spec lists {k!r} as engaging and it does not. A reader "
                    f"builds a stop the system never makes.")

    # The other direction, and the one that was wrong. §0 promises that
    # anything absent from the unbuilt list is built -- a promise about the
    # repo that only the repo can keep. It was false for six checkers, and a
    # reader following it would have built five fewer and believed that was
    # correct.
    reg_path = root / ".v4" / "checkers.json"
    if reg_path.is_file():
        for cid in sorted(json.loads(reg_path.read_text())):
            if f"`{cid}`" not in spec_text and cid not in spec_text:
                problems.append(
                    f"checker {cid!r} is registered and runs on every task, and the "
                    f"spec never mentions it. A reader cannot know it exists.")
    for name in sorted(p.name for p in (root / "detectors").glob("*.py")
                       if not p.name.startswith("_")):
        if name not in spec_text and name.replace("always_", "").replace(".py", "") \
                not in spec_text:
            problems.append(
                f"detectors/{name} runs on every derivation and the spec never "
                f"mentions it")

    for heading, body in sections(spec_text):
        if any(skip in heading for skip in NO_MECHANISM):
            continue
        if len(body.strip()) < 400:
            continue
        if not PIN.search(spec_pins.prose(body)):
            problems.append(
                f"§{heading} describes a mechanism over {len(body.strip())} "
                f"characters and pins nothing. Nothing checks that what it "
                f"describes exists.")

    problems += dead_references(root)
    problems += counted_claims(root)
    problems += flags_resolve(root)
    problems += unbuilt_list_is_honest(root)
    problems += lead_in_counts(root)
    problems += launcher_is_reachable(root)
    problems += sections_ascend(root)
    problems += engaged_kinds_explained(root, spec_text)
    problems += dispositions_add_up(root, spec_text)
    problems += gate_colours_named(root, spec_text)
    problems += layer_one_has_what_it_was_assigned(root, spec_text)
    return problems


#: The sentence that assigns a rule a home.  `層 ④ 加唔到` is the other half of
#: the same argument -- a rule cannot be layer 4, therefore it is layer 1 --
#: and both spellings of the layer have to be here or a translated §9 assigns
#: rules to a section this check cannot find.
LAYER_ONE = re.compile(r"層 ①|層 ④ 加唔到|layer\s*[①1]\b|layer\s*[④4]\s+cannot",
                       re.I)


def layer_one_has_what_it_was_assigned(root: Path, spec_text: str):
    """A rule the spec sends to layer 1 has to be in layer 1.

    §9 names two by way of example -- 「唔好用 stopgap」 and 「唔好 hardcode」 --
    and argues they cannot be layer 4 because neither has a detector to hang a
    claim on. Layer 1 contained neither. The document assigned them a home and
    the home did not have them, which nothing could see: `design_pins` resolves
    symbols, `counted_claims` settles numbers, and a rule named in prose as
    living somewhere is neither.

    Matched on the quoted phrase, because that is the form the spec uses when
    it assigns one: 「…」 inside a sentence about a layer.

    Both halves of that were Chinese -- the landmark `層 ①` and the corner
    brackets -- so an English §9 would assign a rule to layer 1 and this would
    read the section as saying nothing. `layer 1` and `layer ①` name the same
    section, and English marks a term the way these documents already do
    everywhere else: in backticks. The extraction differs because the languages
    differ: stripping `唔好` and `用` off 「唔好用 stopgap」 leaves exactly the
    technical term, and English has no equivalent trim -- `do not use a
    stopgap` is ASCII end to end, so the whole sentence would be looked up in
    the doctrine. A backticked single word is the form that carries the same
    information without the guess.
    """
    # An adopter repo has no kernel of its own and owes no doctrine. This one
    # does, and "could not import it" must not read as "nothing wrong" -- the
    # first version had no `sys.path` entry for the repo root, so the import
    # failed on every run and the check reported clean while both rules it was
    # written for were still missing.
    if not (root / "kernel" / "doctrine.py").is_file():
        return []
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from kernel import doctrine as _doc
    except ImportError as exc:
        return [f"kernel/doctrine.py exists and does not import ({exc}), so "
                f"nothing can tell whether layer 1 has what the spec assigned it."]
    corpus = "\n".join(r for _, rules in _doc.DOCTRINE for r in rules).lower()
    problems, said = [], set()
    for line in spec_text.splitlines():
        if not LAYER_ONE.search(line):
            continue
        # Corner brackets first, then backticks: a line carrying both is this
        # document's mixed prose, and the bracketed phrase is the one its
        # author wrote as the assignment.
        quotes = re.findall(r"「([^」]{4,40})」", line)
        if not quotes and not line.lstrip().startswith("|"):
            # A backticked term, and only a bare one. `kernel/cli.py` and
            # `risk.KINDS` are a path and a symbol; a rule is named in a word.
            #
            # And not in a table row. An assignment is a sentence: the cells of
            # a row are separate fields, and reading across them invents a
            # relationship nobody wrote. Measured -- §9's engaged-kinds table
            # names `test-expectation` in one column and cites `CLAUDE.md layer
            # ① Verification` as its source in another, and this read that as
            # the spec sending that kind to layer 1. The corner-bracket form
            # never had the problem, because a row citing a source carries no
            # quoted rule.
            quotes = [q for q in re.findall(r"`([^`]{4,40})`", line)
                      if q.isascii() and re.fullmatch(r"[A-Za-z][A-Za-z_-]*", q)]
        for quoted in quotes:
            if quoted in said:
                continue          # the same assignment restated is one assignment
            said.add(quoted)
            word = quoted.replace("唔好", "").replace("用", "").strip().lower()
            if not word or not word.isascii():
                continue          # only settle the ones named in a word code has
            if word not in corpus:
                problems.append(
                    f"the spec sends 「{quoted}」 to layer 1 and "
                    f"kernel/doctrine.py does not mention {word!r}. A rule with "
                    f"an assigned home and nothing in it is the shape §9 is "
                    f"about.")
    return problems


#: `三條規矩:` -- a count written as a Chinese numeral, introducing a list the
#: document itself then enumerates.  `counted_claims` cannot see these: it reads
#: Arabic digits and settles them against the repo, and these settle against the
#: next few lines instead.
#: `一` and `二` are left out on purpose. `一個` is the indefinite article far
#: more often than it is a count -- `可以有一個 fixture.json:` introduces one
#: example, not a list of one -- and this text writes the number two as `兩`.
CN = {"兩": 2, "三": 3, "四": 4, "五": 5, "六": 6,
      "七": 7, "八": 8, "九": 9, "十": 10}
#: `第三層` is an ordinal, not a count. Without the lookbehind, `## 第三層
#: —— lens sweep` reads as a claim that the block under it has three
#: things in it, which is a sentence nobody wrote.
LEAD_IN = re.compile(r"(?<!第)([兩三四五六七八九十])\s*(條|個|樣|種|格|步|層)")

#: The same lead-in, in English.  Measured before it existed: the identical
#: mistake -- a lead-in saying four above a block of three -- was caught in
#: Chinese and returned `[]` in English, in both the sentence form and the
#: heading form.  A document translated out of Chinese would have taken 343
#: lead-ins in `docs/SPEC.md` alone to zero while `v4 accept` went on printing
#: the same green, which is the one outcome worse than a red.
#:
#: `one` is left out for the reason `一` is: it is the indefinite article far
#: more often than it is a count.  Ordinals need no exclusion here -- English
#: spells them as different words, so there is no `第三層` to mistake.
EN = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
      "seven": 7, "eight": 8, "nine": 9, "ten": 10}
LEAD_IN_EN = re.compile(r"\b(" + "|".join(EN) + r")\b", re.I)

#: How close the number has to sit to the colon.  A number early in a long
#: sentence is usually about something other than the block underneath:
#: `三個 package 生態,而條 MUST 今日用四種方式被違反:` is followed by a table of
#: five measurements, and neither number is its length.
LEAD_IN_REACH = 14

#: The same limit for English, and it has to be counted in words rather than
#: characters.  Fourteen characters is two or three English words and would
#: refuse `four colours the gate enforces:` -- a lead-in about the block right
#: under it.  The unit is what differs; the question does not.  Six words is
#: where the two fixtures below sit on either side: a number three words from
#: the colon is about the block, and one ten words back is about the sentence.
LEAD_IN_REACH_EN_WORDS = 6

#: `<!-- count-exempt: 第二行係對照組 -->` -- the reason is required, because an
#: exemption with no reason is a line number in disguise.
EXEMPT = re.compile(r"<!--\s*count-exempt:\s*\S.*?-->")

#: Any HTML comment.  Stripped before a line is judged, never before the
#: exemption is looked for.
COMMENT = re.compile(r"<!--.*?-->")


#: A line that says it is about a past run rather than about the tree now.
#: `當時` and `前身` were the whole of it, and both are Chinese: a translated
#: document loses the exemption and starts reporting its own history as a
#: wrong count. The English forms are the ones this project's own documents
#: already use for the same act.
#: `V3` and `rev 1` were already here, spelled inline at two of the three
#: sites and missing from the third -- which is what a rule with no owner looks
#: like from the inside.
PAST = ("當時", "前身", "V3", "rev 1", "at the time", "the predecessor",
        "as measured", "measured on")


def _about_the_past(line: str) -> bool:
    low = line.lower()
    return any(mark in line or mark in low for mark in PAST)


def _block_size(lines, start):
    """How many items the block beginning at `start` has, or None.

    Only shapes where "how many" is unambiguous: a fenced block (non-blank
    lines), a table (body rows), a list (top-level items). Prose returns None,
    because a paragraph does not have a count to disagree with.
    """
    i = start
    # Blank lines, and the pin comments that sit between a heading and the
    # block it counts. A heading is a lead-in like any other, and every
    # heading in this document that counts something has its pins under it --
    # so stopping at the first `<!-- pinned: -->` meant the one place a count
    # is stated as a title was the one place nothing looked.
    while i < len(lines) and (not lines[i].strip()
                              or COMMENT.fullmatch(lines[i].strip())):
        i += 1
    if i >= len(lines):
        return None
    first = lines[i]
    if first.strip().startswith("```"):
        n, i = 0, i + 1
        while i < len(lines) and not lines[i].strip().startswith("```"):
            n += 1 if lines[i].strip() else 0
            i += 1
        return n if i < len(lines) else None          # unterminated: say nothing
    if first.lstrip().startswith("|"):
        rows = 0
        while i < len(lines) and lines[i].lstrip().startswith("|"):
            body = lines[i].strip().strip("|")
            # The `---|---` separator, and only that. An all-blank header row
            # (`| | |`, which this document uses for two-column tables with no
            # headings) is made of the same characters minus the dashes, and
            # counting it as a separator lost a row from every such table.
            if not ("-" in body and set(body) <= set("-: |")):
                rows += 1
            i += 1
        return max(rows - 1, 0)                      # minus the header row
    if re.match(r"\s*(\d+\.|[-*+])\s", first):
        items, indent = 0, len(first) - len(first.lstrip())
        while i < len(lines):
            if not lines[i].strip():
                if i + 1 < len(lines) and lines[i + 1].strip() and \
                        not re.match(r"\s*(\d+\.|[-*+])\s", lines[i + 1]):
                    break
                i += 1
                continue
            cur = len(lines[i]) - len(lines[i].lstrip())
            if cur < indent:
                break
            if cur == indent:
                if not re.match(r"\s*(\d+\.|[-*+])\s", lines[i]):
                    break
                items += 1
            i += 1
        return items
    return None


def lead_in_counts(root: Path):
    """`三條規矩:` followed by five rules.

    Three of these were live at once, and the worst was `四條全部要過:` above a
    block listing three -- the missing one being `bypass/`, the fixture colour
    that found thirteen real evasions the first time it ran. A reader building
    the registration gate from that section builds it without the colour that
    matters most, and nothing anywhere says so. The number was right when it was
    written and the block lost a line; the form stayed valid, which is why every
    other check here passes over it.
    """
    problems = []
    for doc in sorted((root / "docs").glob("*.md")):
        for i, said, got in lead_in_findings(doc.read_text(encoding="utf-8")):
            problems.append(
                f"docs/{doc.name}:{i} says {said} and the block below it has "
                f"{got}. One of them moved.")
    return problems


def lead_in_findings(text):
    """[(line_no, "四條", 3)] -- lead-ins whose block is a different length.

    One implementation rather than one here and one in the test: the two can
    disagree about what is even examined, and then the test passes on a walk
    nothing runs.
    """
    lines = text.splitlines()
    found = []
    for i, line in enumerate(lines):
        # Trailing HTML comments come off before the colon test. Leaving them on
        # meant a lead-in with any comment after it was never examined -- so the
        # exemption did not need a reason, or to be an exemption at all. The
        # marker is matched against the original line.
        bare = COMMENT.sub("", line).rstrip()
        # A heading is a lead-in without the colon. `### 六條機械判準,冇
        # reviewer` sat above a table of six for as long as there were seven,
        # and the colon rule is why nothing said so: a title is the one place a
        # count gets stated where the punctuation would look wrong.
        if not bare.endswith((":", ":")) and not bare.startswith("#"):
            continue
        if bare.lstrip().startswith(">") or _about_the_past(bare):
            continue
        # The escape hatch, and it costs a sentence. A block can legitimately
        # hold more rows than the lead-in counts -- one of them a control rather
        # than an instance -- and the alternative to saying so is a list of
        # exempt line numbers, which is the shape this project has watched fail.
        if EXEMPT.search(line):
            continue
        stem = bare.rstrip(":：")
        # `LEAD_IN_REACH` is about long sentences, and a heading is not one. It
        # measures from the numeral to the end of the line, so `### 七條機械判準,
        # 冇 reviewer` came to 15 and fell one character outside a limit written
        # for prose -- the count in the title of the section that states it.
        heading = bare.startswith("#")
        m = None
        for cand in LEAD_IN.finditer(stem):
            if heading or len(stem) - cand.end() <= LEAD_IN_REACH:
                m = cand
        said = CN[m.group(1)] if m else None
        if m is None:
            # English, where the same limit has to be counted in words. Only
            # when the Chinese pattern found nothing: a line carrying both is
            # this document's normal mixed prose, and the Chinese count is the
            # one its author wrote as the count.
            for cand in LEAD_IN_EN.finditer(stem):
                after = len(stem[cand.end():].split())
                if heading or after <= LEAD_IN_REACH_EN_WORDS:
                    m, said = cand, EN[cand.group(1).lower()]
        if m is None:
            continue
        got = _block_size(lines, i + 1)
        if got is not None and got and got != said:
            found.append((i + 1, m.group(0), got))
    return found


def sections_ascend(root: Path):
    """`## 12` before `## 11`.

    A reader following a `見 §11` lands on whatever is numbered 11, and for a
    while that was a CLI walkthrough sitting between two sections about adding
    checkers, because a section had been moved and its number had not.
    """
    problems = []
    for doc in sorted((root / "docs").glob("*.md")):
        nums, prev = [], None
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            m = re.match(r"^##\s+(\d+(?:\.\d+)?)[.\s]", line)
            if not m:
                continue
            cur = float(m.group(1))
            if prev is not None and cur < prev:
                problems.append(
                    f"docs/{doc.name}:{i} is §{m.group(1)} and comes after "
                    f"§{prev:g}. A cross-reference to a number resolves to "
                    f"whatever is under it, not to what was meant.")
            prev = cur
            nums.append(cur)
    return problems


def engaged_kinds_explained(root: Path, spec_text: str):
    """Every kind that blocks work says why a checker cannot settle it.

    The table under §9 is the argument for layer 4 existing at all: one row per
    engaged kind, naming the half no checker reaches. `bundle-secret` carried a
    rule and had no row, so it stopped work with an explanation nobody had
    written -- and the marker check passed, because the marker line was right.
    """
    try:
        kinds = json.loads((root / CLAIM_KINDS).read_text())
    except (OSError, json.JSONDecodeError):
        return []
    engaged = {k for k, v in kinds.items() if v.get("engagement")}
    rows = set(re.findall(r"^\|\s*`([a-z][a-z-]*)`\s*\|", spec_text, re.M))
    missing = sorted(engaged - rows)
    return [f"`{k}` carries an engagement rule and has no row in the table "
            f"saying what a checker cannot reach about it. It blocks work for a "
            f"reason nobody wrote down." for k in missing]


def dispositions_add_up(root: Path, spec_text: str):
    """The table of where 254 rules went, against where they went.

    The register is worth what the check behind it is worth, and the count table
    beside it had no check: it said 21 landed in `claim_kinds.json` when 25 did,
    and carried a row for four rules owed when every one of the 254 had a
    landing site -- a table proving nothing was lost, itself four over.
    """
    try:
        rules = json.loads(
            (root / ".v4/rule_dispositions.json").read_text())["rules"]
    except (OSError, json.JSONDecodeError, KeyError):
        return []
    real = {}
    for r in rules:
        real[r.get("landed")] = real.get(r.get("landed"), 0) + 1
    problems = []
    # Longest key wins: `.v4/lenses/` is a prefix of `.v4/lenses/prevention.json`,
    # so matching on containment alone reported the prevention row against the
    # count for the other eight.
    keys = sorted((k for k in real if k), key=len, reverse=True)
    for m in re.finditer(r"^\|([^|\n]*)\|\s*(\d+)\s*\|\s*$", spec_text, re.M):
        cell, n = m.group(1), int(m.group(2))
        landed = next((k for k in keys if k in cell), None)
        if landed and n != real[landed]:
            problems.append(
                f"the disposition table says {n} rules landed in {landed}, "
                f"and .v4/rule_dispositions.json has {real[landed]}.")
    total = sum(real.values())
    if f"| | **{total}** |" not in spec_text:
        problems.append(
            f"the disposition table does not total {total}, which is how many "
            f"rules the register holds. A table that proves nothing was lost "
            f"has to add up to what there is.")
    return problems


def gate_colours_named(root: Path, spec_text: str):
    """Every fixture colour the registration gate enforces is in the contract.

    `bypass/` was enforced by `kernel/register.py` and absent from the section
    describing the gate, so the contract and the gate disagreed about how many
    colours there are -- in the direction where a reader builds the weaker one.
    """
    src = (root / "kernel" / "register.py")
    if not src.is_file():
        return []
    text = src.read_text(encoding="utf-8")

    # AST, because the regex before this found nothing. It was
    # `^([A-Z_]+)\s*=\s*"([a-z]+)"`, and the gate declares its colours as one
    # tuple -- `RED, GREEN, BYPASS = "red", "green", "bypass"` -- which that
    # pattern cannot match. So the check written to catch "the gate enforces a
    # colour the contract does not name" had been reading zero colours since the
    # day it was written, and reported clean every time.
    #
    # Two questions, and only the second is about names: what the constants are
    # bound to, and which of them the gate actually loops over.
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [f"kernel/register.py will not parse; the fixture-colour "
                f"contract cannot be checked"]

    bound = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target, value = node.targets[0], node.value
        pairs = []
        if isinstance(target, ast.Tuple) and isinstance(value, ast.Tuple):
            pairs = list(zip(target.elts, value.elts))
        elif isinstance(target, ast.Name):
            pairs = [(target, value)]
        for t, v in pairs:
            if isinstance(t, ast.Name) and isinstance(v, ast.Constant) \
                    and isinstance(v.value, str):
                bound[t.id] = v.value

    # Every constant the gate loops over when it opens a fixture set. Derived,
    # not listed: a whitelist ends up bypassed somewhere nobody thought to list,
    # which SPEC §10 says in those words.
    enforced = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.For) and isinstance(node.iter, ast.Tuple):
            for elt in node.iter.elts:
                if isinstance(elt, ast.Tuple) and elt.elts \
                        and isinstance(elt.elts[0], ast.Name):
                    enforced.add(elt.elts[0].id)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id == "_fixture_cases":
            for a in node.args:
                if isinstance(a, ast.Name):
                    enforced.add(a.id)

    problems = []
    if not enforced:
        problems.append(
            "no fixture colour could be read out of kernel/register.py. This "
            "check reports clean when it reads nothing, which is how its "
            "predecessor passed for as long as it existed.")
    for const in sorted(enforced):
        colour = bound.get(const)
        if colour and f"{colour}/" not in spec_text:
            problems.append(
                f"kernel/register.py enforces a `{colour}/` fixture set and the "
                f"spec never names it. The gate someone builds from the spec is "
                f"weaker than the one that runs.")
    m = re.search(r"^MIN_BYPASS\s*=\s*(\d+)", text, re.M)
    if m and not re.search(r"bypass/\s*≥\s*" + m.group(1), spec_text):
        problems.append(
            f"kernel/register.py requires {m.group(1)} bypass cases and the "
            f"spec does not say so.")
    return problems


#: `12 個 checker` / `9 個 lens` -- a claim the repo can settle.
#:
#: The noun list is derived from `_reality()`, not written here. It used to be
#: ten names typed into this regex, and SPEC §13.5 already records what that
#: costs: `counted_claims` reads `N 個 X`, so a cell written `22` was invisible
#: to it -- "a format that escapes its own check is a format with no check". The
#: fix then was to rewrite that one cell. A name this file does not know is the
#: same hole with a different spelling, and the repo can already count every
#: name it should know.
#:
#: 種 is in the quantifier set for the same reason: 「5 種 claim kind」 is a
#: claim about a number, and the two-quantifier version could not see it.
_QUANTIFIERS = "個|條|種|項|道"


#: Digits, and deliberately not Chinese numerals.
#:
#: Reading 九 as 9 here was tried and reverted, and the reason is worth keeping:
#: 「一個 checker」 is *a* checker, not a claim that this repo has one, and
#: 「四個 checker 入面」 introduces four of thirty-one. Measured on this repo's
#: own documents, the wider matcher turned 6 findings into 62, of which 4 were
#: real. `CN` works in `lead_in_findings` because there the noun phrase is
#: followed by the block it counts, so the sentence is a claim about a length by
#: construction; here there is nothing to hold it to.
#:
#: The consequence is a rule for whoever writes these documents, and SPEC §13.5
#: already records it in the other direction -- a cell written `22` escaped this
#: check and the fix was to rewrite the cell: **a total is written in digits.**
#: A total spelled out is a total nothing settles.
_NUMERAL = r"\d+"


def _count_re(names):
    """A number, a quantifier, and something this repo can count.

    Four groups: the number, the quantifier (empty when there is none), the
    qualifier, the noun. The quantifier is optional and `counted_claims` refuses
    the match when it is absent unless the qualifier names a population this
    repo counts separately -- see `_names_its_population`.
    """
    # Longest first: `帶 engagement 嘅 kind` must win over `kind`.
    alt = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    # One optional qualifier before the noun: `5 種 claim kind` and `9 個
    # reviewer lens` are claims about the same numbers as `5 種 kind` and
    # `9 個 lens`, and the two-quantifier version could see neither.
    return re.compile(
        rf"({_NUMERAL})\s*(?:({_QUANTIFIERS})\s*)?([A-Za-z_*-]+\s+)?({alt})")


def _names_its_population(quantifier: str, qualifier: str, noun: str,
                          narrowing) -> bool:
    """May a count with no Chinese quantifier be read as a claim about a number?

    Only when it says which population it counts. `8 auth_decision rows` does;
    `27 checkers` does not, and the difference is not style. Dropping the
    quantifier outright was tried against this repo's own `docs/`: 50 findings,
    and reading them is the argument. `v4 check` parses as four checks because
    the product is called v4. `20 of 27 checkers are a thin CLI` is a
    measurement someone recorded, `15 checkers exit 4` is a column of a table
    about a Go repo, and `280 lens checks` counts something this file has a
    different number for under the same word.

    A name whose first word is in `narrowing` -- the first word of every
    multi-word key `_reality` returns -- is the one form that cannot be any of
    those: it is a table name, so the sentence has already said which set it is
    about. It arrives two ways and both have to be taken. `8 auth_decision rows`
    matches with the whole thing as the noun, because `auth_decision row` is a
    key; `5 conditional detectors` matches with `conditional` split off as the
    qualifier. Reading only the qualifier missed the first, which is the case
    this was written for. On this repo's `docs/`, 50 findings down to 0.
    """
    if quantifier:
        return True
    full = f"{qualifier.strip()} {noun}".strip()
    return full.split(" ", 1)[0] in narrowing


def _reality(root: Path):
    import sys as _sys
    counts = {}
    try:
        counts["checker"] = len(json.loads((root / CHECKERS).read_text()))
        kinds = json.loads((root / CLAIM_KINDS).read_text())
        counts["kind"] = len(kinds)
        # Settled rather than exempted. Every line mentioning `engagement` used
        # to be skipped, on the reading that `15 個帶 engagement 嘅 kind` is not
        # a claim about 22 -- true, and it made the one number nobody else could
        # check the one number nothing did. §9 said 15 kinds and 36 rules for as
        # long as there were 16 and 37.
        engaged = [v for v in kinds.values() if v.get("engagement")]
        counts["帶 engagement 嘅 kind"] = len(engaged)
        counts["engagement 規則"] = sum(len(v.get("rule") or []) for v in engaged)
        # The same populations under an English name. A key here is what a
        # sentence has to say to be settled -- `_names_its_population` takes the
        # first word of every multi-word key as the narrowing that makes a bare
        # count checkable -- so a population keyed only in Chinese means a
        # translated sentence about it is settled by nothing. Measured on this
        # document's own §4: `7 個判斷唔喺 entry file 嘅 checker` settled, and
        # `7 of the 21 checkers keep their judgement outside the entry file` did
        # not. Aliases rather than replacements: a repo part-way through a
        # translation carries both spellings.
        counts["engaged kind"] = len(engaged)
        counts["engagement rule"] = counts["engagement 規則"]
        # `21 個 kind` carries a quantifier and is settled; `21 kinds` carries
        # nothing and is read as prose. A population keyed under a two-word
        # English name is the one form `_names_its_population` accepts without
        # one, so these are what a translated sentence has to say to be checked
        # at all -- and §13.5's own table is written in them.
        counts["claim kind"] = len(kinds)
        counts["registered checker"] = counts["checker"]
    except (OSError, json.JSONDecodeError):
        pass
    # SPEC §4 states how many checkers keep their judgement out of the entry
    # file, and until this key existed nothing could check it: the sentence said
    # 13 "thin CLIs" and "thin" had no derivation anybody could reproduce --
    # "defines only `main`" gives 7, "has a same-named analysis module" gives 9,
    # "under a hundred inline lines" gives 14, "imports `kernel.analysis` at
    # all" gives 17. Four answers to one number is the shape this whole checker
    # exists to end, so the document was moved onto the one that needs no
    # judgement at all and the measure was brought here.
    #
    # Defining only `main` is that measure, and it is the property §12 step 1 is
    # actually about: a checker that defines a rule is a rule nothing can reach
    # without spawning a process. Where the judgement went -- `kernel/analysis/`
    # or `kernel/` when its inputs are the kernel -- is a layer question and not
    # this count's. `21 個 checker` above was already checked; this is the other
    # half of the same sentence.
    if (root / "checkers").is_dir():
        thin = 0
        for f in (root / "checkers").glob("*.py"):
            if f.name.startswith("_"):
                continue
            try:
                body = ast.parse(f.read_text(encoding="utf-8")).body
            except (OSError, SyntaxError):
                continue
            defined = [n.name for n in body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                         ast.ClassDef))]
            thin += 1 if defined == ["main"] else 0
        counts["個判斷唔喺 entry file 嘅 checker"] = thin
        # `main-only` rather than `thin`: the paragraph above is about `thin`
        # having four reproducible answers, and a key is the phrase a sentence
        # has to use, so it may not be the word this measure was moved off.
        counts["main-only checker"] = thin
    if (root / ".v4/lenses").is_dir():
        lenses = sorted((root / ".v4/lenses").glob("*.json"))
        counts["lens"] = len(lenses)
        counts["reviewer lens"] = len(lenses)
        # What a reviewer is actually handed. Two documents and a docstring
        # described this corpus by three numbers -- files, checks, and how many
        # of the checks were plain strings -- and `check` was not a name this
        # function knew, so the only one of the three anything could settle was
        # the file count. SPEC.md:1578 and SPEC.md:1946 then disagreed with
        # SPEC.md:1168 about the same table, inside one document.
        checks, objects = 0, 0
        for path in lenses:
            try:
                body = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            rows = body.get("checks")
            if not isinstance(rows, list):
                continue
            checks += len(rows)
            objects += sum(1 for r in rows if isinstance(r, dict))
        counts["check"] = checks
        # `reviewer check`, not `lens check`. A key's first word is what makes a
        # count with no quantifier readable, so the first word has to be a
        # qualifier and not a population of its own: keying this under `lens`
        # would settle every bare `11 lens(es)` -- including the two that are
        # sample CLI output -- against 13, and keying the file count under
        # `detector` would read `3,502 detector runs` as a claim about 20.
        counts["reviewer check"] = checks
        counts["dict-shaped check"] = objects
        counts["string-shaped check"] = checks - objects
    if (root / "detectors").is_dir():
        files = [p for p in (root / "detectors").glob("*.py")
                 if not p.name.startswith("_")]
        counts["detector"] = len(files)
        # `detector` names two populations and §8.8 says both in one row: the
        # registry holds one entry per *conditional* detector, and `detectors/`
        # holds those plus the unconditional `always_*` ones. Settled under one
        # name, one of the two numbers is always reported wrong -- and it was:
        # the row's `15 個 detector`, which is right, came back as a finding
        # against the 30 files.
        counts["always_* detector"] = len([p for p in files
                                           if p.name.startswith("always_")])
    try:
        counts["conditional detector"] = len(json.loads(
            (root / DETECTORS).read_text()))
    except (OSError, json.JSONDecodeError):
        pass
    if (root / "hooks").is_dir():
        counts["hook"] = len(list((root / "hooks").glob("*.py")))
    if (root / ".claude/agents").is_dir():
        counts["agent"] = len(list((root / ".claude/agents").glob("*.md")))
    cli = root / "kernel" / "cli.py"
    if cli.is_file():
        counts["子指令"] = len(set(re.findall(
            r'sub\.add_parser\(\s*"([a-z][a-z-]*)"', cli.read_text())))
    # How many standing rules layer ① carries. §13.5's table has a row for it
    # and the row said 88 while the constant held 96 -- the exact drift that
    # table's own warning is about, in the one cell whose number nothing here
    # could read. Taken from `root`'s source rather than from this process's
    # `kernel.doctrine`, because `_reality` answers about the tree it was given
    # and a checker may be run against a repo that is not its own.
    counts.update(_doctrine_rules(root))
    spec = root / "docs" / "SPEC.md"
    if spec.is_file():
        counts["pin"] = spec_pins.count(spec.read_text())
        # `192 個 pin` was settled and `192 pins` is prose, and the number that
        # says how much of this document is held to the code is not one to lose
        # to a translation. The value here is never the answer -- `pin` is
        # settled per document, so `counted_claims` replaces it with the count
        # for the file that sentence is about -- but the key has to exist for
        # the sentence to be read as a claim at all.
        counts["design pin"] = counts["pin"]
    counts.update(_facts_rows(root))
    return counts


def _doctrine_rules(root: Path):
    """`{"doctrine rule": 96}` -- the bullets `CLAUDE.md` layer ① is rendered from.

    The literal, not the rendered file. `kernel/doctrine.py::DOCTRINE` is the
    owner; `CLAUDE.md` is its output and `registry-consistency` already verifies
    the two agree, so counting the output would put a second reader on a
    question that has one.

    Evaluated as a literal rather than imported: this runs against a `root` that
    need not be the tree this process was started from, and importing would
    answer about the wrong repo without saying so. A repo whose doctrine module
    is absent or is not a plain literal settles nothing here, which is the same
    answer every other block gives for something that is not there.
    """
    src = root / "kernel" / "doctrine.py"
    try:
        tree = ast.parse(src.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "DOCTRINE"
                   for t in node.targets):
            continue
        try:
            sections = ast.literal_eval(node.value)
            return {"doctrine rule": sum(len(rules) for _, rules in sections)}
        except (ValueError, TypeError, SyntaxError, MemoryError,
                RecursionError):
            # Whatever the constant turns out to be, this settles nothing
            # rather than raising inside a checker. `literal_eval` raises
            # `ValueError` for a call and `TypeError` for shapes it will not
            # take, and the unpacking below has its own two.
            return {}
    return {}


def _facts_rows(root: Path):
    """How many rows each facts table holds.  `{"auth_decision row": 8, …}`.

    The one population this function could not settle, and the one whose whole
    purpose is to be complete. `docs/FACTS.md` argues at length that a table
    which under-reports its own surface makes every detector read a clean repo,
    tells the story of the paragraph that said five while the file held four,
    and calls that "the same failure one level out" -- and then wrote a count of
    its own that nothing could settle. It said seven from the day an eighth row,
    `kernel/analysis/fail_closed.py::_matches_auth`, landed on 2026-08-20.

    Derived from the file rather than named here. Every list-valued key is a
    table, and a repo that adds one gets it counted without this function being
    edited -- which is the difference between this and the ten typed-in nouns
    the regex above used to carry.

    `kernel.config.facts_path_for` is the one owner of "which file is this
    repo's table", and its own docstring counts nine places that spelled it
    differently. `.v4/layers.json` allows checkers -> kernel; being the tenth
    would be the cheaper thing and the wrong one.

    A repo with no table gets `{}` and nothing is settled against it, which is
    the same answer `_reality`'s other blocks give for a directory that is not
    there. A table that will not parse gets `{}` too -- `v4 facts validate` owns
    that complaint, and a second checker saying it in different words is two
    homes for one rule.
    """
    path = facts_path_for(root)
    if path is None:
        return {}
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(table, dict):
        return {}
    # `row`, not `glob` or `path`, for every table including `ui_globs`.
    # `docs/FACTS.md:40` describes a reference adopter's seven UI globs in a
    # column explaining what the field means, and this repo's own list is empty;
    # settling `glob` repo-wide would report that sentence as drift against a
    # number it was never about.
    return {f"{key} row": len(value) for key, value in sorted(table.items())
            if isinstance(value, list)}


def counted_claims(root: Path):
    """A number in prose that the repo can settle, and does not.

    Every one of these was true when written. `56 個 pin` was true at 56, and
    the reader who checks it at 91 learns that the document counts things
    approximately -- which is a worse thing to learn than the number.

    Historical statements are exempt by marker rather than by guesswork: a
    blockquote, a line naming the predecessor, or a line carrying `當時`. A
    sentence about what a run found on a particular day is not a claim about now.

    `pin` is settled per document, not repo-wide, because a pin count is a fact
    about one file. The first version counted `docs/SPEC.md` for every sentence
    anywhere, which was right while SPEC was the only document carrying pins and
    became wrong the moment `design_pins` stopped being pointed at one file: a
    row correctly saying RATIONALE carries five was reported against SPEC's
    ninety-four. A line that names a `.md` file is a claim about that file;
    otherwise it is a claim about the file it sits in.
    
    Arabic digits, deliberately.  A total in this document is written `11`, not
    `十一`, and the reason is measured rather than stylistic: teaching this to
    read Chinese numerals turned 6 findings into 62, of which 4 were real --
    `九個` in a sentence about what an experiment found in June is prose about
    the past, not a claim about the tree, and there is no punctuation that
    separates the two. So the rule is on the document: a number this checker is
    meant to settle is written in digits, and the four that were not were
    rewritten rather than parsed.

    The quantifier has one exception and it is a narrow one. `docs/FACTS.md` is
    an English document and its subject is a set of tables, so its own row
    counts had no Chinese-quantified form to be written in -- and `_reality`
    could not settle a facts table either way, so the count that decides whether
    every detector reads a clean repo was the count nothing checked. Both halves
    are repaired: `_facts_rows` settles the tables, and a number that names its
    table (`8 auth_decision rows`) counts without a quantifier. A bare number
    still does not; `_names_its_population` carries what that costs.
"""
    truth = _reality(root)
    count_re = _count_re(truth)
    #: The qualifiers this repo counts a separate population under.
    narrowing = {k.split(" ", 1)[0] for k in truth if " " in k}
    problems = []
    for doc in sorted((root / "docs").glob("*.md")):
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            # `engagement` qualifies which kinds are meant, and `15 個帶
            # engagement 嘅 kind` is not a claim about 22.
            if line.lstrip().startswith(">") or _about_the_past(line):
                continue
            named = re.findall(r"([A-Za-z_][A-Za-z0-9_]*\.md)", line)
            for n, quant, qual, noun in count_re.findall(line):
                # A count written without a Chinese quantifier is only a claim
                # about a number when it says which table it counts.
                # `docs/FACTS.md` is written in English and its own row count
                # had no other form to take.
                if not _names_its_population(quant, qual, noun, narrowing):
                    continue
                # The qualifier is part of the name before it is dropped from
                # it. `15 個 conditional detector` is a claim about the fifteen
                # in the registry, and answering it with the thirty files in
                # `detectors/` is this check reporting a drift that is not
                # there -- while the sentence that narrows a count to something
                # nothing can settle goes on saying whatever it likes.
                what = f"{qual.strip()} {noun}" if qual.strip() else noun
                real = truth.get(what)
                # `9 個 reviewer lens` is a claim about the same number as
                # `9 個 lens` -- the qualifier renames rather than narrows, and
                # dropping the fallback would stop checking every such line. A
                # qualifier this repo counts separately is the other kind, so
                # those and only those refuse to fall through.
                if real is None and qual.strip() not in narrowing:
                    what, real = noun, truth.get(noun)
                if noun == "pin":
                    real = _pins_in(root, named[0] if named else doc.name)
                if real is not None and int(n) != real:
                    problems.append(
                        f"docs/{doc.name}:{i} says {n} {what}, and there are "
                        f"{real}. Mark it 當時 if it is about a past run.")
    return problems


def _pins_in(root: Path, name: str):
    """How many pins one document carries.  None when there is no such file."""
    for cand in (root / "docs" / name, root / name):
        if cand.is_file():
            return spec_pins.count(cand.read_text(encoding="utf-8"))
    return None


def user_facing_docs(root: Path):
    """Every document that tells somebody which command to run.

    This was `docs/*.md` plus `CLAUDE.md`, and `.github/monitor/PROMPT.md` and
    `SCOPE.md` -- the two files a monitor session is told to paste and follow,
    and the only user-facing documents inside a protected path -- were read by
    no checker at all. That is how `PROMPT.md` shipped a `review close` with no
    `--claim`, which `kernel/cli.py::cmd_review` then wrote into the ledger as
    an event bound to nothing.

    This is the only thing in the repo that checks a documented `v4` invocation
    against the CLI, so what it does not read is unchecked by anything.

    `.claude/agents/*.md` and `.claude/commands/*.md` were then left out for the
    whole of this checker's life, and SPEC §12.5 is what makes that a hole
    rather than a preference: it names those seven files -- four roles and the
    three commands that string them together -- and the section that names them
    is contract. §12.5 also says "Prompt 唔係契約", and that sentence is about
    *what the files may say*: the roles can be rewritten without changing the
    kernel. It is not a licence for the commands inside them to be unrunnable.

    Measured 2026-08-27, before this line was added: `bash -lc "v4 --repo .
    review lens"` -- the invocation `.claude/agents/reviewer.md` opens with, on
    line 10, as the first thing a reviewer is told to do -- exits 127, command
    not found. All seven files showed `v4 …` and not one of them said where `v4`
    comes from, and nothing in the repo was reading them, so the document that
    teaches a role how to start could not be wrong in a way anything noticed.
    """
    out = sorted((root / "docs").glob("*.md")) + [root / "CLAUDE.md"]
    out += sorted((root / ".github" / "monitor").glob("*.md"))
    for sub in ("agents", "commands"):
        out += sorted((root / ".claude" / sub).glob("*.md"))
    # The front door, left out for as long as the roles were. `README.md` and
    # its two translations are 58 KB of user-facing document that show `v4 …`
    # as something to type, and this function's own sentence above says what
    # that costs: what it does not read is unchecked by anything.
    #
    # Measured before adding them: `launcher_is_reachable` and `flags_resolve`
    # both return zero problems with all three included, so this is coverage at
    # no cost -- and coverage that would have caught a command in the first
    # document a newcomer opens.
    #
    # This is half of finding f41271c8fbffdd01. The other half is a decision
    # nobody here can make: `docs/README.md`'s authority table asks each
    # document for a scope that does not overlap another, and these three
    # restate contracts SPEC owns -- the exit-code table, the staleness key, the
    # hook table, protected paths and eight counted claims. Either they stop
    # restating, or the table records them as a derived summary with SPEC as the
    # authority. That is a decision about what this repo's front door is.
    out += sorted(root.glob("README*.md"))
    out += sorted((root / ".agents/skills").glob("*/SKILL.md"))
    return [d for d in out if d.is_file()]


#: `bin/` is not on anybody's PATH, so a document showing `v4 …` as something
#: to type has to say where `v4` comes from. SPEC §11 does -- its block opens
#: with the export -- and `.github/monitor/PROMPT.md`, which exists to be
#: pasted into a fresh session, did not: `bash -lc "v4 --repo . review lens"`
#: exits 127 there while `./bin/v4 --repo . review lens` exits 0. `bin/v4`'s own
#: header says it exists so that a reader does not get command-not-found on the
#: first line of the walkthrough.
LAUNCHER = ("./bin/v4", "bin:$PATH", "python3 -m kernel.cli")


def launcher_is_reachable(root: Path):
    """A document showing a command to type says how `v4` resolves."""
    problems = []
    for doc in user_facing_docs(root):
        text = doc.read_text(encoding="utf-8")
        if any(hint in text for hint in LAUNCHER):
            continue
        inside, shown = False, []
        for i, line in enumerate(text.splitlines(), 1):
            if line.lstrip().startswith("```"):
                inside = not inside
                continue
            # A fence, or the four-space indent markdown renders the same way.
            # `.github/monitor/PROMPT.md` -- the file a monitor session is told
            # to paste -- writes its commands indented rather than fenced, so a
            # fence-only reading found nothing in the one document whose whole
            # job is to be typed.
            if (inside or line.startswith("    ")) and line.strip().startswith("v4 "):
                shown.append(i)
        if shown:
            problems.append(
                f"{doc.relative_to(root)}:{shown[0]} shows `v4 …` as a "
                f"command to type and never says where `v4` comes "
                f"from. `bin/` is on nobody's PATH, so that line exits 127. "
                f"Write `./bin/v4`, or open the block with "
                f'`export PATH=\"$PWD/bin:$PATH\"` the way SPEC §11 does.')
    return problems


def flags_resolve(root: Path):
    """Every flag a document shows on a `v4` command is one that command takes.

    The README explains, in prose, that an agent built a checker from a document
    declaring one CLI flag where the kernel passes three -- and sixteen fixtures
    exited 2. Directly beside that paragraph it showed
    `run-checker --checker design-pins`, where `--checker` takes a path and the
    command exits with "can't open file". A document teaching that lesson,
    failing it, in the same screen.

    Shape only: the flag has to be declared for that subcommand. Running the
    commands would be a checker with side effects.

    Every finding says where it saw the line, and used to say `docs/<name>`
    whatever `user_facing_docs` handed it. That was already wrong for
    `.github/monitor/`, and it becomes wrong seven more times with
    `.claude/agents/` and `.claude/commands/` in the set: a report reading
    `docs/reviewer.md:10` names a file nobody can open, so the one thing a
    finding owes its reader -- go and look -- is the thing it withholds.
    """
    cli = root / "kernel" / "cli.py"
    if not cli.is_file():
        return []
    src = cli.read_text(encoding="utf-8")

    # {subcommand: {flags}} -- parsed from the block each parser owns.
    subs, order = {}, []
    for m in re.finditer(r'(\w+)\s*=\s*sub\.add_parser\(\s*"([a-z][a-z-]*)"', src):
        order.append((m.start(), m.group(1), m.group(2)))
    for i, (pos, var, name) in enumerate(order):
        end = order[i + 1][0] if i + 1 < len(order) else len(src)
        flags = set(re.findall(rf'{var}\.add_argument\(\s*"(--[a-z-]+)"', src[pos:end]))
        subs[name] = flags | {"--repo", "--acceptance", "--help"}

    problems = []
    for doc in user_facing_docs(root):
        if not doc.is_file():
            continue
        where = doc.relative_to(root)
        for i, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            if _about_the_past(line):
                continue
            m = re.search(r"\bv4\s+(?:--repo\s+\S+\s+)?([a-z][a-z-]+)((?:\s+--?[a-z-]+)*)", line)
            if not m:
                continue
            cmd, rest = m.group(1), m.group(2)
            if cmd not in subs:
                continue
            for flag in re.findall(r"--[a-z-]+", rest):
                if flag not in subs[cmd]:
                    problems.append(
                        f"{where}:{i} shows `v4 {cmd} {flag}` and that "
                        f"command takes {sorted(subs[cmd] - {'--repo','--acceptance','--help'})}")
            # The flag can be right and the value wrong, which is how the
            # README's own example failed: `--checker design-pins` names a
            # registered id where the command wants a path, and exits with
            # "can't open file". Flags that take a path get their value looked
            # at.
            for flag, value in re.findall(
                    r"(--(?:checker|detector|fixtures|subject|out|events))\s+(\S+)", line):
                if value.startswith(("<", "$", "`", "'", '"', "…", "...")) or "/" in value \
                        or value.endswith((".py", ".json", ".jsonl", ".md")):
                    continue
                problems.append(
                    f"{where}:{i} shows `{flag} {value}`, and that flag "
                    f"takes a path. A registered id here exits with "
                    f"\"can't open file\".")
    return problems


def unbuilt_list_is_honest(root: Path):
    """Nothing in the unbuilt list is built.

    §0 promises that anything absent from that list is built. Nothing checked
    the other direction, and the list spent a day claiming four agent roles, two
    rules and a marker were missing after all seven had been built -- plus a row
    left behind by a failed edit whose subcommand count disagreed with the row
    above it.

    A list of what is missing is the one inventory nobody re-reads, because it
    is read to find out what to do rather than to check what was done.
    """
    spec = root / "docs" / "SPEC.md"
    if not spec.is_file():
        return []
    text = spec.read_text(encoding="utf-8")
    m = re.search(r"<!--\s*unbuilt-list\s*-->(.*?)(?=\n### )", text, re.S)
    if not m:
        return ["docs/SPEC.md has no <!-- unbuilt-list --> marker, so nothing can "
                "check that what it calls missing is missing"]
    # Only the first cell of each row -- the thing the row is about. A row
    # saying "the build half of `bundle-secret`" is not claiming the checker is
    # missing, and reading the whole row made three built checkers look
    # unbuilt.
    block = "\n".join(l.split("|")[1] for l in m.group(1).splitlines()
                       if l.strip().startswith("|") and l.count("|") >= 3)
    try:
        reg = set(json.loads((root / CHECKERS).read_text()))
        kinds = set(json.loads((root / CLAIM_KINDS).read_text()))
    except (OSError, json.JSONDecodeError):
        reg, kinds = set(), set()
    problems = []
    for name in set(re.findall(r"`([\w./-]+)`", block)):
        if name in reg or name in kinds:
            problems.append(
                f"the unbuilt list names {name!r}, which is registered. §0 tells "
                f"a reader that anything not on this list is built -- so a built "
                f"thing left on it is the promise pointing the wrong way.")
        elif "/" in name and (root / name).exists():
            problems.append(
                f"the unbuilt list names {name!r}, which exists on disk")
    return problems


#: A cross-reference between documents: `SPEC.md §4`, `RATIONALE.md`, and the
#: bare `§9` form that means "this document".
#: A cross-reference. The trailing `[)\]`]*` matters: the markdown link form
#: `[RATIONALE.md](RATIONALE.md) §1` puts a paren between the name and the
#: section, and without it the § reads as a reference to the current document.
DOC_REF = re.compile(r"`?\b([A-Z][A-Z_]*\.md)`?[)\]`]*(?:\s*§\s*(\d+(?:\.\d+)*))?")
SELF_REF = re.compile(r"(?<![\w.])§\s*(\d+(?:\.\d+)*)")
NUMBERED = re.compile(r"^#+\s*(\d+(?:\.\d+)*)[.\s]", re.M)


def dead_references(root: Path):
    """Does every document reference resolve?

    The predecessor had a command for this and ran it on every push. This
    project deleted it, on the grounds that documents were no longer authority
    -- and then accumulated twelve references to sections of a document that had
    been renumbered, six to a file that had been merged away, and one in the
    very file whose job is telling you what to read.

    A reference is the one part of a document that is mechanically checkable
    without understanding a word of it, and it is also the part a reader trusts
    most: a pointer says "the answer is over there", and following it to nothing
    is worse than not being told.
    """
    problems = []
    docs_dir = root / "docs"
    if not docs_dir.is_dir():
        return problems

    files = sorted(docs_dir.glob("*.md"))
    present = {p.name for p in files}
    sections_of = {p.name: set(NUMBERED.findall(p.read_text(encoding="utf-8")))
                   for p in files}

    # A name this project used to own. Citing another project's document is
    # provenance and stays; citing our own deleted one is a dead pointer, and
    # git is what tells the two apart without a hardcoded list.
    gone = set()
    try:
        # Asked first, because `git log` answers 128 to two different
        # questions and only one of them is a failure. Measured: outside a work
        # tree every one of these exits 128; inside a repository with no
        # commits `git log` still exits 128 ("does not have any commits yet")
        # while `rev-parse --verify -q HEAD` exits 1 and `ls-files` exits 0. A
        # history with no commits has deleted no document, so an empty `gone`
        # is that repo's correct answer rather than an unread one -- and
        # reporting it would put a line in front of every adopter on their
        # first day.
        head = subprocess.run(["git", "rev-parse", "--verify", "-q", "HEAD"],
                              cwd=root, capture_output=True, text=True,
                              timeout=30)
        out = subprocess.run(["git", "log", "--diff-filter=D", "--name-only",
                              "--pretty=format:", "--", "docs/"],
                             cwd=root, capture_output=True, text=True, timeout=30)
        if head.returncode == 1:
            pass                # a repository with no history: nothing deleted
        elif out.returncode != 0:
            # Not `pass`. A non-zero exit here empties `gone`, and an empty
            # `gone` says "no document points at one this project deleted" --
            # the same sentence a clean repo produces. Whether git could be
            # read is this half's own answer, so it is reported rather than
            # assumed.
            problems.append(
                f"the deleted-document half of this check did not run: "
                f"`git log --diff-filter=D` exited {out.returncode}, so a "
                f"reference to a document this project removed would not be "
                f"seen here" + (f" ({out.stderr.strip()[:200]})"
                                if out.stderr.strip() else ""))
        else:
            gone = {Path(l).name for l in out.stdout.split() if l.endswith(".md")}
            gone -= present
    except Exception as exc:                                    # noqa: BLE001
        problems.append(
            f"the deleted-document half of this check did not run "
            f"({type(exc).__name__}: {exc})")

    for p in files:
        text = p.read_text(encoding="utf-8")
        has_numbers = bool(sections_of[p.name])
        for line_no, line in enumerate(text.splitlines(), 1):
            for name, sec in DOC_REF.findall(line):
                if name in gone:
                    problems.append(
                        f"docs/{p.name}:{line_no} points at {name}, which this "
                        f"project deleted. A pointer to nothing is worse than no "
                        f"pointer.")
                elif name in present and sec and sec not in sections_of[name]:
                    problems.append(
                        f"docs/{p.name}:{line_no} points at {name} §{sec}, and "
                        f"that document has no such section")
            # A § that follows a document name belongs to that document, not
            # this one. `RATIONALE.md §1` and `PERFORMANCE_OPTIMIZATION.md §5.2`
            # were both read as self-references and reported as broken.
            for sec in SELF_REF.findall(DOC_REF.sub(" ", line)):
                if not has_numbers:
                    problems.append(
                        f"docs/{p.name}:{line_no} says §{sec} without naming a "
                        f"document, and this one has no numbered sections. The "
                        f"reader cannot tell what §{sec} is.")
                elif sec not in sections_of[p.name]:
                    problems.append(
                        f"docs/{p.name}:{line_no} says §{sec} and this document "
                        f"has no §{sec}")

    # The other half of the same rule. This walked `docs/` only, and a docstring
    # is where most of this project's cross-references actually live: measured
    # here, ten kernel modules opened with `SPEC.md §4`, `§6.5`, `§2.2` and
    # nine more that the document does not have -- `SPEC.md` numbers §0-§14 with
    # named subsections, so none of the `§N.N` forms resolves. Same check, same
    # data, one directory short.
    #
    # Only sections are judged here. A source file naming a document this repo
    # does not have may be citing a predecessor on purpose, and the git test
    # above cannot tell those apart outside `docs/`.
    sources = []
    try:
        tracked = subprocess.run(["git", "ls-files", "*.py"], cwd=root,
                                 capture_output=True, text=True, timeout=30)
        if tracked.returncode != 0:
            # The larger of the two silences. This half is where the findings
            # were -- ten kernel modules opening with sections the document
            # does not have -- and an empty `sources` means the loop below
            # examines no file at all while the function still returns a list
            # that reads as clean.
            problems.append(
                f"the source-citation half of this check did not run: "
                f"`git ls-files` exited {tracked.returncode}, so no Python "
                f"file was read and a docstring citing a section that does "
                f"not exist would not be seen here"
                + (f" ({tracked.stderr.strip()[:200]})"
                   if tracked.stderr.strip() else ""))
        else:
            sources = [root / f for f in tracked.stdout.split() if f.strip()]
    except Exception as exc:                                    # noqa: BLE001
        problems.append(
            f"the source-citation half of this check did not run "
            f"({type(exc).__name__}: {exc})")
    for p in sources:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = p.relative_to(root)
        for line_no, line in enumerate(text.splitlines(), 1):
            for name, sec in DOC_REF.findall(line):
                if name in present and sec and sec not in sections_of[name]:
                    problems.append(
                        f"{rel}:{line_no} points at {name} §{sec}, and that "
                        f"document has no such section")

    for name, secs in sections_of.items():
        dupes = [s for s in secs
                 # The boundary matters: `^#+\s*2[.\s]` matches `### 2.1` too,
                 # which reported every parent number as duplicated.
                 if len(re.findall(rf"^#+\s*{re.escape(s)}(?:\.\s|\s)",
                                   (docs_dir / name).read_text(encoding="utf-8"),
                                   re.M)) > 1]
        for d in sorted(dupes):
            problems.append(
                f"docs/{name} has more than one §{d}, so every reference to it "
                f"is ambiguous")

    return problems

