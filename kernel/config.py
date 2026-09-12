"""The five stores that configure a repo.  SPEC.md §8.

`RepoConfig` loads `config.json`, `claim_kinds.json`, `checkers.json`,
`detectors.json` and the facts table.  This said "three" while loading five,
and the one it left out of the count is the one whose absence turns every
conditional detector off -- SPEC §8 gives that registry its own subsection and
a fail-closed argument.  The module that owns the loading is the last place
that should be undercounting it.

These deliberately live in git rather than in the ledger.  rev 1 put the
registries in the append-only database, which forced a "latest row wins" fake
UPDATE the first time anyone edited a timeout.  Config is edited by hand, so it
belongs somewhere edits are normal and visible in a diff.

The cost is that `.v4/` is then writable by whoever can write the repo -- and
`test_command` is the sole oracle for every `test` claim.  That is why `.v4/**`
is on protected_paths and why every attempt records the config's hash: change
the oracle and previously-answered claims go stale rather than staying green.
"""

import json
from pathlib import Path

from . import hashing
from . import layout

#: Re-exported, because every caller in this package already spells them
#: `config_mod.CONFIG`. They are declared in `kernel/layout.py` so that
#: `kernel/facts.py` can have the one it needs without importing this module,
#: which was an import cycle: `facts` -> `config` at import time and `config`
#: -> `facts` inside `_load_facts`.
from .layout import (CONFIG, CLAIM_KINDS, CHECKERS,  # noqa: F401,E402
                     DETECTORS)

#: Where a delta checker keeps the violations that predate it.  One shape for
#: every such checker: four of them were about to invent four, which is the
#: divergence this project exists to remove.  Lives under .v4/, so a worker
#: cannot grow it without a signature that lands a commit with a name on it.
BASELINE_TEMPLATE = ".v4/{kind}_baseline.json"


def baseline_path(repo_root, kind):
    return Path(repo_root) / BASELINE_TEMPLATE.format(kind=kind)

#: SPEC.md §6.  `allow_accepted_risk` occasionally asks for a signature;
#: `no_accepted_risk` never asks and lets unprovable claims hold the task.
#: What `v4 init` writes for a field only the adopting repo can answer.
#:
#: It lives here rather than in `init` because reading it is not init's job:
#: three checkers take a command out of `.v4/config.json` and run it, and a
#: repo that never answered hands them this string. `test` ran it -- measured:
#: `TODO exited 127 without a run summary`, reported as exit 1, which says the
#: repo's tests fail about a repo that never said how to run tests. A wrong
#: verdict, not a missing one.
UNANSWERED = "TODO"


def declared(cfg: dict, key: str):
    """What this repo actually declared for `key`, or `None`.

    `None` covers three states that are the same state: the key is absent, it
    is empty, or it still holds `UNANSWERED`. A checker that gets `None` has
    not been told, which is exit 4 -- and exit 4 is not terminal, so the
    question stays open instead of being answered wrongly in either direction.
    """
    v = cfg.get(key)
    if isinstance(v, str):
        v = v.strip()
        return v or None if v != UNANSWERED else None
    if isinstance(v, (list, dict)):
        return v or None
    return v


POLICIES = ("allow_accepted_risk", "no_accepted_risk")

#: How long a checker may run before the kernel gives up on it.
#:
#: One number, because there were two: `install` wrote 120 into the adopter's
#: registry for any checker whose source did not declare one, and `check` ran
#: that same checker with 300. Nothing bites today -- all 27 registered entries
#: declare their own -- but SPEC §8 spends a paragraph on the two timeouts
#: having to agree, and this is the pair it was about.
DEFAULT_CHECKER_TIMEOUT = 300

#: How long a repo's own suite may take. Read through `declared`, so a repo
#: that wrote `TODO` there is "has not said" rather than a `ValueError` from
#: `int()` -- which is what `cfg.get("test_timeout_sec", 1800)` produced, and
#: `checkers/test.py`'s bare `except Exception` then swallowed into `ran =
#: None`, silently switching off the execution-trace half of the `test` claim.
#: Named here because `kernel/config.py:66-73` already says "One number,
#: because there were two", about the other timeout.
DEFAULT_TEST_TIMEOUT = 1800

