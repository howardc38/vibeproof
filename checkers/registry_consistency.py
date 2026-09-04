#!/usr/bin/env python3
"""Does the registry still describe what is on disk?  SPEC.md §12.

This project deleted the commands V3 used to find drift between its documents
and its code, on the grounds that documents were no longer authority. That was
half true. The registries took over the job: `claim_kinds.json` says which
kinds exist and which checker judges each, `checkers.json` says where each
checker lives and what its hash is. Both are edited by hand or by one command,
and both can disagree with the files they name.

The failure that follows is quiet. A kind pointing at a checker nobody
registered raises claims nothing can answer. A checker registered against a
hash that no longer matches refuses to run, one claim at a time, with the
reason buried in an attempt row. A detector file sitting in the directory runs
on every derivation whether or not anyone meant it to.

Exit: 0 everything lines up | 1 something does not | 4 no registries to read
| >=5 broke.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel import hashing  # noqa: E402


#: How a rule id is written wherever it lands: the `from` field in a lens or a
#: kind rule, and plain text in a program's own source.
RULE_ID = re.compile(r"\bR-[0-9a-f]{8}\b")


def check(root: Path):
    problems = []
    try:
        kinds = json.loads((root / ".v4" / "claim_kinds.json").read_text())
        checkers = json.loads((root / ".v4" / "checkers.json").read_text())
    except FileNotFoundError:
        return None, ["no registries at .v4/"]
    except json.JSONDecodeError as exc:
        return [], [f"a registry does not parse: {exc}"]

    for kind, cfg in sorted(kinds.items()):
        cid = cfg.get("checker")
        if cid not in checkers:
            problems.append(
                f"kind {kind!r} names checker {cid!r}, which is not registered. "
                f"Claims of this kind cannot be answered by anything.")
        det = cfg.get("detector")
        if det and not (root / "detectors" / det).is_file():
            problems.append(
                f"kind {kind!r} names detector {det!r}, which is not in detectors/")
        # Every rule, not only an engaged kind's. `scope` carries one with
        # `engagement: false` -- `v4 scope widen` judges the `--why` sentence
        # against it (kernel/scope.py) -- and it was the one rule in the repo
        # nothing checked for having any text in it, because the quality test
        # hung off the engagement flag rather than off the rule existing.
        if cfg.get("rule"):
            rules = cfg.get("rule") or []
            if isinstance(rules, dict):
                rules = [rules]
            # Present is not the same as saying something. A bypass fixture set
            # the text to three spaces, which satisfied "carries a rule" and
            # puts a blank line on the worker's screen -- the exact request for
            # characters this is here to refuse.
            usable = [r for r in rules
                      if (r.get("text") or "").strip()
                      and (r.get("source") or "").strip()]
            if not usable:
                problems.append(
                    f"kind {kind!r} carries "
                    f"{'no usable rule' if not rules else 'only empty rules'}. "
                    f"A rule with no text is a request for characters -- "
                    f"SPEC.md §9.")

    for cid, entry in sorted(checkers.items()):
        path = root / entry["path"]
        if not path.is_file():
            problems.append(f"checker {cid!r} is registered at {entry['path']}, "
                            f"which does not exist")
            continue
        on_disk = hashing.file_sha(path)
        if on_disk != entry.get("sha256"):
            problems.append(
                f"checker {cid!r} on disk ({on_disk[:12]}) is not the registered "
                f"one ({str(entry.get('sha256'))[:12]}). Every claim it serves will "
                f"refuse to run, one at a time.")
        fx = entry.get("fixtures")
        if fx and not (root / fx).is_dir():
            problems.append(f"checker {cid!r} names fixtures at {fx}, which is gone")
        # What it reads, so the kernel can refuse to run it over a tree it
        # cannot parse. Measured on a Go repo: eight of twenty-seven returned
        # PASS having read no Go at all, one of them about an unpinned
        # dependency. Undeclared has to be a registry error rather than a
        # default, or "run it and hope" comes back as the quiet path.
        if not entry.get("reads"):
            problems.append(
                f"checker {cid!r} does not declare what it reads. Without it the "
                f"kernel runs it over any tree, and a checker that parsed "
                f"nothing reports a clean repo. Use `**` if it really is "
                f"language-independent -- that is a statement, not a blank.")

    served = {c.get("checker") for c in kinds.values()}
    for cid in sorted(set(checkers) - served):
        problems.append(f"checker {cid!r} is registered and no kind uses it")

    # Checkers, disk -> registry. This walked the registry and asked whether
    # each entry is on disk, and never the other way, though the docstring above
    # claims both directions and the detector half below does both. So a program
    # could sit in `checkers/` -- a protected path, a directory `install` copies
    # wholesale into every adopter -- registered nowhere, called by nothing, and
    # named by nothing. Two are there today.
    # `_`-prefixed is scaffolding by this repo's own convention -- the detector
    # loader skips those names and so does `derive`, so a program called
    # `_selftest_real.py` is not claiming to be a checker. Same rule both sides.
    checker_files = {p.name for p in (root / "checkers").glob("*.py")
                     if not p.name.startswith("_")}
    registered_files = {Path(str(e.get("path", ""))).name for e in checkers.values()}
    for name in sorted(checker_files - registered_files):
        problems.append(
            f"checkers/{name} is a program in a protected directory that no "
            f"registry entry names, so nothing can run it and nothing gates it. "
            f"Register it, or take it out.")

    declared = {c.get("detector") for c in kinds.values() if c.get("detector")}
    on_disk = {p.name for p in (root / "detectors").glob("*.py")
               if not p.name.startswith("_")}
    for name in sorted(on_disk - declared):
        problems.append(
            f"detectors/{name} runs on every derivation and no kind declares it")

    # The detector registry, on the same terms as the checker one. Until it
    # existed, `v4 verify-detector` printed a verdict that went nowhere: the
    # spec required a conditional detector to pass its gate and nothing could
    # tell whether one had, so `derive` ran every file in the directory.
    try:
        detectors = json.loads((root / ".v4/detectors.json").read_text())
    except (OSError, json.JSONDecodeError):
        detectors = {}
    for name in sorted(on_disk):
        if name.startswith("always_"):
            # SPEC.md §2: one fixed line, no dependence on the tree, so "should
            # not fire" cannot be written as a fixture. Its kind's checker is
            # what gates it.
            continue
        entry = detectors.get(name)
        if entry is None:
            problems.append(
                f"detectors/{name} decides what gets checked and has never "
                f"passed `v4 register-detector`. derive refuses to run it.")
            continue
        on_disk_sha = hashing.file_sha(root / "detectors" / name)
        if entry.get("sha256") != on_disk_sha:
            problems.append(
                f"detector {name} on disk ({on_disk_sha[:12]}) is not the "
                f"registered one ({str(entry.get('sha256'))[:12]}). Re-register "
                f"it, or derive will skip it and record it as not run.")
    for name in sorted(set(detectors) - on_disk):
        problems.append(
            f"detector {name} is registered and not in detectors/")

    # The doctrine file is generated from these same registries, so a stale one
    # is a registry disagreement wearing different clothes. It lives here rather
    # than in a checker of its own because this is the question this checker
    # already owns: something declared on one end and the other end not
    # following. A generated file anybody can edit is a hand-written file with a
    # misleading header.
    try:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        from kernel import config as _config
        from kernel import doctrine as _doctrine
        problem = _doctrine.drift(_config.RepoConfig(root))
        if problem:
            problems.append(problem)
    except ImportError:
        pass                      # a repo without this kernel owes no doctrine
    except Exception as exc:                                    # noqa: BLE001
        problems.append(f"could not check the doctrine file: {exc}")

    problems += _dispositions_hold(root)
    problems += _absence_still_holds(root)

    return kinds, problems


def _absence_still_holds(root: Path):
    """A table declared empty, re-checked against the repo as it is now.

    `absent` lets a repo say "we have none of these, and here is how I know".
    That was the fix for a library with no outbound write being unable to adopt.
    But a declaration nobody re-reads is the failure this whole project removes,
    and this one had no reader: measured on a five-task run, the repo declared
    `outbound_write` absent, a later task added `open(path, "w")`, no claim was
    raised -- correctly, the detector has no vocabulary for it -- and the task
    shipped.

    The scan is the same one `v4 install` used to write the declaration, so it
    is exactly as strong as the claim it is checking, and no stronger: a verb
    ending on a dotted call. It cannot see a write behind `subprocess`. It can
    see the ordinary case that makes a declaration go stale, which is the case
    that happened.

    Two reads can fail here, and both used to answer `[]` -- the same value as
    "every absence in this table still holds". `[]` from this function is the
    whole verdict: `check` collects it, `main` prints "21 kind(s) … all agree"
    and exits 0, with not one word about the read that did not happen. This is
    the only reader of `facts.absent` there is, so nothing downstream would
    have caught it either. A check that could not run reports that it could not
    run, which here is a problem string like any other: exit 1, and a sentence
    naming the file rather than the declaration.
    """
    from kernel.config import facts_path_for
    fp = facts_path_for(root)
    if fp is None:
        return []
    try:
        table = json.loads(fp.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{fp.name} does not read ({type(exc).__name__}: {exc}), so "
                f"every `absent` declaration in it went un-rechecked. Nothing "
                f"here says those absences still hold -- it says nobody could "
                f"look."]
    # `facts.AUTO_PREFIX`, not the literal. That constant carries its own
    # warning -- "this is a ship gate held together by a string literal typed in
    # three modules. Changing the writing side alone would make the reading side
    # see a repo where every absence had been confirmed." Two of the three
    # readers were repaired to import it and this one, in the checker layer, was
    # not; the direction of failure is the loose one.
    from kernel import facts as _facts
    absent = {k: v for k, v in (table.get("absent") or {}).items()
              if not str(v).startswith(_facts.AUTO_PREFIX)}
    if not absent:
        return []
    try:
        if str(root) not in sys.path:
            sys.path.insert(0, str(root))
        found = _facts.propose(root)
    except Exception as exc:                                    # noqa: BLE001
        return [f"the scan behind `facts.absent` did not run "
                f"({type(exc).__name__}: {exc}), so the {len(absent)} "
                f"hand-written absence declaration(s) in {fp.name} were not "
                f"re-checked against this tree. `v4 facts propose` runs the "
                f"same scan and prints what it hit."]

    out = []
    for name in sorted(absent):
        reason = str(absent[name])
        # A candidate the reason already names is one the author looked at and
        # ruled out -- `ITEMS.get` is a dict lookup, not an outbound read, and
        # saying so in the reason is the whole act this is asking for.
        #
        # Without this the finding is unsatisfiable: the scan keeps finding the
        # same call whatever anybody writes, and an unsatisfiable finding is a
        # permanent blocker, which is the shape this repo spent a day removing.
        new = [r for r in (found.get(name) or [])
               if r["pattern"] not in reason
               and r["pattern"].rsplit(".", 1)[-1] not in reason]
        if not new:
            continue
        where = ", ".join(f"{r['pattern']} ({r['seen_at']})" for r in new[:3])
        out.append(
            f"facts.absent[{name!r}] says this repo has none, and a scan finds "
            f"{len(new)} the reason does not mention: {where}"
            + ("…" if len(new) > 3 else "")
            + ". The declaration was true when it was written. Add the rows, or "
              "name these in the reason and say why they do not count.")
    return out


def _dispositions_hold(root: Path):
    """Every rule that says it landed somewhere carries its id there.

    `.v4/rule_dispositions.json` records where each of the 254 rules read out of
    the predecessor's guidelines ended up. A register like that is worth exactly
    what the check behind it is worth: without one, "nothing was lost" is a
    sentence, and writing that sentence is how rules disappear.

    Matched by id, not by text. The first version compared a prefix of the
    wording and reported 141 failures, because rules get translated, shortened
    and split when they land -- comparing text is a rule against rewording. The
    landing site carries the id instead.

    Layer 1 is the stated exception: those 78 went through one synthesis into 79
    grouped lines, so there is no 1:1 to check. Only the count and the source
    are verified there. That is a weaker guarantee, and it says so rather than
    pretending otherwise.
    """
    reg = root / ".v4" / "rule_dispositions.json"
    if not reg.is_file():
        return []
    try:
        rules = json.loads(reg.read_text())["rules"]
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        return [f".v4/rule_dispositions.json does not read: {exc}"]

    landed_ids, unreadable = set(), []
    lenses = root / ".v4" / "lenses"
    if lenses.is_dir():
        for f in lenses.glob("*.json"):
            try:
                for c in json.loads(f.read_text()).get("checks", []):
                    if isinstance(c, dict) and c.get("from"):
                        landed_ids.add(c["from"])
            except (OSError, json.JSONDecodeError) as exc:
                # A file that will not parse contributes no `landed_ids`, and
                # every rule that landed there is then reported as not landed --
                # so the repair a reader is pushed toward (re-land the rule) is
                # not the repair the repo needs (fix the file). The two produced
                # identical output. `kernel/review.lens_files` solves the same
                # problem for lenses by returning the unusable ones by name.
                unreadable.append(f"{f.relative_to(root)}: {exc}")
    kinds_path = root / ".v4" / "claim_kinds.json"
    if kinds_path.is_file():
        try:
            for spec in json.loads(kinds_path.read_text()).values():
                for r in (spec.get("rule") or []):
                    if isinstance(r, dict) and r.get("from"):
                        landed_ids.add(r["from"])
        except (OSError, json.JSONDecodeError) as exc:
            unreadable.append(f".v4/claim_kinds.json: {exc}")
    # A rule can also land in a program, and that is the cheapest home there is:
    # written once, never forgotten, no sentence owed on any task. The register
    # could not express it -- `landed: checkers/x.py` was unsatisfiable however
    # the checker was written -- so the one outcome the whole placement argument
    # points at was the one the bookkeeping refused to record.
    for d in ("checkers", "detectors", "kernel"):
        base = root / d
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*.py")):
            try:
                landed_ids |= set(RULE_ID.findall(f.read_text(encoding="utf-8",
                                                              errors="replace")))
            except OSError:
                pass

    problems, missing = [], []
    # Named before anything is reported about what landed. A rule that landed
    # in a file nothing could read is not a rule that did not land, and the two
    # sentences push a reader at different repairs.
    problems += [f"{u} -- rules that landed here cannot be seen, so they are "
                 f"reported below as not landed" for u in unreadable]
    doctrine_expected = 0
    for r in rules:
        target = r.get("landed")
        if target == "CLAUDE.md":
            doctrine_expected += 1
            continue
        # Three states, not two. The register used to skip
        # `checker-candidate-survived` outright -- and that was the one bucket
        # that most needed verifying, because it means "this should be a
        # program". Measured when the skip was removed: all seven were marked
        # survived, five of them had a live checker doing exactly the rule
        # (`route-auth`, `dal-write`, `test-shape` twice, `dangling-ref`), and
        # not one checker carried the id -- so the register read as though five
        # real programs were still candidates, and whoever picked it up next
        # would have built them again.
        #
        # `owed` and `not-applicable` stay exempt from the id check and are not
        # exempt from saying why: a rule with no home is a fact somebody has to
        # state, not a row that goes quiet.
        if target in ("owed", "not-applicable"):
            if not (r.get("why") or "").strip():
                missing.append(
                    f"{r.get('id')} is {target!r} and says nothing about why. "
                    f"That is the state a rule goes to when nobody wants to "
                    f"argue about it.")
            continue
        if not target or target == "checker-candidate-survived":
            missing.append(
                f"{r.get('id')} is still {target!r}. Either a program answers "
                f"it -- name the file and put the id in it -- or it is `owed` "
                f"or `not-applicable` with a reason.")
            continue
        if r.get("id") not in landed_ids:
            missing.append(f"{r.get('id')} says it landed in {target} and no "
                           f"entry there carries that id")

    if doctrine_expected:
        try:
            from kernel import doctrine as _doc
            have = sum(len(g[1]) for g in _doc.DOCTRINE)
        except Exception:                                       # noqa: BLE001
            have = 0
        if have < doctrine_expected * 0.9:
            problems.append(
                f"{doctrine_expected} rules were assigned to layer 1 and the "
                f"doctrine module carries {have}. The synthesis is not 1:1, so "
                f"this is a count check -- but that gap is too wide to be "
                f"rewording.")

    if len(missing) > 6:
        problems += missing[:6] + [f"... and {len(missing) - 6} more"]
    else:
        problems += missing
    return problems

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True)
    ap.add_argument("--facts")
    ap.add_argument("--out")
    a = ap.parse_args()
    try:
        root = Path(json.loads(Path(a.subject).read_text())["repo_root"])
    except Exception as exc:                                    # noqa: BLE001
        print(f"cannot read subject: {exc}", file=sys.stderr)
        return 5

    try:
        kinds, problems = check(root)
    except Exception as exc:                                    # noqa: BLE001
        print(f"checker failed: {exc}", file=sys.stderr)
        return 5

    if kinds is None:
        print("no registries at .v4/; nothing to reconcile")
        return 4
    if a.out:
        Path(a.out).write_text(json.dumps({"problems": problems}, indent=2))
    if problems:
        print(f"FAIL: {len(problems)} registry disagreement(s).\n")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"{len(kinds)} kind(s) and their checkers, detectors and fixtures all agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
