#!/usr/bin/env python3
"""``surface-proof`` detector.  PL-1.

Conditional on purpose. The checker exits 4 when a repo declares no
``surface_command``, and UNSUPPORTED is not terminal -- so an unconditional
detector would hang a claim nothing can answer on every task in every repo that
has no surface suite, and the only way past it would be a human signature per
task. The condition is a declaration, so the detector reads the declaration.

This is the half that was missing. ``checkers/surface_proof.py`` was written,
registered, and named by a claim kind, and no detector emitted the kind -- so
from the day it was built to the day this was written it had never run once.
Measured across the reference adopter's whole ledger: ``surface-proof``
appears zero times in 36 tasks.

    detectors/surface_proof.py --subject s.json [--facts f.json] [--out o.json]

Exit codes -- ``0`` is the only success value:

    0   read the declaration (emitting nothing is still 0)
    3   the detector itself broke
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel import config as config_mod  # noqa: E402

KIND = "surface-proof"
CONFIG_KEY = "surface_command"

#: The fact that says this repo has a surface at all. Declared by the repo, in
#: its own facts table, in whatever language it is written in -- so this reads
#: as well on a Go tree as on a Node one, and guesses at no stack.
SURFACE_FACT = "ui_globs"


def declared(root: Path):
    """The command this repo says drives its own surface, or None."""
    try:
        cfg = json.loads((root / ".v4" / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return config_mod.declared(cfg, CONFIG_KEY)


def has_a_surface(facts: dict) -> bool:
    """Did this repo say it has one?

    The condition used to be the command itself, so a repo with a surface and
    no command raised nothing and shipped -- measured on the reference adopter:
    7 `ui_globs`, 8 Playwright specs somebody maintains, and `web/ui/e2e/**`
    appearing in zero of 433 claims. An opt-in forcing function is a document.

    Raising it means the checker answers 4, and 4 is not terminal, so `ship`
    holds until the repo either says how to drive its surface or signs that it
    cannot be driven -- and that signature lapses the day the command is written.
    """
    return bool((facts.get("present", facts) or {}).get(SURFACE_FACT))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--subject", required=True)
    p.add_argument("--facts")
    p.add_argument("--out")
    a = p.parse_args()
    try:
        s = json.loads(Path(a.subject).read_text(encoding="utf-8"))
        root = Path(s["repo_root"]).resolve()
    except Exception as exc:                                    # noqa: BLE001
        print(f"detector failed: {exc}", file=sys.stderr)
        return 3
    facts = {}
    if a.facts:
        try:
            facts = json.loads(Path(a.facts).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            facts = {}
    if declared(root) or has_a_surface(facts):
        # One claim for the repo, not one per file: the suite is one thing and
        # it either passed or it did not. `staleness: repo` in the kind says
        # the same, and a per-file claim would make them disagree.
        print(f"V4-CLAIM: kind={KIND} symbol=<module> variant=suite")
    if a.out:
        Path(a.out).write_text(json.dumps(
            {"declared": bool(declared(root)), "has_surface": has_a_surface(facts)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