DEFAULT_THRESHOLDS = {
    "min_chars": 40,
    "dup_threshold": 0.8,
    "ship_rederive_max": 3,
    "widen_warn_pct": 5,
    # When a `report` kind stops being deferrable. Three, because deferral
    # rots in three ways and a threshold on one misses the other two: a pile
    # nobody is working through, one old enough to be forgotten, and a claim
    # the work keeps re-opening -- which is not a backlog at all, and no
    # amount of waiting fixes it. `state.split_open` reads them.
    #
    # Defaults rather than required keys: a repo that never wrote them still
    # escalates. The alternative is a field that raises on somebody else's
    # machine the first time a `report` kind piles up.
    "report_max_open": 10,
    "report_max_days": 14,
    "report_max_repeat": 5,
}


class ConfigError(RuntimeError):
    pass


def _load(path: Path, what: str, required=True):
    if not path.is_file():
        if required:
            raise ConfigError(f"{what} not found at {path}")
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        # A malformed config must never read as an empty one: that would
        # silently drop every claim kind and ship a task with nothing checked.
        raise ConfigError(f"{what} at {path} is not valid JSON: {exc}") from exc


def facts_path_for(root):
    """This repo's fact table, or `None`.  The only answer to that question.

    Nine places spelled it and they did not agree -- `facts*.json` against
    `facts.*.json`, preferred-then-fallback against first-wins. The ones with no
    preference at all are the ones that bite: `checkers/dead_wiring.py` and
    `checkers/registry_consistency.py` took `sorted(...)[0]`, so a repo carrying
    a second table is judged against another project's vocabulary. This one had
    two -- `facts.vibeproof.json` and an adopter's -- and escaped it only
    because its own name sorts first.

    It carries one now, and the second lives under `tests/fixtures/facts/`.
    That is not tidying: a repo with two tables answers `None` here in any
    checkout whose directory is not named after it, and `git clone <url>
    my-name` and GitHub's own "Download ZIP" -- which unpacks to
    `<repo>-main/` -- are both that. Measured on the published tree cloned into
    `verify-public`: `v4 accept` 5/7, six tests down with
    `AttributeError: 'NoneType'`, on bytes that pass 7/7 under the right
    directory name. With one table the `len(named) == 1` branch answers, and
    the name of the directory stops mattering.

    `.draft` is excluded: `v4 install` writes `facts.<name>.json.draft` for a
    person to review, and reading a draft as the table is reading a proposal as
    a decision.

    And the escape ran out. The adopter's table sorted before
    `facts.vibeproof.json`, so `named[0]` was another project's vocabulary in any
    checkout whose directory is not named after the repo -- which is every
    `git clone` into a directory of the cloner's choosing. Measured in a tree
    called `differently-named`: the auth-decision scan found none of this repo's
    own hooks, silently, because it was reading the adopter's table.

    So the fallback is only taken when there is one table to fall back to.
    Two tables and no preferred name is a question this function cannot answer,
    and answering it wrongly is worse than saying so: `None` reaches `v4 doctor`
    as a table nothing can find, while a wrong table reaches every conditional
    detector as a vocabulary that quietly does not describe this repo.
    """
    root = Path(root)
    named = [p for p in sorted(root.glob(".v4/facts*.json"))
             if not p.name.endswith(".draft")]
    if not named:
        return None
    preferred = root / f".v4/facts.{layout.repo_name(root)}.json"
    if preferred in named:
        return preferred
    return named[0] if len(named) == 1 else None


