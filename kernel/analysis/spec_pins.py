"""What a `<!-- pinned: -->` is, asked once.  SPEC.md §3.

Two checkers read these markers and each had its own idea of what one is.
`design_pins` required a `::symbol` and reported 136; `spec_coverage` counted
every `<!-- pinned:` and reported 149; and `docs/README.md` printed a third
number, attributing `spec_coverage`'s count to `design_pins`. A reader
comparing them learns that the repo counts approximately, which is a worse
thing to learn than any of the three numbers.

The two disagreements were both real:

* **File-only pins.** 13 of them in SPEC alone -- `hooks/stop_gate.py`,
  `tests/run_without_silent_skips.py`, `detectors/route_auth.py` and ten more.
  `design_pins` matched none of them, so renaming a hook file broke no check
  while `README.md` promised it would. A pin naming only a file still says
  something a program can settle: the file is there.
* **Markers being shown rather than used.** `每個 <!-- pinned: --> 指住嘅
  symbol` inside a table cell is a sentence about the syntax. Counting it is
  reporting the document for describing itself.
"""

import re

#: A fenced block or an inline code span -- where a marker is being quoted.
SHOWN = re.compile(r"```.*?```|`[^`\n]*`", re.S)

#: Every marker, whether or not it names a symbol.
MARKER = re.compile(r"<!--\s*pinned:\s*(.*?)\s*-->")

#: `file::symbol`, optionally `=value`.
SYMBOL = re.compile(r"^([^\s:]+)::([A-Za-z_][A-Za-z0-9_]*)(?:=(.*))?$")


def prose(text: str) -> str:
    """The document with everything it is quoting taken out."""
    return SHOWN.sub("", text)


def pins(text: str):
    """[(file, symbol or None, wanted value or None)] -- every pin, in order.

    `file` is `""` for the empty marker, which is neither a pin nor prose and
    has to be reported as itself.
    """
    out = []
    for body in MARKER.findall(prose(text)):
        m = SYMBOL.match(body)
        if m:
            out.append((m.group(1), m.group(2), m.group(3)))
        else:
            out.append((body, None, None))
    return out


def count(text: str) -> int:
    """How many pins a document carries.  One answer, for every caller."""
    return len(pins(text))
