#!/usr/bin/env python3
"""One repo-scoped claim, raised only where a document actually carries a pin.

`design-pins` verifies that every `<!-- pinned: file::symbol -->` in every
tracked markdown still resolves. `install.py` says the mechanism "sat installed
and idle" and blames nobody having told adopters to write a pin. That was half
the reason. The other half is that no detector emitted the kind, so even a repo
full of pins would never have had one checked.

Conditional on a pin existing, not on a config key: writing a pin *is* the
declaration, and a repo with none would otherwise carry a permanently
UNSUPPORTED claim for a mechanism it has not opted into.
"""

import argparse
import fnmatch
import json
import subprocess
import sys
from pathlib import Path

#: The owner `checkers/design_pins.py` reads through, not a second spelling of
#: it. The comment here already said the two had to be one expression, and this
#: line was the second one anyway: the checker's `PIN` *is* `spec_pins.MARKER`,
#: while this required a `::symbol`, so a document whose pins name a file and
#: no symbol was invisible to the detector and no claim about it was ever
#: raised. Measured before the change: 39 documents listed here against 41 by
#: the owner, the two missing being
#: `tests/fixtures/design_pins/green/a_file_only_pin_that_holds` and
#: `red/a_file_only_pin_naming_nothing` -- the pair that exists to exercise
#: exactly this shape.
#:
#: `spec_pins.pins` rather than `spec_pins.MARKER`, because `MARKER` alone
#: matches the marker being quoted: `docs/README.md` shows `<!-- pinned: -->`
#: inside an inline code span to explain the convention, and `pins` runs
#: `prose` first and drops the empty marker, so that page stays out.
#:
#: The second spelling had that false positive too, which was not predicted
#: and came out of the red step: it read a `<!-- pinned: src/core.py::run -->`
#: quoted inside an inline code span as a pin, because it never ran `prose`.
#: So it was both blind to real file-only pins and credulous about quoted
#: ones, and the owner answers both.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel.analysis import spec_pins  # noqa: E402


def carries_a_pin(text: str) -> bool:
    """Does this document pin anything? One answer, from the owner."""
    return any(path for path, _symbol, _want in spec_pins.pins(text))


def pinned_docs(root: Path):
    """Documents this repo pins something in, minus what it excludes.

    `derive_exclude` applies here for the reason `checkers/design_pins.py`
    gives above its own copy of this: a red fixture is deliberately broken, so
    a pin that fails inside one is the fixture working. The checker read the
    list and this did not, and the detector is the half that *raises* -- so a
    claim was opened about a file the checker would then refuse to look at, and
    somebody had to judge it by hand. Measured on an adopter: 20 documents seen
    here, 19 of them under a path their config excludes.

    `external_write.py`'s own comment records `derive_exclude` being ignored in
    five separate places. This was the sixth.
    """
    out = subprocess.run(["git", "ls-files", "*.md", "**/*.md"], cwd=root,
                         capture_output=True, text=True)
    if out.returncode != 0:
        return []
    try:
        excl = json.loads((root / ".v4/config.json").read_text()).get(
            "derive_exclude", [])
    except (OSError, json.JSONDecodeError):
        excl = []
    docs = []
    for rel in out.stdout.splitlines():
        if any(fnmatch.fnmatch(rel, g)
               or fnmatch.fnmatch(rel, g.rstrip("/") + "/*") for g in excl):
            continue
        p = root / rel
        try:
            if p.is_file() and carries_a_pin(
                    p.read_text(encoding="utf-8", errors="replace")):
                docs.append(rel)
        except OSError:
            continue
    return docs


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--subject", required=True)
    p.add_argument("--facts")
    p.add_argument("--out")
    a = p.parse_args()
    try:
        s = json.loads(Path(a.subject).read_text())
        root = Path(s["repo_root"])
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr)
        return 3

    docs = pinned_docs(root)
    if docs:
        # One claim, because the checker reads every pinned document and
        # returns one exit code. The kind is `staleness: repo` and says the
        # same; a per-document claim would make the two disagree.
        print("V4-CLAIM: kind=design-pins symbol=<module> variant=pins")
    if a.out:
        Path(a.out).write_text(json.dumps({"docs": docs}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