class RepoConfig:
    def __init__(self, repo_root):
        self.root = Path(repo_root).resolve()
        self.config_path = self.root / CONFIG
        self.config = _load(self.config_path, "config")
        from .hosts import CONFIG_KEY, validate_selection
        try:
            validate_selection(self.config.get(CONFIG_KEY, ["claude"]))
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
        self.kinds = _load(self.root / CLAIM_KINDS, "claim_kinds", required=False)
        self.checkers = _load(self.root / CHECKERS, "checkers", required=False)
        # Written by `v4 register-detector`. Not required: a repo with only
        # unconditional detectors has nothing to put here, and refusing to
        # load without it would make the gate a reason not to adopt.
        self.detectors = _load(self.root / DETECTORS, "detectors", required=False)
        self.facts, self.facts_path = self._load_facts()
        self._reads_facts_cache = {}

        for field in ("test_command", "policy"):
            if field not in self.config:
                raise ConfigError(f"{CONFIG} is missing required field {field!r}")

        # The value, not just the key. `policy` decides whether this repo has a
        # signature route at all, and a misspelling used to land silently on the
        # permissive side -- a repo that meant to switch signing off got signing,
        # and nothing anywhere said so.
        if self.config["policy"] not in POLICIES:
            raise ConfigError(
                f"{CONFIG} sets policy={self.config['policy']!r}, which is not "
                f"one of {POLICIES}. The two differ in whether a claim that "
                f"cannot be proved can be signed away, so a value nobody "
                f"recognises must not resolve to either.")

        self.thresholds = {**DEFAULT_THRESHOLDS, **self.config.get("thresholds", {})}

        # Checked at load, on the same grounds as `policy` above. These four
        # numbers gate shipping, widening and signing -- `scope.widen`,
        # `risk.accept` and `engagement.judge` all compare a length against
        # `min_chars`, and `lifecycle.ship` does arithmetic on
        # `ship_rederive_max`. A string or a null there raised TypeError from
        # inside the gate, at the moment somebody was trying to get past it,
        # with a message about `<` and `str`. An unusable threshold is a config
        # error and belongs here, where the file is being read.
        for name, want in (("min_chars", int), ("ship_rederive_max", int),
                           ("widen_warn_pct", (int, float)),
                           ("dup_threshold", (int, float))):
            val = self.thresholds.get(name)
            if isinstance(val, bool) or not isinstance(val, want):
                raise ConfigError(
                    f"{CONFIG} sets thresholds.{name}={val!r}. It has to be a "
                    f"number: this one is compared against a length or counted "
                    f"down inside a gate, so a value of the wrong type does not "
                    f"loosen the gate, it crashes whoever is standing at it.")
            if val < 0:
                raise ConfigError(
                    f"{CONFIG} sets thresholds.{name}={val!r}, and a negative "
                    f"floor is a gate that cannot refuse anything.")

        # The fifth number, and it gates the same way. `sweep.due` does
        # `timedelta(days=float(c["every_days"]))` and
        # `checkers/sweep_current.py` does `float(...)` on the same field, so a
        # null or a string there raises TypeError from inside the after-gate --
        # the exact failure the paragraph above describes, one config key over.
        every = (self.config.get("lens_sweep") or {}).get("every_days")
        if every is not None:
            if isinstance(every, bool) or not isinstance(every, (int, float)):
                raise ConfigError(
                    f"{CONFIG} sets lens_sweep.every_days={every!r}. It has to "
                    f"be a number: the sweep gate turns it into a timedelta, so "
                    f"a value of the wrong type crashes the gate rather than "
                    f"loosening it.")
            if every < 0:
                raise ConfigError(
                    f"{CONFIG} sets lens_sweep.every_days={every!r}, and an "
                    f"interval that has already elapsed before it starts is not "
                    f"a schedule.")

        # Refused rather than defaulted. The weaker of the two is the one that
        # never goes stale for a kind with no subject files, so a missing field
        # would quietly buy a permanent pass.
        for name, spec in self.kinds.items():
            if spec.get("staleness") not in ("subject", "repo"):
                raise ConfigError(
                    f"claim kind {name!r} does not say whether it is judged on its "
                    f"subject or on the repo. There is no default: the weaker "
                    f"answer never expires for a kind with no subject files.")

    def _load_facts(self):
        """Find the repo's fact table and validate it.

        This looked for `.v4/facts.json` while every repo ships
        `.v4/facts.<name>.json`, so `facts` was always `{}` -- and a detector
        given no table falls back to a generic vocabulary and finds less,
        without saying so. Measured: the external-write checker flags the
        feed_graph defect with the table and passes it without one.

        A malformed table is an error, not an empty one. Reading it as empty
        would silently disarm every detector that depends on it, which is the
        loudest possible version of the failure it is meant to prevent.
        """
        path = facts_path_for(self.root)
        if path is None:
            return {}, None
        data = _load(path, "facts")
        try:
            from . import facts as facts_mod
            facts_mod.validate(data)
        except ImportError:
            pass
        except Exception as exc:                                # noqa: BLE001
            raise ConfigError(f"{path} did not validate: {exc}") from exc
        return data, path

    def facts_sha_for(self, checker_id) -> str:
        """The facts sha this checker's answer depends on, or `""`.

        `facts_sha` existed and nothing read it, so editing `.v4/facts.json`
        expired no answer anywhere -- while the table is what seven of this
        repo's checkers scan against. Charging all of them would be the other
        error: 22 declare the `--facts` flag and never touch the value, and
        re-running a 4-minute suite because an unrelated glob moved is how a
        staleness rule stops being obeyed.

        Which is which is not a new judgement: `derive._touches_facts` already
        answers it from the AST, for detectors, and is the reason the empty-glob
        guard applies to some and not others. Same question, other half.
        """
        if not self.facts_path:
            return ""
        entry = self.checkers.get(checker_id)
        if not entry:
            return ""
        return self.facts_sha if self._reads_facts(entry.get("path")) else ""

    def _reads_facts(self, rel) -> bool:
        # Cached per instance: this parses a checker's AST and `check` asks it
        # once per claim. Not `lru_cache` on the method -- that keys on `self`
        # and keeps every RepoConfig ever built alive for the process.
        if not rel:
            return False
        if rel in self._reads_facts_cache:
            return self._reads_facts_cache[rel]
        # `derive.reads_facts`, which follows imports. This used to call the
        # textual half -- `_touches_facts` on the entry file alone, and a
        # private name at that -- and the two disagree about nine of this
        # repo's registered checkers: `dead-wiring`, `facts-coverage`,
        # `layer-boundary`, `lint`, `registry-consistency`, `runtime-proof`,
        # `surface-proof`, `sweep-current` and `test` all reach the table
        # through `kernel/analysis/`, and every one recorded `facts_sha = ""`.
        # `derive._reads_facts` was written to follow imports for exactly this
        # reason, on the detector side: "the textual answer was wrong for two
        # of the three detectors a table can turn off."
        from .derive import reads_facts
        path = self.root / rel
        if not path.is_file():
            self._reads_facts_cache[rel] = False
            return False
        self._reads_facts_cache[rel] = reads_facts(path, self.root)
        return self._reads_facts_cache[rel]

    @property
    def facts_sha(self) -> str:
        """Empty when there is no table -- so a claim answered without one is
        distinguishable from a claim answered with one."""
        return hashing.file_sha(self.facts_path) if self.facts_path else ""

    @property
    def sha(self) -> str:
        return hashing.file_sha(self.config_path)

    @property
    def protected(self):
        from .scope import protected_for
        return protected_for(self.config)

    def kind(self, name):
        if name not in self.kinds:
            raise ConfigError(
                f"claim kind {name!r} is not in {CLAIM_KINDS}. A detector that emits an "
                f"unregistered kind is refused rather than guessed at."
            )
        return self.kinds[name]

    def checker_for(self, kind_name):
        cid = self.kind(kind_name)["checker"]
        if cid not in self.checkers:
            raise ConfigError(
                f"claim kind {kind_name!r} wants checker {cid!r}, which is not registered. "
                f"Register it with `v4 register` -- that runs its fixtures first."
            )
        entry = self.checkers[cid]
        return cid, self.root / entry["path"], entry

    def reads_for(self, checker_id):
        """The paths this checker declared it reads, or `None` for "anything".

        `.v4/checkers.json`'s `reads`, which `v4 register` requires and `doctor`
        already checks against the paths a checker names. A repo-scoped answer
        keys on these rather than on the whole tree: the `test` claim's oracle
        is a test command, and editing `docs/SPEC.md` or a baseline expired it
        for an edit it cannot see.

        `None` and not `[]` when the registry has no entry: a checker that
        declared nothing might read anything, and the safe direction there is
        the whole tree.
        """
        entry = self.checkers.get(checker_id)
        if not entry:
            return None
        globs = entry.get("reads")
        return list(globs) if globs else None

    def checker_sha_on_disk(self, checker_id):
        entry = self.checkers.get(checker_id)
        if not entry:
            return ""
        # The same thing `runner` records: the checker plus every module in
        # this repo it imports. Comparing the entry file alone meant editing the
        # module that makes the decision left every prior PASS looking fresh.
        from .runner import V4_HOME
        return hashing.program_sha(self.root, self.root / entry["path"], framework_root=V4_HOME)

    def question(self, kind_name, *, file="", symbol="", variant="", line=None):
        """Fill the kind's template.  SPEC.md §4: generated, never authored."""
        tpl = self.kind(kind_name)["question_template"]
        return tpl.format(file=file, symbol=symbol, variant=variant, line=line)
