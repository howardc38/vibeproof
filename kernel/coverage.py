"""What the predecessor's obligations map onto here.  SPEC.md §10.

The plan said to keep V3's obligation taxonomy as a catalogue of checkers worth
writing, and the number it kept quoting was 332. Reconciling it against the
event store says otherwise, and the correction matters more than the coverage
number:

    557 unique obligation ids across 51 "families"
    366 of them -- two thirds -- are PO-DL-nnn, generated per phase
    the reusable part is PO-4 (6 ids), PO-5 (17) and PO-6 (5): 28 in total

So it was never a taxonomy of 332 kinds. It was roughly thirty reusable
questions plus five hundred phase-specific ones that will never recur, and a
"we cover 12 of 332" figure would have been measuring against a denominator
that does not exist.

This is a report and not a gate on purpose. A gate here would fail every task
for debt no task created, which is how `secret-scan` earned seventy-eight
false positives -- and unlike lint there is nothing here a baseline could ever
retire, because most of the denominator is one-off by construction.
"""

import json
from pathlib import Path

#: Which reusable obligation families a V4 claim kind answers.  Hand-written,
#: because the mapping is a judgement about what two questions have in common
#: and no string comparison recovers it.
MAPPING = {
    # `lint` shared this family until it was cut: 178 runs, 38% of them
    # UNSUPPORTED because its checker reads `**/*.py` only, and every one of
    # its eleven FAIL->PASS transitions ended in a baseline suppression file
    # rather than a code change. `test` carries PO-4 alone now.
    "PO-4": ["test"],
    "PO-5": ["external-write", "fail-closed", "test"],
    # `surface-proof` asks whether the repo's own surface suite passes and
    # `runtime-proof` asks whether the table that owns the data has the row.
    # This said `[]  # no surface checker yet` while both were registered with
    # detectors, so every `v4 coverage --predecessor` run reported PO-6
    # unanswered -- a judgement about the registry that nothing compared to the
    # registry. `tests/test_kernel.py` now does.
    "PO-6": ["surface-proof", "runtime-proof"],
}

REUSABLE = tuple(MAPPING)


def load(root: Path):
    path = Path(root) / ".v4" / "obligation_catalogue.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


class RubricUnreadable(Exception):
    """`.v4/risk_rubric.json` is on disk and does not read."""


def rubric(root: Path):
    """The declared risk rubric, or `None` when this repo declared none.

    `None` used to mean that *and* "there is one and it will not parse", and
    both callers are one hop from a reader forming an impression. `cmd_coverage`
    printed "no .v4/risk_rubric.json here" about a file that is here, and
    `cli._print_uncovered` -- whose whole job is to stop `SHIP` plus a green
    suite reading as "this works" -- printed nothing at all, so a repo with a
    malformed rubric and a repo where every operational-risk class has a kind
    produced the same ship report.

    The distinction belongs here rather than in an `is_file()` at each caller:
    two derivations of one boundary is how the two get to disagree, which is the
    same defect `doctrine.drift` carried. Same contract as
    `secret_patterns.table_for` for the same reason -- a table that does not
    parse is an error, never an empty one.
    """
    path = Path(root) / ".v4" / "risk_rubric.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise RubricUnreadable(
            f"{path} is on disk and does not read "
            f"({type(exc).__name__}: {exc})") from exc


def risk_report(root: Path, registered_kinds):
    """Coverage against 17 risk classes instead of 557 mostly-one-off ids.

    The old denominator was the predecessor's obligation catalogue, and this
    module's own docstring already said two thirds of it is `PO-DL-nnn`
    generated per phase and will never recur. A coverage figure against a
    denominator that is mostly one-off by construction is not a coverage
    figure.

    These 17 are risk classes drawn from sources that move on a multi-year
    cadence -- SRE PRR, Well-Architected, 12-Factor, OWASP -- plus the shapes
    that recurred across the predecessor's overlay corpus. Nine are marked
    `judgment`: only this project's architecture answers them, which is also
    the half no checker reaches.

    Still a report and still not a gate, for the reason above: a gate here
    fails every task for debt no task created, and unlike lint there is no
    baseline that could ever retire it.
    """
    doc = rubric(root)
    if doc is None:
        return None
    rows = doc["rows"]
    live = [r for r in rows
            if [k for k in r["answered_by"] if k in registered_kinds]]
    judgment = [r for r in rows if r["fill"] == "judgment"]
    j_live = [r for r in judgment
              if [k for k in r["answered_by"] if k in registered_kinds]]

    lines = [
        f"{len(rows)} operational-risk classes  ({doc['source']})",
        f"  {len(live):>2} have a mechanism here, {len(rows) - len(live)} have none",
        f"  {len(j_live):>2} of the {len(judgment)} marked `judgment` -- the ones only "
        f"this project's architecture can answer",
        "",
    ]
    for r in rows:
        answered = [k for k in r["answered_by"] if k in registered_kinds]
        mark = {"answered": "OK  ", "partial": "half", "none": "--  "}[
            "none" if not answered else r["coverage"]]
        tag = "judgment" if r["fill"] == "judgment" else "mixed"
        lines.append(f"  {mark} #{r['n']:<2} {r['category'][:46]:<46} [{tag}]")
        if answered:
            lines.append(f"          {', '.join(answered)}")
    lines += [
        "",
        "Not a gate. A gate here fails every task for debt no task created, and "
        "unlike lint nothing in this denominator could ever be retired by a "
        "baseline. What it is for: knowing which classes have no mechanism "
        "before deciding what to build next.",
    ]
    return lines


def report(root: Path, registered_kinds):
    cat = load(root)
    if cat is None:
        return ["no .v4/obligation_catalogue.json; nothing to reconcile against"]

    fams = cat["families"]
    reusable = {f: fams[f] for f in REUSABLE if f in fams}
    one_off = {f: v for f, v in fams.items() if f not in REUSABLE}
    one_off_ids = sum(v["distinct_ids"] for v in one_off.values())

    lines = [
        f"{cat['unique_obligation_ids']} obligation ids in the predecessor, "
        f"{len(fams)} families",
        f"  reusable   {sum(v['distinct_ids'] for v in reusable.values()):>4} ids "
        f"in {len(reusable)} families ({', '.join(reusable)})",
        f"  phase-only {one_off_ids:>4} ids in {len(one_off)} families -- generated "
        f"per phase, will not recur",
        "",
    ]
    for fam, meta in sorted(reusable.items()):
        answered = [k for k in MAPPING[fam] if k in registered_kinds]
        missing = [k for k in MAPPING[fam] if k not in registered_kinds]
        state = ("answered by " + ", ".join(answered)) if answered else "NOTHING ANSWERS THIS"
        lines.append(f"  {fam:<6} {meta['distinct_ids']:>3} ids  {state}")
        if missing:
            lines.append(f"         missing: {', '.join(missing)}")
        lines.append(f"         e.g. {', '.join(meta['examples'][:3])}")
    lines += [
        "",
        "The 332 figure the plan quoted is not in this data. Counting families "
        "gives 51; counting ids gives 557; counting the part that recurs gives "
        "28. Coverage against the first two would be measuring against a "
        "denominator that is mostly one-off by construction.",
    ]
    return lines
