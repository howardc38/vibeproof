#!/usr/bin/env python3
"""``runtime-proof`` detector.  PL-3.

Conditional for the same reason as ``surface_proof.py``: the checker exits 4
when nothing is declared, UNSUPPORTED is not terminal, and an unconditional
detector would block every task in every repo that has no live environment to
ask.

Two keys, and both have to be there. ``runtime_proof`` says what to trigger and
what to expect; ``truth_command`` says who to ask. Either one alone produces a
claim the checker can only answer with 4 -- so requiring both here is the
difference between a claim and a permanent open question.

The checker and detector share runtime_scope: a known test/documentation-only
change outside all scoped proofs produces an explicit not_applicable result.
An uncovered runtime change still raises the claim that needs a proof.

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
from kernel import runtime_scope  # noqa: E402

KIND = "runtime-proof"

#: The fact that says this repo changes something outside itself. Declared by
#: the repo, in its own facts table -- no stack is assumed and no directory
#: layout is guessed.
WRITES_FACT = "outbound_write"


def declared(root: Path):
    """`[proof, ...]` this repo says it can prove, or `[]`."""
    try:
        cfg = json.loads((root / ".v4" / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    proofs = config_mod.declared(cfg, "runtime_proof")
    truth = config_mod.declared(cfg, "truth_command")
    if not isinstance(proofs, list) or not proofs or not truth:
        return []
    return proofs


def writes_outward(facts: dict) -> bool:
    """Did this repo say it changes something outside itself?

    If it did, there is something whose landing can be proved, and a repo that
    declares 51 outbound writes and no way to check that any of them arrive is
    exactly the case this kind exists for. Raising it answers 4, and 4 is not
    terminal -- so `ship` holds until the repo declares a proof or signs that it
    has no live environment to ask.
    """
    return bool((facts.get("present", facts) or {}).get(WRITES_FACT))


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
    proofs = declared(root)
    _, proof_scope = runtime_scope.select(root, s, proofs)
    if (proofs or writes_outward(facts)) and proof_scope["status"] != "not_applicable":
        # One claim covering every declared proof, because the checker runs
        # them all and returns one exit code. One claim per proof would ask the
        # checker to answer a question it does not take an argument for.
        print(f"V4-CLAIM: kind={KIND} symbol=<module> variant=triggered")
    if a.out:
        Path(a.out).write_text(json.dumps(
            {"proofs": len(proofs), "writes_outward": writes_outward(facts),
             "scope": proof_scope}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
