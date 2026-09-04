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
import json
import re
import subprocess
import sys
from pathlib import Path

#: The same expression `checkers/design_pins.py` matches with. Two spellings of
#: one pattern is how a detector and its checker start disagreeing about what
#: exists -- the thing every paired module in this repo is arranged to prevent.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
PIN = re.compile(r"<!--\s*pinned:\s*([^\s:]+)::([A-Za-z_][A-Za-z0-9_]*)(?:=(.*?))?\s*-->")


def pinned_docs(root: Path):
    out = subprocess.run(["git", "ls-files", "*.md", "**/*.md"], cwd=root,
                         capture_output=True, text=True)
    if out.returncode != 0:
        return []
    docs = []
    for rel in out.stdout.splitlines():
        p = root / rel
        try:
            if p.is_file() and PIN.search(p.read_text(encoding="utf-8", errors="replace")):
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
