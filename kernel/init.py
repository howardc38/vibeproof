"""Scaffold a repo so `v4 doctor` has something to report on.  SPEC.md §8.

Adopting V4 meant hand-writing three JSON files with no template, then finding
out what was missing by running `doctor` and reading its complaints one at a
time. Every one of those complaints already knows what the right shape is --
`doctor` could not have detected them otherwise -- so the shapes existed and
only the writing did not.

What this does not do: guess. It writes the dials the kernel defaults and the
repo may turn (the derive exclusions, the thresholds, the sweep interval) and
leaves a marked hole where only the repo can answer -- the test command, the
policy, the scope. What it does *not* write is anything the repo cannot change
by editing it: `protected_paths` is a union, so a copy of the framework's four
globs would be four lines an adopter can delete with no effect. A
scaffold that invents a `test_command` is a scaffold that hands you a green
`test` claim you did not earn, which is the failure mode this project is about.

It never overwrites. A second run reports what is already there.
"""

import json
from pathlib import Path

from . import config as config_mod

#: Left for the repo to answer, and marked so `doctor` can see it is unanswered.
#: One spelling, in `config`, because three checkers read it too.
UNANSWERED = config_mod.UNANSWERED

CONFIG_TEMPLATE = {
    "test_command": UNANSWERED,
    # The two questions that need something outside the source to answer, and
    # the command that answers the second. Left unanswered rather than absent:
    # a key that is not here reads as a repo that has no surface and writes to
    # nothing, and `v4 init` would say nothing about either. Measured on the
    # reference adopter: 7 `ui_globs` and 51 `outbound_write` patterns declared,
    # and neither of these two ever written -- so `surface-proof` and
    # `runtime-proof` raised zero claims across 37 tasks and `ship` never held.
    "surface_command": UNANSWERED,
    "runtime_proof": UNANSWERED,
    "truth_command": UNANSWERED,
    "policy": "allow_accepted_risk",
    # What *this repo* adds, not a copy of the framework's set. `scope.
    # protected_for` unions `PROTECTED_DEFAULT` in on every read, so a copy
    # here is a line an adopter can delete with no effect at all -- unlike
    # `thresholds`, where deleting a key means "use the default" and the file
    # still says what it does. Restating four globs an adopter cannot remove,
    # in a file whose whole job is to say what this repo decided, is the
    # write-a-thing-nobody-reads shape one level in.
    "protected_paths": [],
    # `.v4/fixtures/**` is not optional. A red fixture is deliberately broken --
    # an unpinned dependency, a committed credential, an import that resolves to
    # nothing -- and every checker that sweeps the repo rather than the diff will
    # find them. Measured on a real adoption: `dependency` reported 86 violations
    # and `dep-provenance` 46 unconfirmed packages, every one of them a fixture
    # doing its job.
    #
    # It says `.v4/fixtures` rather than `tests/fixtures` because that is where
    # `install` puts them. When they moved, this line did not, and the exclusion
    # silently stopped covering anything.
    "derive_exclude": ["tests/fixtures/**", ".v4/fixtures/**",
                       "**/__pycache__/**"],
    # Written so the dials are visible and editable, not so the two states can
    # be told apart: `sweep.config` merges `sweep.DEFAULT` over whatever is
    # here, so a repo that deleted this key and one that chose four days give
    # the same answer. This used to claim otherwise.
    "lens_sweep": {"every_days": 4, "weekday": None, "not_before_hour": None},
    "thresholds": dict(config_mod.DEFAULT_THRESHOLDS),
}

#: `v4 round` freezes this file for the length of a measurement round, and an
#: adopted repo had no such file: `v4 round open` died on a raw FileNotFoundError
#: traceback, so a whole mechanism was unreachable and said so in the least
#: useful way available. Copying this framework's criteria would be worse than
#: crashing -- they are its criteria, about its deliverables. So: the shape,
#: with the judgement left `TODO` exactly like `test_command`, and `round open`
#: refuses while it still says TODO. Freezing a ruler nobody has written is the
#: failure the freeze exists to prevent, performed on time.
ACCEPTANCE_TEMPLATE = {
    "rule": "A deliverable that fails these criteria is fixed in the deliverable, "
            "never by relaxing the criteria. Changing this file to make a failing "
            "deliverable pass is rewriting the exam because the student failed.",
    "criteria": UNANSWERED,
    "amendment_rule": "Between rounds only. Every measurement taken under the old "
                      "criteria is re-run rather than grandfathered.",
    "amendments": [],
}

#: The three claim kinds every repo can answer on day one, because their
#: checkers need nothing from the repo but a test command and a git history.
#: Starting from 22 would be starting from someone else's repo.
#: The three claim kinds every repo can answer on day one, because their
#: checkers need nothing from the repo but a test command and a git history.
#: Starting from 22 would be starting from someone else's repo.
#:
#: `engagement` carries the value this framework's own registry carries, not
#: `False`. The first version wrote `False` for all three, which quietly removed
#: layer 4 from every repo that adopted through `init` -- and then removed it
#: from a measurement of layer 4, where the finding read as "engagement was
#: never triggered" rather than "the scaffold turned it off".
#: Named here, defined nowhere here.  An earlier version wrote out each kind's
#: question, rules and flags by hand, which made this the second copy of three
#: rows that already exist in this framework's own registry -- and copies drift.
#: Measured: `secret` asked "Does {file} carry a credential?" while the registry
#: had moved to "Do the files this task changed contain a committed credential?",
#: and because `v4 install` merges rather than overwrites, the stale copy won.
#: Every repo adopting through `init` then rendered a question with an empty
#: `{file}` in it. The names belong here; the text belongs where it already is.
STARTER_KINDS = ("test", "scope", "secret")

#: No facts template ships, because writing one would mean shipping a scaffold
#: whose first act is to fail. `v4 install` produces the draft from this repo
#: instead; what is left is the half a program cannot do.
FACTS_IS_EARNED = [
    "v4 install                       # 順手寫低 .v4/facts.{name}.json.draft",
    "# 逐行核 seen_at,刪走猜錯嘅,`kind` 由 proposed 改成真嘅",
    "# auth_decision 空表要有一句 absent 說明;installer 寫嘅 AUTO 佔位要親自確認,",
    "#   否則 ship 擋住 —— 而「呢個 repo 冇任何關於邊個做得到乜嘅決定」呢句,",
    "#   問落去通常係五條冇人寫低嘅規則",
    "mv .v4/facts.{name}.json.draft .v4/facts.{name}.json",
]


def _kinds_with_rules() -> dict:
    """The three starter kinds, read from this framework's own registry."""
    src = Path(__file__).resolve().parent.parent / config_mod.CLAIM_KINDS
    table = json.loads(src.read_text())
    missing = [k for k in STARTER_KINDS if k not in table]
    if missing:
        raise RuntimeError(
            f"{src} has no {missing}. `init` starts a repo with these three and "
            f"reads their definition from here rather than keeping a second copy "
            f"-- renaming one has to be a change in one place, not a silent hole.")
    return {k: json.loads(json.dumps(table[k])) for k in STARTER_KINDS}


def scaffold(root: Path) -> list:
    """[(path, "written" | "kept")] -- never overwrites."""
    root = Path(root).resolve()
    v4 = root / ".v4"
    v4.mkdir(parents=True, exist_ok=True)
    plan = [
        (v4 / "config.json", CONFIG_TEMPLATE),
        (v4 / "claim_kinds.json", _kinds_with_rules()),
        # Empty registries rather than absent ones: `doctor` and
        # `registry-consistency` both read them, and a missing file reads as a
        # broken repo where an empty one reads as a repo that has registered
        # nothing yet. Those are different states and only one is a problem.
        (v4 / "acceptance.json", ACCEPTANCE_TEMPLATE),
        (v4 / "checkers.json", {}),
        (v4 / "detectors.json", {}),
    ]
    out = []
    for path, body in plan:
        if path.is_file():
            out.append((path.relative_to(root), "kept"))
            continue
        path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n")
        out.append((path.relative_to(root), "written"))
    return out


def unanswered(root: Path) -> list:
    """The holes the scaffold deliberately left.  [(file, field)]"""
    out = []
    for rel in (".v4/config.json", ".v4/acceptance.json"):
        try:
            obj = json.loads((Path(root) / rel).read_text())
        except (OSError, json.JSONDecodeError):
            continue
        out += [(rel, k) for k, v in sorted(obj.items()) if v == UNANSWERED]
    return out
