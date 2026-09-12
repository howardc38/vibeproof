"""v4 -- the kernel's command line.

Everything an agent can ask the kernel to do goes through here.  Note what is
absent: there is no command that writes an outcome.  An agent can ask for a
checker to be run; it cannot report what running it produced.
"""

import argparse
import json
import shlex
import sqlite3
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from . import config as config_mod
from . import layout as layout_mod
from . import facts as facts_mod
from . import (composition, coverage, engagement, ledger, lifecycle, register,
               review, risk, runner, scope as scope_mod, state)


def _repo(args):
    return Path(args.repo).resolve()


def _acceptance(args) -> Path:
    """Where the criteria live, relative to the repo rather than to the shell.

    `--acceptance` defaults to `.v4/acceptance.json` and every caller resolved
    it with `Path(args.acceptance).resolve()`, which is relative to the process
    cwd. `--repo` exists precisely so a command can be run from somewhere else
    -- `bin/v4` passes `--repo $(git rev-parse --show-toplevel)` on every
    invocation -- so the ruler a measurement round is frozen against was
    whichever file happened to sit beside the shell.
    """
    given = Path(args.acceptance)
    return given.resolve() if given.is_absolute() else (_repo(args) / given).resolve()


def cmd_init(args):
    """Write the three files a repo needs, and say what is still owed.

    It answers what the kernel already fixes and refuses to answer the rest.
    `test_command` is the sole oracle for every `test` claim, so a scaffold that
    guessed it would hand out a green claim nobody earned.
    """
    from . import init as init_mod
    root = _repo(args)
    for rel, what in init_mod.scaffold(root):
        print(f"  {what:<8} {rel}")
    if getattr(args, "hosts", None):
        from . import hosts
        hosts.configure(root, args.hosts)
    # Layer 1 is a generated file, and a repo without it has that layer absent
    # rather than empty. It was absent from every arm of the three-arm
    # experiment for exactly this reason: `init` wrote the registries and not
    # the doctrine, so the layer whose whole mechanism is "be in the context"
    # was not in anyone's context, and the run was read as evidence about it.
    try:
        from . import config as _cfg_mod, doctrine as _doc
        _doc.write(_cfg_mod.RepoConfig(root))
        # And the line that keeps it. `doctrine.drift` is opt-in via
        # `"doctrine": true` and its docstring says why -- a fixture case is a
        # mini-repo with no reason to carry standing rules, so the flag has to
        # be explicit rather than inferred from the presence of `.v4/`. This
        # wrote the file and never wrote the flag, so every repo adopted through
        # `v4 init` carried a layer-1 file whose deletion produced no finding at
        # all. Measured on a fresh init: with the key absent `drift` returns
        # `None` for a missing CLAUDE.md, and with it set the same call returns
        # "CLAUDE.md is missing and this repo declares ...", which is the case
        # the flag exists for.
        #
        # Written here rather than into `init.CONFIG_TEMPLATE`, because the flag
        # says this repo keeps a generated CLAUDE.md and that is only true once
        # the write above has succeeded -- the scaffold runs before it and would
        # be declaring something the next line might fail to do.
        cfg_path = root / config_mod.CONFIG
        obj = json.loads(cfg_path.read_text(encoding="utf-8"))
        if not obj.get("doctrine"):
            obj["doctrine"] = True
            cfg_path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
        from .hosts import doctrine_files
        print("  written  " + ", ".join(p.name for p in doctrine_files(root)) + " (generated doctrine)")
        print("  written  .v4/config.json: doctrine=true; selected instruction files are checked")
    except Exception as exc:                                    # noqa: BLE001
        print(f"  skipped  CLAUDE.md   ({exc})")
    owed = init_mod.unanswered(root)
    if owed:
        print(f"\n{len(owed)} field(s) only this repo can answer, left as "
              f"{init_mod.UNANSWERED!r}:")
        for where, field in owed:
            print(f"  {where:<22} {field}")
    print("\nfacts 表要由 repo 度賺返嚟,唔係一個模板:")
    for line in init_mod.FACTS_IS_EARNED:
        print(f"  {line.format(name=layout_mod.repo_name(root))}")
    print("  v4 --repo . doctor      # 逐項講返仲欠乜")
    return 0


def _install_copy(install_mod, src, root, kinds, host_choice=None):
    """Land the files, then make the exclusion follow the fixtures."""
    copied = install_mod.copy_files(src, root, kinds, host_choice=host_choice)
    written = [p for p, how in copied if how == "written"]
    updated = [p for p, how in copied if how == "updated"]
    yours = [p for p, how in copied if how == "yours"]
    print(f"  {len(written)} file(s) copied"
          + (f", {len(updated)} updated" if updated else "")
          + (f", {len(yours)} yours and left alone" if yours else ""))
    for rel in yours[:8]:
        print(f"    yours  {rel}")
    if len(yours) > 8:
        print(f"    …and {len(yours) - 8} more")
    # The exclusion has to follow the fixtures. A repo that adopted before they
    # moved carries the old line, and its checkers would sweep the new location.
    cfg_path = root / config_mod.CONFIG
    try:
        obj = json.loads(cfg_path.read_text())
        exclude = list(obj.get("derive_exclude") or [])
        want = f"{install_mod.ADOPTER_FIXTURES}/**"
        if want not in exclude:
            obj["derive_exclude"] = exclude + [want]
            cfg_path.write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n")
            print(f"  .v4/config.json: added {want} to derive_exclude -- a red "
                  f"fixture is broken on purpose and every repo-sweeping checker "
                  f"would find it")
    except (OSError, ValueError) as exc:
        # Not swallowed. The comment two lines up says what the alternative is
        # -- every repo-sweeping checker firing on fixtures that are broken on
        # purpose -- and `pass` left the adopter with that and no sentence.
        print(f"  .v4/config.json: could not add "
              f"{install_mod.ADOPTER_FIXTURES}/** to derive_exclude ({exc}). "
              f"Add it by hand, or every repo-sweeping checker will report the "
              f"red fixtures as findings.")
    install_mod.write_kinds(root, kinds)
    print(f"  {len(kinds)} kind(s) declared in .v4/claim_kinds.json")


def _install_checkers(install_mod, conn, root, kinds, reg_src, facts):
    """Every checker, through the gate it would face one at a time.

    The gate is not skipped because this is bulk: a checker that would be
    refused on its own is refused here too.  (registered, refused, no_subject)
    """
    by_checker = {}
    for name, spec in kinds.items():
        by_checker.setdefault(spec.get("checker"), []).append(name)
    ok_n, refused, no_subject = 0, [], []
    for cid in sorted(k for k in by_checker if k):
        entry = reg_src.get(cid)
        if not entry or not (root / entry["path"]).is_file():
            continue
        good, report = register.register(
            conn, checkers_json=root / config_mod.CHECKERS, checker_id=cid,
            checker_path=(root / entry["path"]).resolve(),
            kinds=sorted(by_checker[cid]),
            # Where they landed here, not where they live in the framework.
            fixtures_dir=(root / install_mod.fixture_dest(entry["fixtures"])).resolve(),
            timeout_sec=entry.get("timeout_sec",
                                  config_mod.DEFAULT_CHECKER_TIMEOUT),
            repo_root=root,
            facts=facts, reads=entry.get("reads") or [])
        if good:
            ok_n += 1
            no_subject += [(cid, r["said"]) for r in report.get("no_subject") or []]
        else:
            refused.append((cid, report["failures"][:1]))
    print(f"  {ok_n} checker(s) registered through their fixtures")
    return ok_n, refused, no_subject


def _install_detectors(install_mod, conn, root, src, facts):
    """Every conditional detector, through its own gate.

    Skipping it here would leave `derive` silently raising none of their claims
    -- the failure `doctor` reports as "not registered, or changed since".
    """
    det_src = json.loads((src / ".v4" / "detectors.json").read_text())
    det_ok, det_bad, det_missing = 0, [], []
    for name, entry in sorted(det_src.items()):
        # Where the fixtures landed here, not where they live in the framework
        # -- the same translation the checker loop above does. Without it every
        # `fx.is_dir()` was False, all 9 detectors took the `continue`, and the
        # line below reported "0 registered" as though that were a finding.
        dpath = root / entry["path"]
        fx = root / install_mod.fixture_dest(entry.get("fixtures", ""))
        if not dpath.is_file() or not fx.is_dir():
            det_missing.append((name, entry.get("fixtures", "")))
            continue
        good, dreport = register.register_detector(
            conn, detectors_json=root / ".v4" / "detectors.json",
            detector_path=dpath.resolve(), fixtures_dir=fx.resolve(),
            repo_root=root, facts=facts)
        if good:
            det_ok += 1
        else:
            det_bad.append((name, (dreport.get("failures") or [""])[:1]))
    print(f"  {det_ok} conditional detector(s) registered through their fixtures")
    # These were collected and never printed. A detector that does not register
    # raises none of its claims, and `derive` gives no sign -- the whole reason
    # this gate runs at install time is so the answer arrives before a task does.
    for name, fails in det_bad:
        print(f"    REFUSED {name:<24} {fails}")
    for name, where in det_missing:
        print(f"    no fixtures for {name:<18} looked in {where}")
    return det_ok, det_bad, det_missing


def _install_artefacts(install_mod, root, src, kinds):
    """The pin example, the launcher, the facts draft, layer ①, the stamp."""
    pinned = install_mod.write_pin_example(root)
    if pinned:
        print(f"  {pinned.relative_to(root)} now carries one worked "
              f"`<!-- pinned: -->` -- `design-pins` verifies it every task")
    lenses = len(list((root / ".v4" / "lenses").glob("*.json")))
    print(f"  {lenses} lens(es) available to `v4 review lens`")
    launcher = install_mod.write_launcher(root, src)
    print(f"  {launcher.relative_to(root)} written -- run every command through it")
    # After the registries exist, so the proposal can tell this repo's own code
    # from the checkers and hooks that just landed in it.
    fpath, guessed = install_mod.write_facts(root)
    if fpath:
        print(f"  {fpath.relative_to(root)} drafted from this repo "
              f"({guessed} guessed row(s) to prune)")
    else:
        print(f"  facts table already here, left alone")
    # After the facts draft, and reading it: `entry` comes from
    # `entrypoint_globs`, and a data layer only from a declared `dal_globs`.
    lpath, edges = install_mod.write_layers(root)
    if lpath:
        print(f"  {lpath.relative_to(root)} drafted from this repo "
              f"({edges} existing edge(s) to prune)")
    # Layer 1 is generated from the kind table, and this command just changed
    # that table. `registry-consistency` catches a stale CLAUDE.md, and it
    # caught this one: `init` wrote it with three kinds and `install` added
    # twenty more without regenerating, so the doctrine described a repo that
    # no longer existed.
    try:
        from . import doctrine as _doc
        _doc.write(config_mod.RepoConfig(root))
        from .hosts import doctrine_files
        print("  " + ", ".join(p.name for p in doctrine_files(root)) + f" regenerated for {len(kinds)} kind(s)")
    except Exception as exc:                                    # noqa: BLE001
        print(f"  CLAUDE.md NOT regenerated ({exc}) -- run `v4 doctrine --write`")
    # Last, because it has to hash what everything above just wrote. Without it
    # these land in the next task's diff as changes the worker cannot explain:
    # measured, three of the eight paths `scope` flagged on a real run.
    from .hosts import doctrine_files
    install_mod.stamp_generated(root, [
        ".v4/claim_kinds.json", ".v4/detectors.json", ".v4/checkers.json",
        *[p.name for p in doctrine_files(root)], "bin/v4",
        # `write_launcher` appends to it; a path this command writes and does
        # not stamp is a diff the next worker cannot account for.
        ".gitignore",
    ])
    return lenses


def _install_what_is_left(root, held, refused, det_bad, lenses, no_subject):
    """Everything installed and not yet working, said once, at the end."""
    if held:
        print(f"\n{len(held)} kind(s) held back -- each says why. Some are "
              f"waiting on a file you have not written, some on any file their "
              f"checker can read; the rest are about this framework rather "
              f"than your repo:")
        for name, why in held:
            print(f"  {name:<16} {why}")
    if refused or det_bad:
        print(f"\n{len(refused) + len(det_bad)} REFUSED by their own fixtures:")
        for cid, why in refused + det_bad:
            print(f"  {cid:<16} {why}")
    # The after-gate, on the same terms as the hooks below it. `v4 sweep`
    # answers "is it due" and starts nothing -- SPEC.md §10.1 -- so the trigger
    # is a cron the adopter has to write, and until they do, every rule that
    # landed in a lens has never been read. In this framework's own repo that
    # was 145 of 254.
    if lenses:
        print(f"\n{lenses} lens(es) installed and nothing calls them yet. "
              f"`v4 sweep` says when they are due; something has to ask:")
        print("  v4 --repo . sweep --if-due        # exits 1 when it is not due")
        print("  # a cron, or a scheduled CI job. `v4 doctor` reports it "
              "until one exists.")
    from . import hosts
    for host in hosts.selected(root):
        template = ".claude/settings.template.json" if host == "claude" else ".codex/hooks.template.json"
        settings = ".claude/settings.json" if host == "claude" else ".codex/hooks.json"
        print(f"\n  {host} host: {template} supplies the handlers for {settings}")
        hooks = sorted(p.name for p in (root / "hooks").glob("*.py")
                       if not p.name.startswith("_"))
        settings_path = root / settings
        try:
            body = settings_path.read_text() if settings_path.is_file() else ""
        except OSError:
            body = ""
        unwired = [name for name in hooks if name not in body]
        if unwired:
            print(f"  {len(unwired)} of {len(hooks)} hook(s) have no reference in {settings}")
            for name in unwired:
                print(f"  {name}")
            print(f"  merge {template} into {settings}" if settings_path.is_file()
                  else f"  cp {template} {settings}")
        print("  --activate-hooks merges framework handlers while preserving unrelated settings")
        if host == "codex":
            print("  review and trust the definitions in Codex /hooks; a file is not execution evidence")
    if no_subject:
        print(f"\n{len(no_subject)} checker(s) have nothing to read in this repo "
              f"yet. They are registered and they work; every task will raise "
              f"their claim and get exit 4, which does not close a task:")
        for cid, said in no_subject:
            print(f"  {cid:<16} {said}")
        print("  add what they look for, or once a task has run:")
        print("    v4 risk accept --claim <id> --kind unprovable --scope repo --why '…'")
    print("\n  v4 --repo . doctor      # 逐項講返仲欠乜")


def cmd_install(args):
    """Copy the rules in and register every one of them.  SPEC.md §8.

    The eleven jobs below are named because this was 224 lines of them
    interleaved, and it is the first command an adopter runs -- so the one
    function in this file with no seam in it was the one on the path a
    newcomer has to debug.
    """
    from . import install as install_mod
    root, src = _repo(args), install_mod.source_root()
    if root.resolve() == src:
        print("REFUSED: that is this framework's own repo", file=sys.stderr)
        return 2
    if not (root / config_mod.CONFIG).is_file():
        print("REFUSED: run `v4 init` first -- there is no .v4/config.json here",
              file=sys.stderr)
        return 2

    if getattr(args, "check", False):
        return _install_check(install_mod, src, root, getattr(args, "hosts", None))

    from . import hosts
    # Asked before anything is written. `write_kinds` merges rather than
    # replaces, so a kind dropped afterwards left the earlier write standing and
    # `derive` went on raising a claim whose checker was never registered.
    kinds, held = install_mod.installable_kinds(src, root)
    print(f"installing from {src}\n")
    reg_src = json.loads((src / config_mod.CHECKERS).read_text())

    # Select candidate host assets without committing the choice before the
    # corpus preflight. A refused custom-lens upgrade must not change hosts.
    _install_copy(install_mod, src, root, kinds, getattr(args, "hosts", None))
    if getattr(args, "hosts", None):
        hosts.configure(root, args.hosts)

    conn = ledger.connect(root)
    # Read once. This sat inside both registration loops, so a full `RepoConfig`
    # was built once per registration -- 31 checkers plus 30 detectors on this
    # repo, each construction re-reading four JSON files and re-running
    # `facts.validate`. It was also re-read *after* `_install_copy` had rewritten
    # `.v4/config.json` and `.v4/claim_kinds.json`, so the table handed to later
    # registrations was not the one handed to earlier ones -- a worse problem
    # than the cost.
    facts = _cfg(args).facts or None
    _ok, refused, no_subject = _install_checkers(
        install_mod, conn, root, kinds, reg_src, facts)
    _det_ok, det_bad, _missing = _install_detectors(
        install_mod, conn, root, src, facts)
    lenses = _install_artefacts(install_mod, root, src, kinds)
    _install_what_is_left(root, held, refused, det_bad, lenses, no_subject)
    if getattr(args, "activate_hooks", False):
        selected = hosts.selected(root)
        for host in selected:
            print(f"  hooks merged: {hosts.activate_hooks(root, host).relative_to(root)}")
        # A Codex-specific trust reminder belongs only to Codex installations.
        if "codex" in selected:
            print("  Codex hooks still require host trust; inspect /hooks before relying on them.")
    return 1 if refused or det_bad else 0


def _install_check(install_mod, src, root, host_choice=None):
    """`v4 install --check` -- what this repo would get if it ran `install`.

    The question `.v4/installed.json` could not answer. It records the bytes
    this framework *shipped*, so it tells an edit from an untouched copy -- and
    says nothing about the framework having moved on since, which is the way an
    adopter actually goes stale. Measured on the reference adopter the day this
    was written: 99 current, 18 behind, 2 missing, 4 edited here. Two of the
    eighteen were `.github/monitor/*.md`, the brief a monitor session pastes,
    whose first of four duties named a claim nothing raises any more.

    The only mechanism that would have caught that is `PROMPT.md` §3 -- a person
    reading the sync diff. Which is one of the files that was stale.

    Reads and prints. `install` is the thing that writes.
    """
    rows = install_mod.copy_files(
        src, root, install_mod.installable_kinds(src, root)[0], dry=True, host_choice=host_choice)
    by = {}
    for rel, how in rows:
        by.setdefault(how, []).append(rel)
    print(f"checking {root} against {src}\n")
    for how, why in (
        (install_mod.BEHIND, "the framework has moved on; this repo has the old copy"),
        (install_mod.MISSING, "the framework ships it and this repo has none"),
        (install_mod.YOURS, "edited here -- `v4 install` leaves these alone"),
        (install_mod.CURRENT, "same bytes as the framework"),
    ):
        got = by.get(how, [])
        print(f"  {len(got):>4} {how:<8} {why}")
    for how in (install_mod.BEHIND, install_mod.MISSING, install_mod.YOURS):
        for rel in by.get(how, []):
            print(f"    {how:<8} {rel}")
    stale = len(by.get(install_mod.BEHIND, [])) + len(by.get(install_mod.MISSING, []))
    if stale:
        print(f"\n{stale} file(s) `v4 install` would bring over. Nothing was written.")
    return 1 if stale else 0


def cmd_accept(args):
    """`v4 accept` -- run the gates, in a tree that carries no local state.

    Named for what it is: the acceptance a batch of work has to pass before
    anybody says it is done. `v4 check` answers one claim; this answers "would
    a fresh clone of this still hold", which is a different question and was
    being asked by hand.
    """
    from . import accept as accept_mod
    root = _repo(args)
    parts = dict(tests=args.tests or not (args.fixtures or args.docs),
                 fixtures=args.fixtures or not (args.tests or args.docs),
                 docs=args.docs or not (args.tests or args.fixtures))

    def show(step, ok, detail):
        print(f"  {'ok  ' if ok else 'FAIL'} {step:<22} {detail}")

    if args.here:
        print(f"accepting {root} as it stands -- local state included\n")
        rows = accept_mod.run(root, on_step=show, **parts)
    else:
        import tempfile as _tf
        with _tf.TemporaryDirectory() as td:
            tree = accept_mod.archive(root, Path(td))
            print(f"accepting the staged index of {root} in a fresh tree at {tree}\n")
            rows = accept_mod.run(tree, on_step=show, **parts)
    bad = [s for s, ok, _d in rows if not ok]
    print(f"\n{len(rows) - len(bad)}/{len(rows)} held"
          + (f" -- {', '.join(bad)}" if bad else ""))
    if not bad:
        print("  what that means: this tree passes its own gates from a clean\n"
              "  checkout. What it does not mean: that the gates are complete.")
    return 1 if bad else 0


def cmd_verify(args):
    """Run a checker against its fixtures.  Read-only: nothing is registered."""
    conn = ledger.connect(_repo(args))
    try:
        rnd = register.assert_ruler_unmoved(conn, _acceptance(args))
    except register.RulerMoved as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    if rnd:
        print(f"round   : {rnd['label']}  (criteria {rnd['acceptance_sha']})")
    ok, report = register.verify_checker(
        repo_root=_repo(args),
        checker_path=Path(args.checker).resolve(),
        fixtures_dir=Path(args.fixtures).resolve(),
        kind=args.kind,
        # The gate has to hand over the same table production does, or the
        # fixtures exercise a fallback that never runs for real.
        facts=(json.loads(Path(args.facts).read_text()) if args.facts
               else (_cfg(args).facts or None)),
        timeout_sec=args.timeout,
        min_cases=args.min_cases,
    )
    print(f"checker : {report['checker']}")
    print(f"sha256  : {report['checker_sha'][:16]}")
    print(f"cases   : {report.get('passed', 0)}/{report.get('total', 0)} as expected")
    for c in report["cases"]:
        mark = "ok  " if c["ok"] else "FAIL"
        print(f"  {mark} {c['colour']:<5} {c['case']:<40} want {c['want']} got {c['got']}")
    if report["failures"]:
        print("\nfailures:")
        for f in report["failures"]:
            print(f"  - {f}")
    print("\nVERDICT:", "registrable" if ok else "NOT registrable")
    # And what that verdict is about, because the word is easy to read as more.
    #
    # Fixtures answer one question: does this program tell the two states
    # apart, and does it survive somebody who knows the rule. They do not
    # answer "is the rule complete" and they cannot. Measured on
    # `fail-closed`, whose rule is a table of 170 names: removing any one of
    # them leaves every fixture green in 168 of the 170 cases, and ten of its
    # twelve tables can be emptied whole with the gate still passing. That is
    # arithmetic -- eleven red cases cannot pin 170 names -- and it is worth
    # printing rather than leaving for somebody to discover.
    if ok:
        print("  what that means: this program tells red from green on "
              f"{report.get('total', 0)} case(s), including bypasses.\n"
              "  what it does not mean: that the rule behind it is complete. "
              "Fixtures pin behaviour, not vocabulary.")
    return 0 if ok else 1


def cmd_register_detector(args):
    """Verify, then write the result down.  SPEC.md §2.

    `verify-detector` on its own told a person and told nothing else, so the
    requirement that a conditional detector pass the gate could not be enforced
    by anything. This is the same command with somewhere to put the answer.
    """
    conn, cfg = ledger.connect(_repo(args)), _cfg(args)
    ok, report = register.register_detector(
        conn, detectors_json=_repo(args) / config_mod.DETECTORS,
        detector_path=Path(args.detector).resolve(),
        fixtures_dir=Path(args.fixtures).resolve(),
        repo_root=_repo(args), facts=cfg.facts or None,
        timeout_sec=args.timeout, min_cases=args.min_cases)
    print(f"detector: {Path(args.detector).name}")
    print(f"cases   : {report.get('passed', 0)}/{report.get('total', 0)} as expected")
    for f in report["failures"]:
        print(f"  FAIL {f}")
    print("registered" if ok else "NOT registered")
    return 0 if ok else 1


def cmd_verify_detector(args):
    ok, report = register.verify_detector(
        repo_root=_repo(args), detector_path=Path(args.detector).resolve(),
        fixtures_dir=Path(args.fixtures).resolve(),
        facts=_cfg(args).facts or None,
        timeout_sec=args.timeout, min_cases=args.min_cases)
    print(f"detector: {report['detector']}")
    print(f"cases   : {report.get('passed', 0)}/{report.get('total', 0)} as expected")
    for c in report["cases"]:
        print(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['colour']:<5} {c['case']:<40} "
              f"{c['claims']} claim(s)")
    for f in report["failures"]:
        print(f"  - {f}")
    checked, st_problems = register.self_trigger(
        repo_root=_repo(args), detector_path=Path(args.detector).resolve(),
        fixtures_dir=Path(args.fixtures).resolve(),
        facts=_cfg(args).facts or None, timeout_sec=args.timeout)
    if checked is None:
        print("\nself-trigger: no cases declared. A conditional detector should "
              "have them -- see SPEC.md §4.")
    else:
        print(f"\nself-trigger: {checked} run(s), "
              f"{'clean' if not st_problems else str(len(st_problems)) + ' problem(s)'}")
        for pb in st_problems:
            print(f"  - {pb}")
        ok = ok and not st_problems
    print("\nVERDICT:", "usable" if ok else "NOT usable")
    return 0 if ok else 1


def cmd_register(args):
    conn = ledger.connect(_repo(args))
    try:
        register.assert_ruler_unmoved(conn, _acceptance(args))
    except register.RulerMoved as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    ok, report = register.register(
        conn,
        checkers_json=Path(args.registry).resolve(),
        checker_id=args.id,
        checker_path=Path(args.checker).resolve(),
        kinds=args.kinds.split(","),
        fixtures_dir=Path(args.fixtures).resolve(),
        timeout_sec=args.timeout,
        repo_root=_repo(args),
        reads=[g.strip() for chunk in (args.reads or [])
               for g in chunk.split(",") if g.strip()],
        # The same table production gets. Registering with none while `verify`
        # passes one means the two commands hand the checker different inputs.
        facts=_cfg(args).facts or None,
    )
    # Formatted, not a raw dump sliced at 4000 characters. `cases` is built
    # before `failures` and json.dumps preserves that order, so for a checker
    # with fifteen fixture cases the whole diagnosis fell past the cut -- and
    # nothing said the output had been truncated. `cmd_verify` reads the same
    # dict and prints it properly; this is the command that writes the registry
    # and it was the illegible one, and the one `doctor` tells people to run.
    # `register` merges per-kind reports and keeps only `checker_sha`; the
    # per-kind `verify_checker` report is the one carrying `checker`. Print what
    # the caller asked for rather than a key that is absent here.
    print(f"checker : {args.checker}")
    print(f"sha256  : {str(report.get('checker_sha'))[:16]}")
    print(f"cases   : {report.get('passed', 0)}/{report.get('total', 0)} as expected")
    for c in report.get("cases") or []:
        if c.get("ok"):
            continue                      # the failures are what a reader needs
        print(f"  FAIL {c.get('colour', ''):<5} {c.get('case', ''):<40} "
              f"want {c.get('want')} got {c.get('got')}")
        head = (c.get("stdout_head") or "").strip()
        if head:
            for line in head.splitlines()[:4]:
                print(f"         {line}")
    if report.get("failures"):
        print("\nfailures:")
        for f in report["failures"]:
            print(f"  - {f}")
    if ok:
        print("registered")
    elif report.get("found_no_fixtures"):
        # Not the same refusal, so not the same sentence. This one is about the
        # command, and the registry was left as it was.
        print(f"REFUSED -- no fixtures under {args.fixtures}. Nothing was "
              f"unregistered; check the path.")
    else:
        print("REFUSED -- fixtures did not hold")
    for row in report.get("no_subject") or []:
        print(f"\nWARNING  {row['kind']}: this repo has no subject for it.")
        print(f"         it said: {row['said']}")
        print(f"         it is registered and it works -- there is just nothing "
              f"here to read. Until that changes, every task raises this claim, "
              f"the checker exits 4, and 4 does not close a task.")
        print(f"         either add what it looks for, or run `v4 check` once "
              f"and sign it off for the whole repo:")
        print(f"           v4 risk accept --claim <id> --kind unprovable "
              f"--scope repo --why '…'")
    return 0 if ok else 1


def cmd_round(args):
    conn = ledger.connect(_repo(args))
    if args.action == "open":
        try:
            sha = register.open_round(conn, _acceptance(args), args.label)
        except register.RulerMoved as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        print(f"round '{args.label}' open; criteria pinned at {sha}")
        print("the acceptance file cannot change until this round closes")
    else:
        rnd = register.current_round(conn)
        register.close_round(conn, args.label, args.note or "")
        print(f"round '{args.label}' closed")
        if rnd:
            print("measurements taken under these criteria stand; amending now "
                  "means re-running them, not grandfathering them")
    return 0


def _cfg(args):
    return config_mod.RepoConfig(_repo(args))


def cmd_task(args):
    conn = ledger.connect(_repo(args))
    # Two lists parsed two ways in one call was the first defect here: the raw
    # split stored ' b/**' with the space, which matches nothing, so the task
    # ran with a glob that silently covered no file. They were made to agree
    # with each other and still disagreed with `--add`/`--drop` next door, so
    # both go through `_list_arg` now and the CLI has one answer.
    forbid = _list_arg(args.forbid)
    scope = _list_arg(args.scope)
    if not scope:
        print("REFUSED: --scope named no glob. A task with no scope can write "
              "nothing, which is not a task.")
        return 2
    base = lifecycle.open_task(conn, _cfg(args), base=args.base, task_id=args.id,
                               request=args.request,
                               scope_globs=scope,
                               forbid_globs=forbid, after=args.after)
    # What was stored, not what was typed: the two differ exactly when the
    # stripping above did something, which is when a reader most needs to see it.
    print(f"task {args.id} open at {base[:12]}  scope={','.join(scope)}"
          + (f"  forbid={','.join(forbid)}" if forbid else "")
          + (f"  after={args.after}" if args.after else ""))
    return 0


def cmd_abandon(args):
    """End a task without shipping it, on the record.

    Until the hooks started inferring their task, an unshipped task was inert --
    nothing read it, and one left open cost nothing. They read it now: the newest
    unshipped task is the scope every write is checked against. A throwaway task
    opened to probe something therefore became the permanent guard, and the only
    way out was to ship work that was never done, which is worse than leaving it.

    So a task gets a second ending. It is an append like every other, it carries
    a reason with the same floor as `--not-done` and `scope widen`, and `ship`
    still refuses to touch it -- abandoning is not a way to pass anything, it is
    a way to say out loud that this one is over.
    """
    conn, cfg = ledger.connect(_repo(args)), _cfg(args)
    # `lifecycle.abandon`, which is where the other ending lives. Every refusal
    # and the terminal event itself used to be built here, on the entry
    # surface, while `shipped` is written by `lifecycle.ship` -- so one of a
    # task's two endings answered to the rules this layer enforces and the
    # other did not, and nothing but argv could reach it.
    try:
        unsettled = lifecycle.abandon(conn, cfg, args.task, args.why)
    except lifecycle.CannotAbandon as exc:
        prefix = "" if str(exc).startswith("no such task") else "REFUSED: "
        print(f"{prefix}{exc}", file=sys.stderr)
        return 2
    print(f"{args.task} abandoned. Its claims stay in the ledger -- what was "
          f"found does not stop being true.")
    if unsettled:
        print(f"\n{len(unsettled)} finding(s) this task never settled -- not "
              f"answered, not signed, not retracted:")
        for cid, kind, file, symbol in unsettled[:10]:
            where = (file or "") + (f"::{symbol}" if symbol else "")
            print(f"  {cid}  {kind:<14} {where}")
        if len(unsettled) > 10:
            print(f"  … and {len(unsettled) - 10} more")
        print("\nThe next task starts from a new base, so a delta gate will "
              "find no delta and stop asking. That is not the finding being "
              "wrong; it is the finding being unasked. `v4 doctor` counts "
              "these until somebody signs or repairs them.")
    return 0


def inherited_claims(root, rows, changed, lines):
    """`(on an untouched file, on an untouched symbol)`.

    A file, then a symbol. Counting files alone was blind in the one place the
    count matters most: a change that adds sixteen lines to a large module puts
    that module in `changed`, so every claim the module had ever earned was
    reported as this task's own work. Measured on an adopter: 26 claims on one
    file, 23 of them on symbols the diff never entered, and the line that exists
    to show that cost before it is paid printed nothing.

    `lines` is `None` when the diff could not be read, and a language with no
    span reader returns `None` too. Both mean the same thing here and are
    treated the same way: nothing in that file is reported as untouched.
    Guessing would hand the task somebody else's code, which is the error this
    report exists to prevent, arriving through the report itself.
    """
    from .analysis import symbols as _symbols
    owners = {}
    if lines is not None:
        for f in {c["file"] for c in rows if c["file"] in changed}:
            owners[f] = _symbols.owner_of_line(Path(root) / f, lines.get(f, ()))
    on_file = [c for c in rows if c["file"] not in changed]
    on_symbol = [c for c in rows
                 if c["file"] in changed and c["symbol"]
                 and owners.get(c["file"]) is not None
                 and c["symbol"] not in set(owners[c["file"]].values())]
    return on_file, on_symbol


def _retraction_lines(retracted):
    """`["  - <id>  retracted: <why>", ...]` for what a derive withdrew.

    One reader for a shape one function produces. `derive._retract_orphans`
    always returns a four-tuple, and both call sites decoded it by index behind
    `isinstance` guards that cannot fire -- `cmd_derive` and `cmd_scope`, four
    lines each, word for word. The dead fallback also named only one of the two
    real reasons ("the code it was about is gone"), so the copy that ran and
    the copy that could not both said something the producer contradicts.
    """
    return [f"  - {cid}  retracted: {why}"
            for cid, _kind, _files, why in retracted]


def cmd_derive(args):
    conn, cfg = ledger.connect(_repo(args)), _cfg(args)
    res = lifecycle.derive(conn, cfg, args.task, phase=args.phase)
    for cid, kind, file, sym in res["created"]:
        print(f"  + {cid}  {kind:<14} {file}{('::' + sym) if sym else ''}")
    for name, why, detail in res["refused"]:
        print(f"  ! {name}: {why} -- {detail}")
    # Retraction is the only route to a terminal state with no checker and no
    # signature -- `state.claim_state` returns RETRACTED on one event and
    # RETRACTED is in TERMINAL -- so claims stop being owed and nothing said
    # so, here or afterwards. The counts on the next line are unaffected by it,
    # so a derive that retracted six looked exactly like one that retracted none.
    # The reason the retraction itself recorded. There are two -- a subject
    # that is gone, and a rule that was narrowed after the claim it raised had
    # already passed -- and one sentence for both would say the wrong thing
    # about half of them. `_retraction_lines` is the one reader of that shape.
    for line in _retraction_lines(res.get("retracted") or []):
        print(line)
    print(f"{len(res['created'])} claim(s) from {len(res['detectors_ran'])} detector(s)"
          + (f", {len(res['retracted'])} retracted" if res.get("retracted") else ""))

    # How many of them are about files this task has not touched.
    #
    # `scope` says where work may go, so a detector raises across the whole
    # glob and not across the diff. That is deliberate -- sometimes auditing a
    # whole area is the point -- and nothing said what it cost. Measured on an
    # adopter: a scope of `tests/chat/**` pulled in 43 pre-existing
    # findings across seven files the task never touched and stalled at 3 of
    # 77; narrowing the glob to the nine real files took it to 39.
    #
    # A number, not a gate. Whether inherited debt belongs in this task is a
    # judgement, and the only thing missing was being able to see it before
    # paying for it.
    from .analysis import subject_files as _sf
    from . import state as _state
    # Signed `unprovable` eleven times, always arguing that `changed = None`
    # disables a report and not a verdict. **That was right**, and a review of
    # the repair had to say so: `changed` is read below and nowhere else, the
    # claims are printed above it out of `lifecycle.derive`, so a swallowed
    # ledger read and an unreadable diff cost the same hint and nothing more.
    #
    # The rule was right about the shape and the signatures were right about
    # the cost. What is wrong is a guard drawn around a call with no degraded
    # state to degrade to: `changed_since` raises on purpose (rewritten
    # history, shallow clone, a base this checkout lacks) so its caller has to
    # choose, and `conn.execute` makes no such promise -- one `None` cannot
    # honestly stand for both. A bare `except Exception` here once swallowed a
    # `NameError` and printed a clean derive; that wanted a narrow tuple, this
    # wants a narrow scope.
    #
    # It costs nothing today, and that is why it took eleven signatures for
    # anyone to just fix it.
    row = conn.execute("SELECT base_commit FROM task WHERE id = ?",
                       (args.task,)).fetchone()
    base = row["base_commit"] if row else ""
    try:
        changed = set(_sf.changed_since(cfg.root, base)) if base else None
    except (OSError, _sf.DiffUnreadable):
        changed = None
    if changed is not None:
        # A hand-raised finding can explicitly ask for missing tests around
        # unchanged product code. It is assigned work, not detector scope debt.
        rows = [c for c in _state.task_claims(conn, args.task)
                if c["file"] and c["origin"] == "derive"]
        try:
            lines = _sf.changed_lines(cfg.root, base)
        except (OSError, _sf.DiffUnreadable):
            lines = None
        untouched_file, untouched_symbol = inherited_claims(
            cfg.root, rows, changed, lines)
        inherited = sorted({c["file"] for c in untouched_file})
        if untouched_file or untouched_symbol:
            if untouched_file:
                print(f"  {len(untouched_file)} of them are on {len(inherited)} "
                      f"file(s) this task has not changed against {base[:12]}:")
                for f in inherited[:4]:
                    print(f"    {f}")
                if len(inherited) > 4:
                    print(f"    …and {len(inherited) - 4} more")
            if untouched_symbol:
                where = sorted({f"{c['file']}::{c['symbol']}"
                                for c in untouched_symbol})
                print(f"  {len(untouched_symbol)} more are in files this task did "
                      f"change, on symbols it did not:")
                for w in where[:4]:
                    print(f"    {w}")
                if len(where) > 4:
                    print(f"    …and {len(where) - 4} more")
            print("  These detector findings concern unchanged code. Check "
                  "whether the request includes them before changing scope. "
                  "For verified inherited debt, a delta kind's `--emit-baseline` "
                  "route still requires its normal review and authorization.")

    # Which of these will ask for a sentence, said here rather than at `check`.
    # SPEC.md §9 argues the whole value of this layer is that it lands before
    # the work: "refusing before anything is written costs nothing; refusing
    # after twenty minutes is a different mechanism at a different price." The
    # enforcement point has to stay in `check` -- that is where a claim is
    # answered -- but the worker used to find out only there, having already
    # written the code. That is the loop the argument is against, arriving
    # through the reporting rather than through the rule.
    from . import engagement, state as state_mod
    owed = []
    for row in state_mod.task_claims(conn, args.task):
        if engagement.required_for(cfg, row) and \
                not engagement.accepted_for(conn, row["id"]):
            owed.append(row)
    if owed:
        print(f"\n{len(owed)} claim(s) need a sentence before `v4 check` will "
              f"run their checker:")
        for row in owed[:10]:
            print(f"  engage {row['id']}  {row['kind']:<14} "
                  f"{row['file'] or ''}{('::' + row['symbol']) if row['symbol'] else ''}")
        if len(owed) > 10:
            print(f"  … and {len(owed) - 10} more")
        print(f"\n  v4 --repo . engage --claim <id>            印呢個 kind 嘅規則\n"
              f"  v4 --repo . engage --claim <id> --text '…'  寫佢對呢段 code 意味咩")
    return 0


def cmd_check(args):
    conn, cfg = ledger.connect(_repo(args)), _cfg(args)
    # `--claim` is `append` + `nargs="*"`, so both `--claim a b` and
    # `--claim a --claim b` arrive, and neither loses the other.
    only = set(_list_arg(args.claim)) or None
    held = []
    for row, cached, res in lifecycle.check(conn, cfg, args.task, only=only,
                                            run_expensive=args.all):
        if cached == "SKIPPED_EXPENSIVE":
            # `res` carries the cheap kinds that held it back. The list was
            # built in `lifecycle.check` and read nowhere, so this printed
            # "something cheaper is still failing" and named none of them.
            held.append((row, res or []))
            continue
        if res is None:
            # `(unchanged)` was printed for every `res is None` row, including
            # NEEDS_ENGAGEMENT -- where nothing ran, so there is no previous
            # answer for it to be unchanged from, and the line named no way
            # past. 17 of this repo's 29 kinds ask for a sentence, so this is
            # the common line, not the rare one. `v4 status` does not carry
            # NEEDS_ENGAGEMENT either (it is not one of `state`'s values), so
            # this print was the only place it appeared.
            if cached == "NEEDS_ENGAGEMENT":
                print(f"  --  {row['id']}  {row['kind']:<14} needs a sentence "
                      f"before it runs")
                print(f"        v4 --repo . engage --claim {row['id']}"
                      f"            印呢個 kind 嘅規則")
                print(f"        v4 --repo . engage --claim {row['id']} --text '…'"
                      f"  寫佢對呢段 code 意味咩")
            else:
                print(f"  = {row['id']}  {row['kind']:<14} {cached} (unchanged)")
        else:
            # `exit 7` was the whole report on a state the reader cannot name
            # from the number: the kernel diagnoses 6, 7 and 8 itself and no
            # command mapped them back to what `runner` calls them.
            mark = ("PASS" if res.exit_code == 0
                    else f"exit {runner.exit_name(res.exit_code)}")
            print(f"  {'ok  ' if res.exit_code == 0 else 'FAIL'} {row['id']}  "
                  f"{row['kind']:<14} {mark}  {res.duration_ms} ms")
            if res.exit_code != 0:
                # Both streams, not one-or-the-other. The two failures the
                # kernel diagnoses itself carry a checker's partial stdout *and*
                # the kernel's own sentence on stderr -- SUBJECT_MOVED ("the
                # working tree changed while the checker ran; this answer
                # describes neither state") and TIMEOUT ("checker exceeded
                # 300s") -- and `res.stdout or res.stderr` printed the partial
                # output and swallowed the explanation. All a reader got was
                # the number.
                lines = "\n".join(
                    x for x in (res.stdout, res.stderr) if (x or "").strip()
                ).strip().splitlines()
                for line in lines[:6]:
                    print(f"        {line}")
                # A cap that says nothing reads as "that was all of it". It was
                # not: `secret-chain` and `test-token-shape` print the baseline
                # id to add for each finding, and the ids were exactly what fell
                # past line six on a real run.
                if len(lines) > 6:
                    print(f"        … {len(lines) - 6} more line(s) -- "
                          f"`v4 status --task {args.task} --detail`")
    for row, by in held:
        secs = lifecycle.kind_cost(conn, row["kind"]) / 1000
        print(f"  --   {row['id']}  {row['kind']:<14} not run ({secs:.0f}s)"
              + (f" -- held by {', '.join(by)}" if by else ""))
    if held:
        print(f"\n{len(held)} expensive claim(s) not run: something cheaper is "
              f"still failing.\n"
              f"A repo-scoped claim is keyed on the content of the tree, so the "
              f"edit that answers\nthose failures expires this answer before you "
              f"can read it. Answer them first;\nthis runs by itself on the next "
              f"`v4 check`.\n"
              f"  v4 --repo . check --task {args.task} --all      # run them anyway")
    return 0


def _refused_widens(u) -> str:
    """The tail on every widen line, or `""` when nothing was refused.

    `scope.usage` returned `refused` and nothing anywhere read it, so a widen
    that was written, judged and turned down was invisible in all three places
    the widen count is printed -- while the count itself included it. Silent on
    zero, because "refused 0" on every task is a line people stop seeing, and
    this one has to be noticed the once it is not zero.
    """
    n = u.get("refused") or 0
    return (f"; {n} more refused -- the sentence did not pass, so the "
            f"path was never added") if n else ""


def cmd_status(args):
    """What is open, and why.  `--json` for something other than a person.

    An orchestrator -- a person driving several tasks, or an agent doing it --
    reads this to decide what to do next, and the only machine-readable thing
    here was the exit code: 0 if no claim blocks, 1 otherwise. Which
    claim, in which state, and what is blocking it had to be recovered by
    parsing lines written for a human, and those lines change whenever the
    wording improves.

    The exit code stays exactly what it was. This adds the same facts in a
    shape that does not move.
    """
    conn, cfg = ledger.connect(_repo(args)), _cfg(args)
    rows, blocked, reported = lifecycle.report(conn, cfg, args.task)
    # The continuation edge. `open_task` writes a `task_continues` event
    # specifically so that "what continued t-dead-tests" stops living inside
    # `task.request` where only a regex could find it -- and then `continues`
    # and `continued_by` had no production caller, so the query still had no
    # answer anybody could ask for. This is where an orchestrator looks.
    _after = lifecycle.continues(conn, args.task)
    _before = lifecycle.continued_by(conn, args.task)
    if getattr(args, "json", False):
        u = scope_mod.usage(conn, cfg, args.task)
        # `risk.signatures`, not the join written out here. This entry surface
        # held the same query three times -- twice in this command and once in
        # `ship` -- about a table `kernel/risk.py` owns.
        by_kind, by_signer, _line = risk.signatures(conn, args.task)
        print(json.dumps({
            "task": args.task,
            "continues": _after,
            "continued_by": _before,
            # `note` is here because of what this output is for. A review
            # finding's note *is* the finding -- "what did the lens see" --
            # and an orchestrator handed thirty of them has file, symbol and
            # lens but nothing to cut tasks by. Every other kind carries it
            # too; it is where a detector puts what it saw.
            "claims": [{"id": r["id"], "kind": r["kind"], "state": st,
                        "question": r["question"],
                        # `review.current_note`, not the stored row: a
                        # correction is an event, because `claim` is
                        # append-only, and this is the reader that has to
                        # apply it. The comment below says a review finding's
                        # note *is* the finding.
                        "note": review.current_note(conn, r["id"], r["note"]),
                        "checker": r["checker"], "file": r["file"],
                        "symbol": r["symbol"], "variant": r["variant"],
                        "line": r["line"]}
                       for r, st in rows],
            "open": [r["id"] for r, _ in blocked],
            # The same two facts the printed line separates. `terminal` is
            # `state.TERMINAL`, and a consumer scripting against this got the
            # not-blocking count under that name -- so an open report kind read
            # as a claim somebody had answered.
            "terminal": sum(1 for _, st in rows if st in state.TERMINAL),
            "not_blocking": len(rows) - len(blocked),
            "total": len(rows),
            "scope": {"widens": u["widens"], "paths": len(u["paths"]),
                      "pct": u["pct"], "repo_files": u["repo_files"]},
            "signatures": by_kind,
            # Beside it rather than folded into it: a consumer that wants to
            # know how much of this task's terminal state rests on a second
            # session, or on a worker waiving its own judge, cannot get that
            # from the kind.
            "signed_by": by_signer,
        }, indent=2, ensure_ascii=False))
        return 0 if not blocked else 1
    if reported:
        # Printed before the claim list, not after it. A line at the bottom of
        # a forty-row report is a line that scrolls past, and the whole risk
        # this tier carries is that "13 reported" becomes furniture.
        esc = [x for x in reported if str(x[2]).startswith("ESCALATED")]
        print(f"\n  {len(reported)} claim(s) do not hold this task"
              + (f", {len(esc)} of them past a threshold and holding it anyway"
                 if esc else "")
              + ":")
        for row, st, why in reported:
            print(f"    {st:<12} {row['id'][:16]}  {row['kind']:<20} "
                  f"{str(why)[:74]}")
        print()
    if _after:
        print(f"  continues  {_after}")
    if _before:
        print(f"  continued by  {', '.join(_before)}")
    for row, st in rows:
        print(f"  {st:<14} {row['id']}  {row['kind']:<14} {row['question'][:56]}")
        if args.detail:
            # The text first. `v4 check` truncates a refusal at six lines and
            # points here for the rest, and this read only `checker_out` -- a
            # different artifact, the checker's `--out` JSON, which many
            # checkers never write at all. So the lines a reader was sent here
            # for were unreachable by any command, and for a FAIL path with no
            # `--out` this printed nothing. `attempt.stdout` is where they are.
            att = conn.execute(
                "SELECT stdout, stderr, exit_code FROM attempt WHERE claim_id = ? "
                "ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
            if att:
                text = (att["stdout"] or "") + (att["stderr"] or "")
                for line in text.strip().splitlines():
                    print(f"        {line}")
            out = conn.execute(
                "SELECT payload FROM event WHERE claim_id = ? AND kind = 'checker_out' "
                "ORDER BY id DESC LIMIT 1", (row["id"],)).fetchone()
            if out:
                body = json.dumps(json.loads(out["payload"]), indent=2,
                                  ensure_ascii=False).splitlines()
                for line in body[:60]:
                    print(f"        {line}")
                if len(body) > 60:
                    print(f"        … {len(body) - 60} more line(s) in the "
                          f"ledger's checker_out event")

    u = scope_mod.usage(conn, cfg, args.task)
    print(f"\nwidened {u['widens']} time(s), {len(u['paths'])} path(s) = {u['pct']}% "
          f"of {u['repo_files']} files"
          + _refused_widens(u))
    print("signatures: " + risk.signatures(conn, args.task)[2])
    # `terminal` is a defined word -- `state.TERMINAL` is ANSWERED,
    # RISK_ACCEPTED and RETRACTED -- and this line was counting something else:
    # everything that is not blocking, which includes an OPEN claim of a
    # `report` kind. Measured on this repo's own ship, `14/14 terminal` printed
    # beside a claim the same report listed as OPEN.
    #
    # Two numbers because there are two facts, and merging them is the failure
    # this repo has a standing rule about: whether the gate would pass, and how
    # many claims actually reached a terminal state. The second is the smaller
    # one whenever a report kind is open, and it is the one the word belongs to.
    settled = sum(1 for _, st in rows if st in state.TERMINAL)
    line = f"\n{settled}/{len(rows)} terminal"
    if len(rows) - len(blocked) != settled:
        line += (f", {len(rows) - len(blocked)}/{len(rows)} not blocking "
                 f"-- a report kind is open and does not hold the ship")
    print(line)
    return 0 if not blocked else 1


def _print_nothing_seen(rep) -> None:
    """The roll-up beside `NOT RUN`, as a function so a test can call it.

    It was four lines inline in `cmd_ship`, and the only test on it read
    `cli.py` for the string "NOTHING SEEN". A monitor session mutated the line
    to `if False and rep.get("nothing_seen"):` and all 777 tests stayed green:
    the source still held the string and the branch was dead. A grep is not a
    test of behaviour, and a report line nothing exercises is the same shape
    this roll-up exists to catch.
    """
    seen = rep.get("nothing_seen")
    if not seen:
        return
    names = [n for n, _ in seen]
    # The whole range, not `seen[0][1]` stated as the number for the group.
    # `detector_coverage` merges the newest `detector_run` per detector across
    # every derive round and phase, and `considered` is recomputed per round
    # from `subject_files` -- which a `scope widen` changes -- so the entries
    # can legitimately disagree, and the report picked one and spoke for all of
    # them.
    counts = sorted({int(n_files) for _, n_files in seen})
    over = (f"{counts[0]} file(s)" if len(counts) == 1
            else f"between {counts[0]} and {counts[-1]} file(s)")
    print(f"  NOTHING SEEN: {names}")
    print(f"    {len(names)} detector(s) ran over {over} and none of those "
          f"files matches what their own kind declares it reads. Their kinds "
          f"were not answered leniently -- they were not asked. "
          f"<- recorded, not assumed clean")


def cmd_ship(args):
    conn, cfg = ledger.connect(_repo(args)), _cfg(args)
    ok, rep = lifecycle.ship(conn, cfg, args.task)
    if not rep["converged"]:
        print("REFUSED: ship did not converge")
        print(f"  rounds produced: {rep['rounds']}")
        print(f"  {rep['why']}")
        return 1
    print(f"re-derive converged in {len(rep['rounds'])} round(s): {rep['rounds']}")
    print(f"chain: {'intact' if rep['chain_ok'] else 'BROKEN'}")
    # `chain_problems` gates the ship (`lifecycle.ship`: `ok = ... and chain_ok
    # ...`) and had no reader anywhere, so the word BROKEN was the entire report
    # on the one failure a person cannot diagnose from the outside. The strings
    # it holds are the actionable half -- "attempt 41: contents do not match
    # row_hash (edited in place)". `cmd_audit` already prints them this way.
    for p in rep.get("chain_problems") or []:
        print(f"  - {p}")
    # A dict repr of ~28 name-to-bool pairs, in the one report a person reads
    # before shipping. The names that matter are the ones that did not run, and
    # they are printed below under NOT RUN.
    _det = rep["detectors"]
    print(f"detectors: {sum(1 for v in _det.values() if v)}/{len(_det)} ran")
    if rep.get("detectors_all_dropped"):
        print(f"  ALL DROPPED: {rep['detectors_all_dropped']}  <- raised claims "
              f"and every one was out of scope or of a kind this repo does not "
              f"register; that is not the same as finding nothing")
    if rep.get("claim_origins"):
        print("claims   : " + ", ".join(f"{k}={v}" for k, v in
                                        sorted(rep["claim_origins"].items())))
    if rep["hook_seen"] == 0:
        print("  DEGRADED: the write hook never fired for this task. Scope was "
              "checked at ship, not while the code was being written -- so this "
              "task had no early warning, and that is recorded rather than assumed "
              "away.")
    for host, coverage in rep.get("hook_coverage", {}).items():
        if coverage["observed"]:
            print(f"  {host} hooks: observed {', '.join(coverage['observed'])}; "
                  f"checked {', '.join(coverage['checked']) or 'none'}")
    u = scope_mod.usage(conn, cfg, args.task)
    print(f"widened  : {u['widens']} time(s), {len(u['paths'])} path(s) = {u['pct']}%"
          + _refused_widens(u))
    print("signed   : " + risk.signatures(conn, args.task, empty="nothing")[2])
    if rep["detectors_not_run"]:
        print(f"  NOT RUN: {rep['detectors_not_run']}  <- recorded, not assumed clean")
    _print_nothing_seen(rep)
    _print_uncovered(cfg)
    for cid, kind, st in rep["blocked"]:
        print(f"  blocked  {cid}  {kind:<14} {st}")
    if rep.get("reported"):
        # Beside `blocked`, not after the verdict. The risk this tier carries
        # is that the line becomes furniture, and a count that sits under the
        # word SHIP is a count nobody reads twice.
        esc = [x for x in rep["reported"] if str(x[3]).startswith("ESCALATED")]
        print(f"reported : {len(rep['reported'])} claim(s) unanswered and not "
              f"holding this task"
              + (f" -- {len(esc)} past a threshold and holding it anyway"
                 if esc else ""))
        for cid, kind, st, why in rep["reported"][:5]:
            print(f"    {cid[:16]}  {kind:<20} {st:<12} {str(why)[:58]}")
        if len(rep["reported"]) > 5:
            print(f"    … {len(rep['reported']) - 5} more -- v4 status --task "
                  f"{args.task}")
    if rep.get("deferred"):
        print(f"deferred : {len(rep['deferred'])} finding(s) put off, each "
              f"naming where the work went")
        for cid, target in rep["deferred"][:5]:
            print(f"    {cid}  → {target[:60]}")
    for name in rep.get("facts_unconfirmed", []):
        print(f"  blocked  facts.absent[{name}] — the installer wrote 'this repo "
              f"has none', not a person. Every detector over {name} is reporting "
              f"a clean repo on a verb-ending sweep. Confirm it and rewrite the "
              f"line without AUTO:, or add the rows.")
    # Three states, because there are three. `lenses_run` records that a brief
    # printed; `lenses_reviewed` records that a reviewer came back and said
    # how many findings it had. Printing the first as the second is how this
    # ship said `reviewed by: near-miss` about a lens no reviewer had ever
    # read a diff with -- twice, on the two ships that built it.
    #
    # The middle state is the common one and had no words at all: somebody
    # took the brief and never reported. It is not a review and it is not
    # nothing, and a reader deciding whether to trust this ship needs it.
    briefed = set(rep.get("lenses_run") or [])
    reviewed = rep.get("lenses_reviewed") or {}
    if reviewed:
        print("\nreviewed by: " + ", ".join(
            f"{n} ({reviewed[n]} finding(s))" for n in sorted(reviewed)))
    else:
        print("\nreviewed by: no lens has reported on this task")
    unfinished = sorted(briefed - set(reviewed))
    if unfinished:
        print(f"  briefed and never reported back: {', '.join(unfinished)}"
              f" -- a brief that printed is not a review")
    print("\nSHIP" if ok else "\nHELD")
    return 0 if ok else 1


def cmd_risk(args):
    conn, cfg = ledger.connect(_repo(args)), _cfg(args)
    if args.action == "waiting":
        rows = risk.waiting(conn, cfg, args.task, ended=getattr(args, 'ended', False))
        # One block per claim, not one line. The line said `STALE ... test ...
        # Does the repo's declared test command pass?` and left the reader to
        # work out, per claim, whether a signature was even the right move --
        # and mostly it is not. `route` makes that call from the same facts
        # `claim_state` used, so the expensive exit is offered only where it is
        # the only one.
        need_signing = 0
        for row, st in rows:
            what, why = risk.route(conn, cfg, row, st)
            if what in (risk.SIGN_REPO, risk.SIGN_ONLY, risk.TASK_ENDED):
                need_signing += 1
            print(f"\n  {row['id']}  {row['kind']}  {st}")
            print(f"      asks     {row['question']}")
            if row["file"]:
                print(f"      subject  {row['file']}"
                      + (f"::{row['symbol']}" if row["symbol"] else ""))
            print(f"      do       {what}")
            for line in textwrap.wrap(why, 66):
                print(f"               {line}")
            if what in (risk.SIGN_ONLY, risk.TASK_ENDED):
                for cid, where, test in risk.closed_by_a_test(conn, row["kind"]):
                    print(f"      compare  {cid} was the same kind at {where}")
                    print(f"               and closed with {test}")
            if what in (risk.SIGN_REPO, risk.SIGN_ONLY, risk.TASK_ENDED):
                scope = " --scope repo" if what == risk.SIGN_REPO else ""
                # `--kind unprovable` spelled whole in the source, not
                # `unprovable{scope}`: `test_printed_exits_exist` parses this
                # file for every `--kind X` it prints and checks X against
                # `risk.KINDS`, and an f-string that splices onto the kind
                # hands it a name no parser would take. The test was right and
                # the output was fine, which is the shape that survives.
                print(f"      then     v4 risk accept --claim {row['id']}{scope} "
                      f"--kind unprovable --why '…'")
                print(f"               the record carries your git identity, your "
                      f"reason, whether\n"
                      f"               stdin was a terminal and which route "
                      f"signed it,\n"
                      f"               into the hash chain.")
        print(f"\n{len(rows)} claim(s) not terminal. "
              f"{need_signing} of them a signature could close; "
              f"{len(rows) - need_signing} want a re-run, a test, or a repair.")
        if not args.task and not getattr(args, "ended", False):
            hidden = len(risk.waiting(conn, cfg, None, ended=True)) - len(rows)
            if hidden > 0:
                print(f"{hidden} more on tasks that have ended, not shown. "
                      f"`--ended` lists them; `v4 doctor` counts them.")
        return 0
    try:
        record, path = risk.accept(conn, cfg, claim_id=args.claim, kind=args.kind,
                                   why=args.why, require_tty=not args.no_tty_check,
                                   scope=args.scope, as_monitor=args.as_monitor)
    except risk.RefusedToSign as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    # `risk.attribution`, not a line built here: `who` is a git identity and
    # `signed_by` is who actually signed, and a sentence about that difference
    # written on the entry surface is a sentence no test can reach.
    print(risk.attribution(record))
    print(f"record written to {path.relative_to(cfg.root)} -- commit it")
    if args.scope == risk.REPO:
        print(f"scope: repo -- every task's {record['claim_kind']} claim is "
              f"covered while the checker keeps exiting 4")
        print(f"lapses when: {record['lapses_when']}")
    else:
        print("this signature covers the bytes it saw; it expires when they move")
    return 0


def cmd_sweep(args):
    """Is the after-gate due, and what does a reviewer run.  SPEC.md §10.1."""
    from . import sweep as sweep_mod
    conn, cfg = ledger.connect(_repo(args)), _cfg(args)
    c = sweep_mod.config(cfg)

    if args.history:
        rows = sweep_mod.history(conn)
        if not rows:
            print("no sweep has been recorded in this repo")
            return 0
        # Both halves of both pairs. `record` stores `ran` beside `lenses` and
        # `raised_since_last_sweep` beside `findings`, and says why: "a sweep
        # that claims nine and shows two is a fact somebody should be able to
        # read afterwards". This printed the claimed number of each and dropped
        # the observed one, so the gap was readable only in the one second at
        # `--done` time when the reconcile message happened to fire.
        for when, p in rows:
            claimed, seen = p.get("findings"), p.get("raised_since_last_sweep")
            n_lens, n_ran = len(p.get("lenses") or []), len(p.get("ran") or [])
            # `None` and `{}` are different rows, not the same one. Every sweep
            # recorded before `reviewed` existed carries no such key, and
            # printing those as `0 reviewed` would be this command inventing a
            # measurement the row never took -- the failure one column over.
            rev = p.get("reviewed")
            if rev is None:
                lens_txt = (f"{n_lens} lens(es)" if n_lens == n_ran
                            else f"{n_lens} lens(es) claimed, {n_ran} ran")
            elif len(rev) == n_lens:
                lens_txt = f"{n_lens} lens(es) reviewed"
            else:
                lens_txt = (f"{n_lens} lens(es) available, {n_ran} briefed, "
                            f"{len(rev)} reviewed")
            if claimed is None:
                find_txt = ""
            elif seen is None or claimed == seen:
                find_txt = f", {claimed} finding(s)"
            else:
                find_txt = f", {claimed} finding(s) claimed and {seen} in the ledger"
            print(f"  {when[:19]}  {lens_txt}{find_txt}  {p.get('note', '')}")
        return 0

    if args.done:
        lenses = sorted(review.lenses(cfg.root))
        prev = sweep_mod.last_attempted(conn)
        briefed = sweep_mod.ran_since(conn, prev)
        reviewed = sweep_mod.reviewed_since(conn, prev)
        # Refused before the sweep is closed, not reported after. Both counts
        # have been stored since `record` was written and nothing compared
        # them -- 61 reported against 1 in the ledger, and no command had to
        # say a word about it.
        why_not = sweep_mod.reconcile(conn, args.findings, args.note,
                                      cfg.thresholds["min_chars"])
        if why_not:
            print(f"REFUSED: {why_not}", file=sys.stderr)
            return 2
        recorded = sweep_mod.record(conn, lenses=lenses, findings=args.findings, note=args.note or "")
        print(f"recorded: {len(lenses)} lens(es) available"
              f"{'' if args.findings is None else f', {args.findings} finding(s)'}")
        # The same three states `v4 ship` prints, because it is the same fact:
        # `lens_run` says a brief printed and printing one is free. This line
        # said `N of M printed their brief` and stopped there, so a sweep where
        # thirteen reviewers took a brief and none came back read exactly like
        # a sweep thirteen reviewers had finished.
        #
        # Not a refusal, either half of it: a sweep may deliberately skip a
        # lens, and gating on "did a judgement happen" buys a checkbox (§10.2).
        # What it buys is that the three stop looking like one.
        if reviewed:
            print(f"\n{len(reviewed)} of {len(lenses)} reported: "
                  + ", ".join(f"{n} ({reviewed[n]} finding(s))"
                              for n in sorted(reviewed)))
        else:
            print(f"\nno lens reported on this sweep -- "
                  f"`v4 review done --lens <name> --findings <n>` is how one does")
        unfinished = sorted(briefed - set(reviewed))
        if unfinished:
            print(f"  briefed and never reported back: {', '.join(unfinished)}"
                  f" -- a brief that printed is not a review")
        never = sorted(set(lenses) - briefed - set(reviewed))
        if never:
            print(f"  no brief printed since the last sweep: {', '.join(never)}")
        if recorded["complete"]:
            print(f"\nnext due in {c['every_days']} day(s)")
            return 0
        print("\nPARTIAL: complete-review cadence was not advanced. Use maintain start for versioned, resumable review.")
        return 1

    ok, why = sweep_mod.due(conn, cfg)
    print(f"{'DUE' if ok else 'not due'} -- {why}\n")
    print(f"schedule: every {c['every_days']} day(s)"
          + (f", weekday {c['weekday']}" if c.get("weekday") is not None else "")
          + (f", not before {int(c['not_before_hour']):02d}:00"
             if c.get("not_before_hour") is not None else ""))
    print("set it in .v4/config.json under \"lens_sweep\"")
    if not ok:
        return 1 if args.if_due else 0
    lenses = sorted(review.lenses(cfg.root))
    print(f"\n{len(lenses)} lens(es) to run over {cfg.root}:\n")
    for name in lenses:
        print(f"  v4 review lens --lens {name}")
    # The directories this repo actually has. `app/ and tests/` was hardcoded
    # and this repo has no `app/`, so a reviewer obeying the command it was
    # started by read a directory that does not exist and never reached the
    # kernel. `review.lens_brief` recorded the same contradiction being fixed on
    # its half ("the code as it stands"); the sweep half kept both the
    # contradiction and the adopter-specific paths.
    tops = sorted(d.name + "/" for d in cfg.root.iterdir()
                  if d.is_dir() and not d.name.startswith(".")
                  and any(d.rglob("*.py")))
    where = ", ".join(tops) if tops else "the code"
    print(f"\nReview {where} as they stand -- this is the after-gate, so it")
    print("reads code that exists, not a diff. Findings go in through")
    print("`v4 review add`, the same way any reviewer's finding does.")
    print("\nWhen the sweep is finished:  v4 sweep --done --findings <n>")
    return 0


def cmd_cover(args):
    """Account for one piece of the request.  SPEC.md §4.7.

    `measure` returns six things and this printed four. The two it dropped are
    the two that say the accounting is wrong rather than incomplete:

      `faults`   -- entries `fault()` threw out at measure time. They are still
                    in `entries()`, so the listing printed each of them as
                    `→ symbol`, delivered, while the ratio above had already
                    refused to count it. The one place a reader could have seen
                    the difference was the place that hid it.
      `unspoken` -- clauses nobody quoted at all. `missing` is a character
                    ratio, and its own comment says why that is not enough: a
                    request weighing 20 and 2 reads as 91% with the second
                    clause never mentioned. It was computed for that reason and
                    then had no reader anywhere in this repo.
    """
    from . import request_cover
    conn, cfg = ledger.connect(_repo(args)), _cfg(args)
    row = conn.execute("SELECT request FROM task WHERE id = ?", (args.task,)).fetchone()
    if row is None:
        print(f"no such task: {args.task}", file=sys.stderr)
        return 2
    request = row["request"] or ""
    if args.show:
        covered = request_cover.entries(conn, args.task)
        m = request_cover.measure(request, covered, root=cfg.root)
        print(f"request  : {request}\n")
        rejected = {f["quote"]: f["why"] for f in (m.get("faults") or [])}
        for e in covered:
            bad = rejected.get(e["quote"])
            what = ("REJECTED  " + bad[:60]) if bad else \
                   ("NOT DONE  " + e["why"][:60]) if e["not_done"] else \
                   ("→ " + (e["symbol"] or e["test"]))
            print(f"  {e['quote'][:44]:46} {what}")
            if e.get("acceptance"):
                print(f"  {'':46} done when: {e['acceptance'][:70]}")
        gone = request_cover.withdrawals(conn, args.task)
        for w in gone:
            print(f"  {w['quote'][:44]:46} WITHDRAWN {w['why'][:60]}")
        print(f"\n{round(m['ratio'] * 100)}% accounted for "
              f"({m['delivered']} delivered, {m['not_done']} not done"
              f"{f', {len(gone)} withdrawn' if gone else ''}"
              f"{f', {len(rejected)} rejected' if rejected else ''})")
        if m["missing"]:
            print(f"\nnot spoken for yet:\n  {m['missing']}")
        _print_unspoken(m)
        return 0
    if args.withdraw:
        try:
            bad = request_cover.withdraw(
                conn, task_id=args.task, quote=args.quote, why=args.why or "",
                min_chars=cfg.thresholds["min_chars"])
        except request_cover.NotInTheRequest as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        if bad:
            print(f"REFUSED: {bad}", file=sys.stderr)
            return 2
        m = request_cover.measure(request, request_cover.entries(conn, args.task),
                                  root=cfg.root)
        print(f"withdrawn. {round(m['ratio'] * 100)}% of the request is "
              f"accounted for now.")
        return 0
    try:
        request_cover.record(
            conn, task_id=args.task, request=request, quote=args.quote,
            symbol=args.symbol, test=args.test, not_done=args.not_done,
            why=args.why or "", acceptance=args.acceptance or "",
            min_chars=cfg.thresholds["min_chars"], root=cfg.root)
    except request_cover.NotInTheRequest as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    m = request_cover.measure(request, request_cover.entries(conn, args.task),
                              root=cfg.root)
    print(f"recorded. {round(m['ratio'] * 100)}% of the request is accounted for.")
    # The entry that was just written may be one `fault()` throws out, and
    # until this line the answer to that was "the percentage did not move" --
    # a number nobody can act on, printed instead of the reason.
    for f in (m.get("faults") or []):
        print(f"REJECTED  {f['quote'][:44]}\n  {f['why']}", file=sys.stderr)
    if m["missing"]:
        print(f"still unspoken for:\n  {m['missing'][:200]}")
    _print_unspoken(m)
    return 0


def _print_unspoken(m):
    """Clauses of the request nobody quoted at all.

    Not the same fact as `missing`, which is the unquoted *characters*: a
    request whose two clauses weigh 20 and 2 reads as 91% accounted for with
    the second never mentioned, and `measure` computes this list for exactly
    that reason. It had no reader in this repo, which made the reason it exists
    unobservable from the command that exists to show it.
    """
    clauses = m.get("unspoken") or []
    if not clauses:
        return
    print(f"\n{len(clauses)} clause(s) nobody quoted at all:")
    for clause in clauses[:12]:
        text = " ".join(str(clause).split())
        print(f"  · {text[:110]}")
    if len(clauses) > 12:
        print(f"  … {len(clauses) - 12} more")


def _print_uncovered(cfg):
    """What shipping proves nothing about.

    `SHIP`, `chain: intact` and a green suite together read as "this works". They
    are not that claim and were never meant to be: every kind here is about the
    change, and none of them asks what state the system is in. Measured on one
    six-task run -- six ships, chain intact, 5,580 tests green, and the feature
    was not wired: no migration applied, no bucket, no scheduler row, no plist.
    Nothing in the output was false; the reader supplied the rest.

    `v4 coverage` already knows which risk classes no registered kind reaches.
    Saying it here costs a line and puts it where the impression is formed.

    Three states, and the first version printed two of them the same way. No
    rubric on disk is a repo that never declared one, and silence is the whole
    answer. A rubric that is there and cannot be read is not that: `except
    Exception: return` -- over a `coverage.rubric` that answered `None` for a
    file it could not parse -- dropped this line out of the ship report
    altogether, so a repo whose rubric was malformed printed exactly like a
    repo where every operational-risk class has a kind. That is the false
    impression the paragraph above says this function exists to prevent,
    produced by the function itself.

    `rubric` tells the two apart now, so this does not test the file a second
    time: `None` is "none declared" and `RubricUnreadable` is the other one.

    Not a gate, for `coverage.risk_report`'s reason: a gate here fails every
    task for debt no task created. The answer is the line, in the place where
    the impression is formed, saying that the question was not asked.
    """
    try:
        from . import coverage
        doc = coverage.rubric(cfg.root)
        if doc is None:
            return              # none declared, so there is nothing to be silent about
        none = [r for r in doc["rows"]
                if not [k for k in r["answered_by"] if k in set(cfg.kinds)]]
    except Exception as exc:                                    # noqa: BLE001
        _print_unasked_unreadable(cfg.root / ".v4" / "risk_rubric.json",
                                  f"{type(exc).__name__}: {exc}")
        return
    if not none:
        return
    print(f"unasked  : {len(none)} of {len(doc['rows'])} operational-risk "
          f"class(es) have no kind here, so this ship says nothing about them")
    for r in none[:4]:
        print(f"           #{r['n']:<2} {r['category'][:56]}")
    if len(none) > 4:
        # `v4 coverage`, not `v4 coverage --risk`: the risk report is what that
        # command prints with no flag and `--risk` has never been a flag it
        # takes -- `cmd_coverage` has `--predecessor` and nothing else. Pointing
        # a reader at an invocation the parser rejects is the same defect as
        # printing an escape a gate has closed, and writing the second copy of
        # it under this repair is how it would have stopped being one line.
        print(f"           … {len(none) - 4} more -- v4 coverage")


def _print_unasked_unreadable(path, why: str):
    """The `unasked` line for a rubric that could not be read.

    Same column and same prefix as the answer it replaces, because a reader
    scanning a ship report for `unasked  :` has to find this too. What it must
    not read as is zero unanswered classes.
    """
    print(f"unasked  : {path.name} could not be read ({why}), so this ship "
          f"says nothing about which operational-risk classes have no kind "
          f"here -- which is not the same as none of them being unanswered")
    print("           v4 coverage reads the same file and says the same thing")


def _engage_before(conn, cfg, args):
    """`v4 engage --task <id> --kind <kind>` -- the sentence written first.

    Half the claims a task answers are about code it is creating, and those
    cannot be engaged with beforehand because they do not exist until the code
    does. The rule does exist. This writes against the rule.
    """
    if not args.task:
        print("--kind needs --task: a sentence before the work is about what "
              "this task is going to build", file=sys.stderr)
        return 2
    rules = (cfg.kinds.get(args.kind) or {}).get("rule") or []
    if args.text is None:
        if not rules:
            print(f"{args.kind} carries no rule.", file=sys.stderr)
            return 2
        print(f"kind    : {args.kind}")
        print(f"about   : what {args.task} is going to write\n")
        for i, r in enumerate(rules, 1):
            print(f"  {i}. {r['text'] if isinstance(r, dict) else r}\n")
        print("Say what this rule means for what you are about to build -- not "
              "what the rule says. Name a path this task is scoped to.")
        return 0
    ok, reason = engagement.judge_before(
        conn, cfg, task_id=args.task, kind=args.kind, sentence=args.text,
        actor=getattr(args, "actor", None) or "worker")
    if not ok:
        print(f"REFUSED: {reason}", file=sys.stderr)
        return 2
    print(f"recorded against {args.kind}, before the work, "
          f"by {getattr(args, 'actor', None) or 'worker'}")
    print("when a claim of this kind is raised later, its own sentence is "
          "readable against this one -- the difference is what you had not "
          "thought of yet")
    return 0


def cmd_engage(args):
    conn, cfg = ledger.connect(_repo(args)), _cfg(args)
    # `getattr`, because callers build this Namespace by hand as well as by
    # parsing, and a new optional flag must not break the ones that predate it.
    if getattr(args, "kind", None):
        return _engage_before(conn, cfg, args)
    if not getattr(args, "claim", None):
        print("give --claim <id>, or --task <id> --kind <kind> to engage with a "
              "rule before the code it is about exists", file=sys.stderr)
        return 2
    row = conn.execute("SELECT * FROM claim WHERE id = ?", (args.claim,)).fetchone()
    if row is None:
        print(f"no such claim: {args.claim}", file=sys.stderr)
        return 2

    kind_cfg = cfg.kinds.get(row["kind"], {})
    if not kind_cfg.get("engagement"):
        # A kind that does not engage still owes an answer to "why not". Without
        # a reader, `engagement_why` was a field written and never looked at --
        # which `dead-wiring` reports, and which is how `engagement: false`
        # becomes the quiet way to delete a checkpoint.
        why = kind_cfg.get("engagement_why")
        print(f"claim   : {row['question']}")
        print(f"\n{row['kind']} does not ask for a sentence.")
        print(f"reason  : {why}" if why else
              "reason  : none recorded -- that is a gap, not a design")
        return 0

    rules = engagement.rule_for(cfg, row) or []
    if args.text is None:
        # No sentence yet: show what this is about. A checkpoint that names
        # nothing is a request for characters.
        print(f"claim   : {row['question']}")
        print(f"about   : {row['file']}{'::' + row['symbol'] if row['symbol'] else ''}")
        # What was said about this kind before any claim existed. `v4 engage
        # --task X --kind Y` writes that sentence and the kernel promises it
        # will be "readable against" the claim when one arrives -- and
        # `before_the_work`, the function that reads it back, had no production
        # caller. So the sentence was collected and never shown to the one
        # person it was collected for: whoever is now writing the second one.
        said = engagement.before_the_work(conn, row["task_id"], row["kind"])
        if said:
            actor, sentence = said
            print(f"\nbefore  : {sentence}")
            print(f"          -- written by {actor} before this claim existed")
        # `if not rules`, not `for ... else`: the `else` on a loop runs whenever
        # the loop was not broken out of, which here is always. So every claim
        # that carried a rule printed the rule and then, directly underneath,
        # a line telling the worker there was no rule.
        for i, r in enumerate(rules, 1):
            n = f"rule {i}" if len(rules) > 1 else "rule"
            print(f"\n{n:<8}: {r['text']}")
            print(f"source  : {r['source']}")
            print("\nWrite what it means for this code -- not the rule again.")
        if not rules:
            print("\n(this kind carries no rule; that is a gap, not a design)")
        return 0
    ok, reason = engagement.judge(conn, cfg, claim_row=row, sentence=args.text,
                                  exempt_duplicate=args.after_widen)
    engagement.record(conn, task_id=row["task_id"], claim_id=args.claim,
                      sentence=args.text,
                      verdict="accepted" if ok else "rejected_machine", reason=reason)
    print("accepted" if ok else f"REFUSED: {reason}")
    if not ok:
        print("\nRewrite and try again. There is no limit and no judge here -- "
              "every test is mechanical, so a sentence that satisfies them passes.")
    return 0 if ok else 1


#: Which `review` action reads which flag, as the branches below actually read
#: them. `--withdraw` is the one that was measured: it is honoured in `defer`
#: only, and `v4 review add --withdraw` took it, ignored it, and filed the
#: finding -- so somebody who meant to cancel a deferral opened a claim instead
#: and read `raised <id>` as confirmation. argparse cannot know this; the
#: parser has one flag set for seven actions.
#:
#: A table rather than seven `if args.withdraw` lines, because the fact is not
#: about `--withdraw`. Every other flag here has the same shape: `--why` is read
#: by `defer` and `group`, `--gone` by `close`, `--findings` by `done`, and each
#: of them was silently dropped by the other six. Fixing the one that was
#: reported would have left five.
_REVIEW_FLAGS = {
    "add":    {"task", "file", "symbol", "note", "lens", "run", "check_id"},
    "amend":  {"claim", "note"},
    "close":  {"claim", "test", "command", "parent", "gone", "now",
               "mutation_file", "mutation_gone", "mutation_now", "rename_commit", "declaration", "why"},
    "defer":  {"claim", "why", "target", "withdraw"},
    "done":   {"lens", "findings", "task", "run", "result", "note", "evidence"},
    "group":  {"name", "claim", "why"},
    "lens":   {"lens", "task", "run"},
}

#: Where each flag *is* read, for the refusal to name. Derived from the table so
#: the two cannot drift.
_REVIEW_FLAG_HOMES = {
    flag: sorted(a for a, fs in _REVIEW_FLAGS.items() if flag in fs)
    for flag in set().union(*_REVIEW_FLAGS.values())
}


def _stray_review_flags(args) -> list:
    """[(flag, actions that read it)] -- what was typed and would be dropped."""
    allowed = _REVIEW_FLAGS.get(args.action, set())
    stray = []
    for flag, homes in sorted(_REVIEW_FLAG_HOMES.items()):
        if flag in allowed:
            continue
        value = getattr(args, flag, None)
        # `--findings 0` is a value and the reason `done` exists; `if value`
        # would drop exactly the one that carries the meaning.
        if value is None or value == [] or value is False or value == "":
            continue
        stray.append((flag, homes))
    return stray


def cmd_review(args):
    conn, cfg = ledger.connect(_repo(args)), _cfg(args)
    # `--claim` repeats, because `group` names several and everything else
    # names one. The one-claim actions take the last, which is what a person
    # who typed it twice meant and what argparse gave them before this changed.
    claim = (args.claim or [None])[-1]
    stray = _stray_review_flags(args)
    if stray:
        print("REFUSED: `review " + args.action + "` does not read "
              + ", ".join(f"--{f}" for f, _ in stray) + ".\n\n"
              + "\n".join(f"  --{f} is read by: {', '.join(homes)}"
                           for f, homes in stray)
              + "\n\nTaking a flag and dropping it is how "
                "`review add --withdraw` filed a finding for somebody who "
                "meant to cancel a deferral.", file=sys.stderr)
        return 2
    run_context = None
    if getattr(args, "run", None):
        from . import maintenance
        try:
            run_context = maintenance.validate_run(conn, cfg.root, args.run, args.lens)
            if args.task and args.task != run_context["task"]:
                raise ValueError("--task differs from the assigned run")
            args.task = run_context["task"]
        except ValueError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
    if args.action == "defer":
        if not claim:
            print("defer needs --claim", file=sys.stderr)
            return 2
        if args.withdraw:
            # Cancelling the record, not the decision. `withdraw_deferral`
            # refuses when the claim exists, so this reaches only the case it
            # is for: a deferral written about nothing.
            try:
                path = review.withdraw_deferral(
                    conn, cfg.root, cfg=cfg, claim_id=claim,
                    why=args.why or "")
            except review.CannotDefer as exc:
                print(f"REFUSED: {exc}", file=sys.stderr)
                return 2
            print(f"the deferral naming {claim} is cancelled. Both rows "
                  f"stay in the ledger; only the accounting moved.")
            if path.is_file():
                print(f"  {path.relative_to(cfg.root)} still on disk")
            return 0
        try:
            path = review.defer(conn, cfg.root, cfg=cfg, claim_id=claim,
                                why=args.why or "", target=args.target or "")
        except review.CannotDefer as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        print(f"deferred. {path.relative_to(cfg.root)} -- commit it.")
        print(f"target: {args.target}")
        print("`v4 ship` prints how many are outstanding, every time.")
        return 0
    if args.action == "lens":
        all_lenses, skipped = review.lens_files(cfg.root, include_legacy=bool(args.lens))
        if not args.lens:
            print(f"{len(all_lenses)} lens(es). Pass --lens <name> for the brief.\n")
            for slug, l in all_lenses.items():
                print(f"  {slug:<40} {len(l['checks']):>2} checks   {l['source']}")
            # Named, not silently absent. A lens file that will not load used to
            # take this command down with a traceback; skipping it quietly
            # instead would mean a lens somebody wrote and got wrong simply
            # stops existing, which is the same silence one layer down.
            if skipped:
                print(f"\n{len(skipped)} file(s) in .v4/lenses/ are not usable "
                      f"and were left out:")
                for slug, why in sorted(skipped.items()):
                    print(f"  {slug:<40} {why}")
            return 0
        if args.lens in skipped:
            print(f"lens {args.lens!r} is there and not usable: "
                  f"{skipped[args.lens]}", file=sys.stderr)
            return 2
        if args.lens not in all_lenses:
            print(f"no such lens: {args.lens}", file=sys.stderr)
            return 2
        # The task, when there is one. `lens_brief` prints a different opening
        # line for each mode, and it can only tell them apart if this hands it
        # the one thing that distinguishes them. A periodic sweep passes none.
        context = run_context["context"] if run_context else None
        if args.task and context is None:
            from .maintenance import context_for
            context = context_for(conn, args.task)
        print(review.lens_brief(all_lenses[args.lens], slug=args.lens,
                                task=args.task, repo=cfg.root, context=context,
                                run=getattr(args, "run", None)))
        # A reviewer who ran and found nothing looked identical to one that
        # never ran. The predecessor measured this failure at 100% of adopters:
        # two repos vendored an executable kit, both satisfied the contract by
        # recording the command, and it executed zero times in either repo's
        # recorded history. Nothing here gates on it -- recording which lenses
        # ran is the whole mechanism, and that is deliberate.
        #
        # Recorded whether or not a task is named. It used to be `if args.task`,
        # and the X/Y run is what that cost: 72 lens agents on one arm and 72 on
        # the other, 394 findings between them, and `lens_run` in the ledger was
        # zero -- because a reviewer reading a diff names no task, and neither
        # does the periodic sweep, which by design has none. The one signal that
        # tells a run-and-found-nothing from a never-ran was switched off for
        # exactly the two ways a lens is actually used.
        review.record_lens_run(conn, slug=args.lens,
                               lens=all_lenses[args.lens], task_id=args.task,
                               root=cfg.root, run_id=getattr(args, "run", None))
        return 0

    if args.action == "done":
        # `lens_run` is written when the brief prints, and its own docstring in
        # `sweep.ran_since` says so honestly: "which lenses actually printed
        # their brief". Two readers then treated it as a review having
        # happened -- the ship payload's `lenses_run`, whose comment claims it
        # separates "nobody reviewed this" from "three reviewers found
        # nothing", and the `reviewed by:` line that prints it. Measured on
        # this repo: `near-miss` had two `lens_run` rows, zero findings, and no
        # reviewer had ever read a diff with it, while `v4 ship` printed
        # `reviewed by: near-miss`.
        #
        # So the reviewer says when it is finished, the same way a sweep does
        # (`v4 sweep --done --findings N`). `--findings 0` is the whole point:
        # it is the only way "ran and found nothing" can exist as a fact.
        try:
            review.record_lens_reviewed(conn, cfg.root, slug=args.lens,
                                        findings=args.findings, task_id=args.task,
                                        run_id=getattr(args, "run", None), result=getattr(args, "result", None) or "completed",
                                        note=args.note or "", evidence=getattr(args, "evidence", None) or [])
        except (review.BadCoordinates, ValueError) as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        print(f"recorded: {args.lens} reviewed"
              f"{'' if not args.task else ' ' + args.task}"
              f", {args.findings} finding(s)")
        return 0

    if args.action == "add":
        # `review lens --lens X` has refused an unknown X since it was written;
        # `review add --lens X` never did, and the slug it takes goes into the
        # claim id and into `claim.variant`, which is what a reader of a
        # finding sees as the lens that found it. A typo there files a finding
        # under a lens that does not exist and no sweep can ever account for.
        if args.lens:
            known, skipped = review.lens_files(cfg.root, include_legacy=bool(args.lens))
            if args.lens in skipped:
                print(f"lens {args.lens!r} is there and not usable: "
                      f"{skipped[args.lens]}", file=sys.stderr)
                return 2
            if args.lens not in known:
                print(f"no such lens: {args.lens}. `v4 review lens` lists them.",
                      file=sys.stderr)
                return 2
        try:
            cid, created, siblings = review.raise_finding(
                conn, cfg, task_id=args.task, file=args.file, symbol=args.symbol or "",
                note=args.note, lens=args.lens or "", check_id=getattr(args, "check_id", None))
        except (review.BadCoordinates, ValueError) as exc:
            # Refused here, where it costs one retry, rather than at the end of
            # the task where the only remaining move is a signature.
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        if run_context:
            from .maintenance import observe_finding
            observe_finding(conn, cfg.root, args.run, args.lens, cid, getattr(args, "check_id", None))
        print(f"{'raised' if created else 'already open'} {cid}")
        # The line that would have stopped six answered findings being
        # overwritten. `note amended on <id>` was printed for a write nobody
        # asked for, and there was nothing to print for the case that actually
        # happened -- a lens finding a second thing at coordinates it had
        # already filed on. Now both are said: which claim this is, and what
        # else stands where it stands.
        if siblings:
            where = (args.file or "") + (f"::{args.symbol}" if args.symbol else "")
            print(f"  finding {len(siblings) + 1} at {where}"
                  + (f" under lens {args.lens}" if args.lens else "")
                  + f" -- the other(s): {', '.join(siblings)}")
            print(f"  if this was meant to correct one of those rather than "
                  f"stand beside it:")
            print(f"    v4 --repo . review amend --claim {siblings[0]} "
                  f"--note '<the corrected sentence>'")
        # Which of the two closures applies depends on whether this finding
        # has a symbol a frame can be named after. A finding on a document or a
        # table has none by construction -- `resolve_symbol` refuses one --
        # and telling its author to write a red-green test names a route that
        # does not exist for them.
        # Addressed, because the session reading this is the one forbidden to
        # act on it. `.github/monitor/SCOPE.md` lists `./bin/v4 review close`
        # under refused with the reason "closing your own finding", `PROMPT.md`
        # repeats it, and `.claude/agents/reviewer.md` says the same for the
        # reviewer role -- and nothing enforces any of it: `review close` has
        # no origin or session check at all. So the one actor holding the claim
        # id at this moment was being handed the command in the imperative.
        #
        # Naming the addressee is not enforcement; it is the difference between
        # an output that states the separation and one that invites crossing it.
        print("this finding is closed by the session that repairs it, not this "
              "one. What that session runs:")
        if args.symbol:
            print("  v4 review close --claim <id> --test <path> "
                  "--command '<cmd with {path}>' --parent <commit>")
            print("  ... or --mutation-file with --mutation-gone, when the "
                  "repair *is* the test")
        else:
            print("  v4 review close --claim <id> "
                  "--gone '<the sentence that has to go>' "
                  "--now '<what replaces it>' --parent <commit>")
        return 0
    if args.action == "amend":
        # The route `review add` used to take by inference, asked for by name.
        # Six answered findings on this repo were overwritten by a lens sweep
        # that meant to file new ones, and the reviewer who tried to undo it
        # pasted the original text back into `review add`, matched the stored
        # row, and was told `already open` -- exit 0, nothing written, the
        # wrong text still serving. A correction is a decision somebody makes
        # about a named claim, so it takes the claim's name.
        if not claim:
            print("REFUSED: `review amend` needs --claim <id> --note '<the "
                  "corrected sentence>'.\n\n"
                  "It corrects what one finding says. Filing a different "
                  "finding is `review add`, and at the same coordinates that "
                  "now opens its own claim rather than rewriting this one.",
                  file=sys.stderr)
            return 2
        try:
            was, changed = review.amend_note(conn, claim_id=claim,
                                             note=args.note or "")
        except (review.BadCoordinates, ValueError) as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        if not changed:
            print(f"already says this: {claim}")
            return 0
        print(f"note amended on {claim}")
        print(f"  was: {was[:160]}{'…' if len(was) > 160 else ''}")
        print("  both readings stay in the ledger; `current_note` serves the "
              "new one and `claim.note` is untouched.")
        return 0
    if args.action == "group":
        try:
            ids = review.group(conn, cfg, name=args.name,
                               claim_ids=args.claim or [], why=args.why)
        except (review.BadCoordinates, ValueError) as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        print(f"{len(ids)} finding(s) recorded as one fact: {args.name}")
        print("  close them with one test: `v4 review close --claim <id> "
              "--test <path> …` per claim.")
        print("  each run still requires that claim's own symbol to have been "
              "entered, so a")
        print("  finding this group does not actually reach will fail there.")
        return 0
    if args.gone or args.now:
        if getattr(args, "rename_commit", None) is not None or getattr(args, "declaration", False) or args.why:
            print("REFUSED: rename/declaration evidence requires executable red/green proof, not --gone/--now")
            return 2
        try:
            review.bind_text_change(conn, claim_id=claim, gone=args.gone,
                                    now=args.now, parent_commit=args.parent)
        except ValueError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        print(f"text closure offered for {claim}")
        print("the quoted text has to have moved between the parent and now; "
              "this proves a string changed and nothing more")
        return 0
    # The `close` action had no branch of its own: every other action refuses
    # its missing arguments and this one fell through with whatever the parser
    # left as `None`. `v4 review close` with no flags at all printed "None
    # offered as closing None" and appended a `review_close` event with a NULL
    # claim id and a payload of nulls into an append-only table, exit 0 -- a row
    # nothing can use, reported as success. Not hypothetical: this is the form
    # `.github/monitor/PROMPT.md` printed, so a monitor session pasting it
    # believed a finding had been offered a closing test when nothing was bound.
    # `doctor` warns about the same shape one line down ("the event is about
    # nothing").
    #
    # Anything that is not `defer`, `lens` or `add` also arrived here, so a
    # typo in the action was a silent write too.
    if args.action != "close":
        print(f"REFUSED: unknown action {args.action!r}", file=sys.stderr)
        return 2
    mutation = None
    if args.mutation_file or args.mutation_gone:
        mutation = (args.mutation_file or "", args.mutation_gone or "",
                    args.mutation_now or "")
    missing = [name for name, value in (("--claim", claim),
                                        ("--test", args.test),
                                        ("--command", args.command)) if not value]
    if not args.parent and not mutation:
        missing.append("--parent or --mutation-file/--mutation-gone")
    if missing:
        print(f"REFUSED: `review close` needs {', '.join(missing)}.\n\n"
              f"  v4 --repo . review close --claim <id> --test <path> "
              f"--command '<cmd with {{path}}>' --parent <commit>\n\n"
              f"The test has to pass at HEAD, enter the symbol the finding "
              f"names, and be red one of two ways: at --parent, the tree before "
              f"the repair; or with --mutation-file/--mutation-gone, which "
              f"breaks what the test covers. The second is for a finding whose "
              f"repair *is* the test -- there the code was always right, so no "
              f"parent exists where the test fails.", file=sys.stderr)
        return 2
    try:
        review.bind_closing_test(conn, claim_id=claim, test_path=args.test,
                                 command=args.command, parent_commit=args.parent,
                                 mutation=mutation, root=cfg.root,
                                 rename_commits=getattr(args, "rename_commit", None),
                                 declaration=getattr(args, "declaration", False), why=args.why)
    except (review.BadCoordinates, ValueError) as exc:
        print(f"REFUSED: --test {args.test} -- {exc}.\n\n"
              f"{review.HOW_TO_NAME_A_TEST}", file=sys.stderr)
        return 2
    print(f"{args.test} offered as closing {claim}")
    if getattr(args, "declaration", False):
        print("coordinate correction recorded: observe the original value's initialization; original claim and symbol retained")
    if mutation:
        print(f"it has to pass at HEAD, run the symbol, and fail with "
              f"{mutation[0]} broken")
    else:
        print("it has to fail at the parent, pass at HEAD, and run the symbol")
    # Recorded, not accepted, and the difference was left to be inferred from
    # two lines that describe a condition without saying who checks it. Exit 0
    # is right -- the offer was written, which is this command's whole job --
    # and it is also what a reader takes for "closed", so the next step is said
    # rather than implied. Measured on an adopter closing 35 findings: they read
    # exit 0 plus this wording as success and found otherwise by counting OPEN
    # in `status`.
    claim_task = conn.execute("SELECT task_id FROM claim WHERE id=?", (claim,)).fetchone()[0]
    print(f"\nNOT VERIFIED YET -- the claim is still open. The verdict comes "
          f"from:\n  v4 --repo {shlex.quote(str(cfg.root))} check --task {shlex.quote(claim_task)} "
          f"--claim {claim}")
    return 0


def _list_arg(values):
    """One list, however the caller wrote it.  Every repeatable flag in this CLI.

    `--add` and `--drop` take `nargs="*"`, so `--add a b` was the only form
    that worked, while `v4 task --scope` in this same CLI splits on commas.
    Writing `--add 'a/**,b/**'` therefore stored a single glob with a comma in
    it, which matches no path -- a widen that reported success and widened
    nothing. Accepting both forms is what makes the two commands agree.

    That fixed one pair and left the fact standing everywhere else: this CLI had
    four spellings of "split a list" -- here, and three hand-written
    comprehensions in `cmd_task` and `cmd_foresee` -- across two argparse shapes
    that both drop what you typed. Measured 2026-09-06 on this repo:
    `v4 task --scope kernel/redgreen.py --scope 'tests/**'` opened a task scoped
    to `tests/**` alone, and `v4 scope widen --add A --add B` widened to B. Both
    printed what they stored, and neither said anything was gone. A scope that
    is half of what was meant fails nothing -- it makes the checkers skip files,
    and "not checked" reads exactly like "checked and clean".

    So: every one of them is `action="append"` now, which cannot drop a value,
    and every one of them arrives here. Named `_list_arg` rather than `_globs`
    because `check --claim` is on it too and claim ids are not globs -- the
    splitting is the same operation either way, and one name for it is the
    point.

    A nested list is flattened one level: `action="append"` with `nargs="*"`
    hands back a list of lists, which is what keeps `--add a b` working
    alongside `--add a --add b`.
    """
    out = []
    for value in values or []:
        if isinstance(value, (list, tuple)):
            out.extend(_list_arg(value))
            continue
        out.extend(g.strip() for g in str(value).split(",") if g.strip())
    return out


def cmd_scope(args):
    conn, cfg = ledger.connect(_repo(args)), _cfg(args)
    args.add, args.drop = _list_arg(args.add), _list_arg(args.drop)
    if args.action == "show":
        u = scope_mod.usage(conn, cfg, args.task)
        print(f"scope   : {scope_mod.current_scope(conn, args.task)}")
        print(f"widened : {u['widens']} time(s), {len(u['paths'])} path(s) "
              f"= {u['reached']} of {u['repo_files']} files ({u['pct']}%)"
              + _refused_widens(u))
        if u["over_warn"]:
            print("  ^ past the warning line. There is no gate here on purpose; "
                  "the defence is that this number is visible.")
        return 0

    if args.action == "narrow":
        # `--add` belongs to `widen` and this branch never passed it on, so
        # `scope narrow --drop a/** --add a/b.py` took the drop, dropped the
        # add on the floor, and said nothing -- which is how the obvious way to
        # replace a wide glob with a narrow one fails silently.
        if args.add:
            print("REFUSED: `--add` is `widen`'s. Widen to the narrow glob "
                  "first, then narrow away the wide one -- two events, so the "
                  "ledger says which was which.", file=sys.stderr)
            return 2
        try:
            globs = scope_mod.narrow(conn, cfg, task_id=args.task,
                                     drop=args.drop, why=args.why)
        except scope_mod.WidenRefused as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return 2
        print(f"scope now: {globs}")
        print("Claims already raised over the dropped paths stay in the ledger "
              "-- narrowing says what this task will not touch, it does not "
              "unask a question. A re-derive stops raising new ones there.")
        res = lifecycle.derive(conn, cfg, args.task, phase="widen")
        for line in _retraction_lines(res.get("retracted") or []):
            print(line)
        return 0

    try:
        globs, protected = scope_mod.widen(conn, cfg, task_id=args.task,
                                           add=args.add, why=args.why)
    except scope_mod.WidenRefused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 2
    print(f"scope now: {globs}")
    print("no re-plan, no re-split, nothing already answered re-run")
    if protected:
        print(f"\n{len(protected)} of these judge the work: {protected}")
        print("changing them needs a signature:")
        print("  v4 risk accept --claim <id> --kind scope_widen_protected --why '…'")
    # Re-derive so the new files get claims, then say what appeared.
    res = lifecycle.derive(conn, cfg, args.task, phase="widen")
    print(f"\nre-derived: {len(res['created'])} new claim(s)")
    return 0


def cmd_trend(args):
    """Arithmetic over the whole ledger.  PL-7.

    Every other command answers about one task. The findings that decided
    anything in the last two reviews were cross-task, and each was hand-typed
    SQL nobody kept. A number nobody can re-run is an anecdote.
    """
    from . import trend as trend_mod
    conn = ledger.connect(_repo(args))
    data = trend_mod.report(conn, since=args.since)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))
        return 0

    print(f"{data['tasks']} task(s)" + (f" since {args.since}" if args.since else ""))

    print("\n  first engagement sentence, and what was written before it")
    for r in data["engagement_lag"]:
        if not r.get("watched", True):
            print(f"    {r['task']:<26} the write hook never fired -- nothing "
                  f"was watching, which is not a score of zero")
            continue
        secs = "" if r["seconds"] is None else f"  {r['seconds']:+.0f}s"
        print(f"    {r['task']:<26} {r['writes_before_first_sentence']:>3} write(s) before{secs}")

    print("\n  the write gate")
    for r in data["gate"]:
        bases = " ".join(f"{k}={v}" for k, v in sorted(r["bases"].items()))
        print(f"    {r['task']:<26} {r['marks']:>3} mark(s)  {r['refused']} refused  {bases}")

    print("\n  how claims reached terminal")
    for r in data["how_claims_ended"]:
        g = sum(k["green"] for k in r["by_kind"].values())
        s_ = sum(k["signed"] for k in r["by_kind"].values())
        o = sum(k["open"] for k in r["by_kind"].values())
        print(f"    {r['task']:<26} {r['claims']:>3} claim(s)  {g} green  {s_} signed  {o} open")
    worst = sorted(((k, v) for r in data["how_claims_ended"]
                    for k, v in r["by_kind"].items() if v["signed"]),
                   key=lambda kv: -kv[1]["signed"])[:5]
    if worst:
        print("      most-signed kinds: " +
              ", ".join(f"{k} ({v['signed']})" for k, v in worst))

    print("\n  claims inherited from a file rather than from the change")
    for r in data["inherited"]:
        if not r.get("watched", True):
            print(f"    {r['task']:<26} {r.get('why', 'not measurable here')}")
            continue
        print(f"    {r['task']:<26} {r['before_first_write']:>3} before the first write, "
              f"{r['after']} after")

    sg = data["signatures"]
    print(f"\n  {sg['total']} signature(s): "
          + ", ".join(f"{k}={v}" for k, v in sorted(sg["by_kind"].items()))
          + f"  |  tty={sg['tty']['tty']} no_tty={sg['tty']['no_tty']}"
          + f"  |  median reason {sg['median_why_chars']} chars")
    # On its own line, because it is the answer to a different question and the
    # line above was being read as if it were the answer to this one: `no_tty`
    # counts both a worker waiving its own judge and a second session signing a
    # detector's claim, and those are the two the third value exists to split.
    print("    by route: "
          + ", ".join(f"{k}={v}" for k, v in sorted(sg["by_signer"].items())))

    print("\n  measured cost per kind (median / max ms)")
    for k, v in sorted(data["checker_cost"].items(), key=lambda kv: -kv[1]["median_ms"]):
        print(f"    {k:<26} {v['median_ms']:>8} / {v['max_ms']:<8} over {v['runs']} run(s)")
    return 0


def cmd_foresee(args):
    """Which files outside a proposed scope name what is inside it.  PL-6."""
    from . import foresee as foresee_mod
    cfg = _cfg(args)
    globs = _list_arg(args.scope)
    hits = foresee_mod.foresee(cfg.root, globs,
                               exclude=cfg.config.get("derive_exclude", []),
                               min_name=args.min_name)
    if args.json:
        print(json.dumps({k: [list(x) for x in v] for k, v in hits.items()},
                         indent=2, sort_keys=True))
        return 0
    print(foresee_mod.render(cfg.root, globs, hits))
    return 0


def cmd_remerge(args):
    """What a merge invalidated, and how many rounds it has taken.  PL-5."""
    from . import hashing, remerge as remerge_mod
    cfg = _cfg(args)
    conn = ledger.connect(cfg.root)
    # A worktree digest, not a commit: repo-scoped answers are keyed to the
    # bytes that are here, because a worker's edits are uncommitted.
    head = args.head or hashing.worktree_digest(cfg.root)
    if args.action == "show":
        print(remerge_mod.report(conn, head, kinds_cfg=cfg.kinds))
        return 0
    stale = {s["task"]: s["claims"]
             for s in remerge_mod.stale_after(conn, head, kinds_cfg=cfg.kinds)}
    if args.task not in stale:
        print(f"{args.task} has no repo-scoped answer older than {head[:12]}")
        return 0
    n, blocked = remerge_mod.record(conn, task_id=args.task, head=head,
                                    claims=stale[args.task])
    print(f"round {n} recorded for {args.task}: {stale[args.task]} claim(s) expired")
    if blocked:
        print(f"\n{args.task} is blocked: {n} rounds. Two cuts that keep needing "
              f"each other re-checked is a fact about where they were cut, and a "
              f"fourth round will not find that out.")
    return 0


def cmd_cost(args):
    conn = ledger.connect(_repo(args))
    if args.action == "record":
        ledger.insert(conn, "cost_observation", task_id=args.task, claim_id=None,
                      source="self_reported", tokens=args.tokens,
                      wall_ms=args.wall_ms,
                      created_at=__import__("datetime").datetime.now(
                          __import__("datetime").timezone.utc).isoformat())
        print(f"recorded {args.tokens} token(s) as self_reported")
        print("marked as such because a number an agent reports about itself "
              "cannot be used to judge that agent")
        return 0
    rows = conn.execute(
        "SELECT source, COUNT(*) n, SUM(COALESCE(tokens,0)) tok, "
        "SUM(COALESCE(wall_ms,0)) ms FROM cost_observation "
        + ("WHERE task_id = ? " if args.task else "") + "GROUP BY source",
        (args.task,) if args.task else ()).fetchall()
    for r in rows:
        print(f"  {r['source']:<14} {r['n']:>5} obs  {r['tok']:>9} tokens  "
              f"{r['ms']/1000:>8.1f} s")
    if not rows:
        print("  nothing recorded")
    return 0


def cmd_coverage(args):
    cfg = _cfg(args)
    # The risk classes are the denominator now. The predecessor's obligation
    # catalogue is still readable with `--predecessor`, and is still worth
    # reading once -- it is where the correction lives that 557 ids and 51
    # families were never a taxonomy of 332.
    if args.predecessor:
        for line in coverage.report(cfg.root, set(cfg.kinds)):
            print(line)
        return 0
    # The same distinction `_print_uncovered` needs, at the other surface that
    # reads this file. "no .v4/risk_rubric.json here" was printed for a rubric
    # that is here and does not parse -- a false sentence, under an exit code
    # that is right for both. The verdict does not move; the sentence does.
    try:
        lines = coverage.risk_report(cfg.root, set(cfg.kinds))
    except coverage.RubricUnreadable as exc:
        print(f"{exc}\nNothing below, and that is not a coverage figure of "
              f"zero -- fix the file and ask again.", file=sys.stderr)
        return 4
    if lines is None:
        print("no .v4/risk_rubric.json here. `v4 coverage --predecessor` reads "
              "the obligation catalogue instead, if this repo has one.")
        return 4
    for line in lines:
        print(line)
    return 0


def cmd_doctor(args):
    """Seven things that were each silently untrue here at some point today."""
    from . import doctor
    rows = doctor.run(_repo(args))
    mark = {"ok": "ok  ", "warn": "warn", "bad": "BAD "}
    worst = 0
    for r in rows:
        print(f"  {mark[r['status']]} {r['what']:<16} {r['detail']}")
        if r.get("fix"):
            print(f"       {r['fix']}")
        worst = max(worst, {"ok": 0, "warn": 1, "bad": 2}[r["status"]])
    print()
    print({0: "wired", 1: "wired, with things worth knowing",
           2: "not wired -- the BAD lines above are silent in normal use"}[worst])
    return 1 if worst == 2 else 0


def _analysis_behind(root: Path, program: Path) -> list:
    """Which `kernel/analysis/` modules this program's judgement comes from.

    Read off the imports rather than declared anywhere: the registry names the
    program, and the rule a checker applies usually lives one module further
    in. That last hop is the one a reader cannot guess.
    """
    import ast as _ast
    try:
        tree = _ast.parse(program.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    out = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.ImportFrom) and (node.module or "").endswith("analysis"):
            out.update(f"kernel/analysis/{a.name}.py" for a in node.names)
        elif isinstance(node, _ast.Import):
            for a in node.names:
                if a.name.startswith("kernel.analysis."):
                    out.add(a.name.replace(".", "/") + ".py")
    return sorted(m for m in out if (root / m).is_file())


def cmd_facts(args):
    """The commands that maintain the facts table.

    They existed only as `python3 -m kernel.facts`, which `v4 --help` did not
    mention and `USING.md` contradicts in one line -- 所有指令行 `./bin/v4`.
    `docs/README.md` makes `verify` the verification story for a file 30% of
    this repo's claims depend on, and CI calls it; a newcomer following the
    documents could not reach any of it.

    Delegated, not reimplemented: `kernel.facts.main` owns the arguments and
    the exit codes, and a second parser here would be a second answer.
    """
    from . import facts as facts_mod
    rest = list(args.rest or [])
    if facts_mod.help_requested(rest):
        return facts_mod.main([args.command, *rest])
    # This repo's own table and root, unless the caller named them. Flags are
    # not positional arguments: `v4 facts verify --gone-only` has to mean the
    # same thing as spelling both paths out, or the form CI runs is the one
    # form this command cannot take.
    positional = [r for r in rest if not r.startswith("-")]
    if args.command == "propose" and not positional:
        rest = [str(_repo(args)), *rest]
    elif args.command in ("validate", "verify", "scan", "restate") and (
            not positional or (args.command == "scan" and positional[0] in facts_mod.SYMBOL_LISTS)):
        cfg = _cfg(args)
        if cfg.facts_path is None:
            print("this repo has no facts table; `v4 facts propose` drafts one")
            return 4
        head = [str(cfg.facts_path)]
        if args.command in ("verify", "scan", "restate"):
            head.append(str(_repo(args)))
        rest = head + rest
    return facts_mod.main([args.command, *rest])


def cmd_explain(args):
    """What decides this claim's verdict, in one place.

    Starting cold from a claim id the route was four hops: `v4 status --json`
    for the checker id, `.v4/claim_kinds.json` for the detector filename,
    `.v4/checkers.json` for the program, and that program's imports for the
    module holding the rule -- two of them JSON files no command printed and
    USING.md never mentioned. 28 subcommands and none of them answered it,
    while `config.checker_for` and `doctor` already walk exactly this chain.
    """
    cfg = _cfg(args)
    kind = args.kind
    claim = None
    if args.claim:
        conn = ledger.connect(_repo(args))
        claim = conn.execute(
            "SELECT id, kind, question, file, symbol, detector, note FROM claim "
            "WHERE id = ?", (args.claim,)).fetchone()
        if claim is None:
            print(f"no claim {args.claim} in this ledger")
            return 1
        kind = claim["kind"]
    if not kind:
        print("name a kind (--kind) or a claim (--claim)")
        return 1
    try:
        kind_cfg = cfg.kind(kind)
    except Exception as exc:                                    # noqa: BLE001
        print(f"{exc}")
        return 1

    print(f"kind      {kind}")
    if claim is not None:
        where = (claim["file"] or "") + (f"::{claim['symbol']}" if claim["symbol"] else "")
        print(f"claim     {claim['id']}  {where}")
        print(f"asks      {claim['question']}")
    try:
        cid, program, entry = cfg.checker_for(kind)
        rel = program.relative_to(cfg.root)
        print(f"checker   {cid}  ->  {rel}")
        print(f"          registered sha {str(entry.get('sha256'))[:16]}, "
              f"reads {entry.get('reads')}")
        for mod in _analysis_behind(cfg.root, program):
            print(f"rule in   {mod}")
    except Exception as exc:                                    # noqa: BLE001
        print(f"checker   unresolved: {exc}")
    raiser = claim["detector"] if claim is not None and claim["detector"] else None
    print(f"raised by {raiser or kind_cfg.get('detector') or 'review, not a detector'}")
    print(f"applies   {kind_cfg.get('applies_to', 'always')}"
          + ("  (needs a sentence first)" if kind_cfg.get("engagement") else ""))
    # `rule` is a list of `{from, source, text}` -- the shape `claim_kinds.json`
    # actually carries. Reading `rule_source`/`source` off the kind was reading
    # two fields nothing sets, which `dead-wiring` reported the moment it ran.
    for r in (kind_cfg.get("rule") or [])[:3]:
        if isinstance(r, dict) and r.get("source"):
            print(f"rule from {r['source']}")
    return 0


def cmd_doctrine(args):
    """Layer ①.  Generated, because a hand-maintained one rots invisibly."""
    from . import doctrine
    cfg = config_mod.RepoConfig(_repo(args))
    if args.audit:
        rows = doctrine.uptake(ledger.connect(cfg.root), cfg)
        live = [r for r in rows if r["exposed"] and r["engaged"] is not None]
        vague = [r for r in rows if r["exposed"] and r["engaged"] is None]
        # Rate, not a zero test. Measured: "External write 要留低 audit trail:
        # actor、timestamp、trace id" was engaged with twice in 25 exposures while
        # its loudest sibling scored 19, and the repo held no trace id at all --
        # a rule at 8% is being skipped just as surely as one at 0%, and only a
        # rate says so.
        for r in live:
            r["rate"] = r["engaged"] / r["exposed"]
        print(f"{len(rows)} standing rule(s); {len(live)} measurable and on screen "
              f"at least once. Weakest first:\n")
        for r in sorted(live, key=lambda x: (x["rate"], -x["exposed"]))[:8]:
            pct = round(r["rate"] * 100)
            print(f"  {pct:>3}%  {r['kind']:18} {r['engaged']:>3}/{r['exposed']}"
                  f"   （認佢嘅詞: {', '.join(r['terms'][:5])}）")
            print(f"        {r['rule'][:92]}")
            if r["source"]:
                print(f"        source: {r['source']}")
            print()
        print("A rule that keeps appearing and keeps not being engaged with is one")
        print("of three things, and all three are worth knowing: it does not apply")
        print("to this repo, it is written in a way nobody can act on, or it is")
        print("being ignored.")
        if vague:
            print(f"\n{len(vague)} rule(s) share every word with a sibling rule, so "
                  f"this cannot tell them apart -- unmeasured, not clean:")
            for r in vague:
                print(f"  {r['kind']:18} {r['rule'][:72]}")
        return 0
    if args.check:
        problem = doctrine.drift(cfg)
        if problem:
            print(f"FAIL: {problem}")
            if not args.write:
                return 1
        else:
            from .hosts import doctrine_files
            print(", ".join(p.name for p in doctrine_files(cfg.root)) + " is what `v4 doctrine` generates")
            if not args.write:
                return 0
    # `--write` was declared and read by nothing, so `v4 doctrine --check --write`
    # checked and wrote nothing -- while the header this command generates into
    # every adopter's CLAUDE.md, and SPEC.md's command table, both name that flag.
    # Writing stays the behaviour with neither flag, which is what the docs rely on.
    from .hosts import doctrine_files
    for destination in doctrine_files(cfg.root):
        path, changed = doctrine.write(cfg, destination)
        print(f"{path}{'  (updated)' if changed else '  (already current)'}")
    return 0


def cmd_export(args):
    """Write the ledger somewhere an auditor can reach it.

    The chain argument rests on CI walking it, and CI cannot: the ledger lives
    in `.git/v4/`, which a clone does not carry. Three sections of the spec
    named that walk as the anchor for the whole tamper story, and it has never
    run once.
    """
    root = _repo(args)
    conn = ledger.connect(root)
    n = ledger.export_jsonl(conn, args.out, root)
    print(f"{n} row(s) -> {args.out}")
    print(f"Verify with no database in reach:  v4 audit --events {args.out}")
    return 0


def cmd_audit(args):
    if getattr(args, "events", None):
        details = {}
        n, problems = ledger.verify_exported(args.events, details=details)
        if problems:
            print(f"FAIL: {len(problems)} problem(s) across {n} chained row(s).\n")
            for pr in problems:
                print(f"  {pr}")
            return 1
        projected = len(details.get("redacted_projection_events", []))
        if projected:
            print(f"export integrity verified across {n} chained row(s) in {args.events}")
            print(f"  {projected} legacy event(s) verified as redacted projections; "
                  "their original hashes cannot be re-derived from public bytes.")
            print("  The projection commitment was checked against the source ledger at export; "
                  "it is not an authenticated signature or recovery of the original secret.")
        else:
            print(f"chain intact across {n} chained row(s) in {args.events}")
        return 0

    conn = ledger.connect(_repo(args))
    if getattr(args, "compositions", False):
        worst = 0
        for r in conn.execute("SELECT id FROM task ORDER BY rowid"):
            ok, lines = composition.report(conn, _repo(args), r["id"])
            print(f"{r['id']}: {lines[0]}")
            for extra in lines[1:]:
                print(extra)
            worst = max(worst, 0 if ok else 1)
        return worst
    _walked, problems = ledger.audit_chain(conn, _repo(args))
    # Everything is printed; only tampering is a non-zero exit. A concurrent
    # append is a fact about this ledger that stays true forever, so it is on
    # the screen every time -- and it is not a reason to call the record
    # forged.
    fatal = ledger.fatal(problems)
    ok = not fatal
    n = conn.execute("SELECT COUNT(*) c FROM attempt").fetchone()["c"]
    print(f"attempts: {n}")
    # Where the event cover begins. Rows written before `event` was chained
    # carry no hash; that is history rather than tampering, so it is a line
    # here and not a problem there -- but a reader who is told "chain: intact"
    # has to know how much of it the walk could see.
    bare, hashed = ledger.event_chain_starts_at(conn)
    print(f"events: {hashed} chained"
          + (f", {bare} written before the event chain existed" if bare else ""))
    if ok:
        print("chain: intact")
        for said in problems:
            print(f"  {said}")
        print("\nnote: an intact chain means no row was edited after it was written.")
        print("      it does not mean the ledger cannot be written to -- see SPEC.md 2.1")
        return 0
    print("chain: BROKEN")
    for p in problems:
        print(f"  - {p}")
    return 1


def cmd_run_checker(args):
    """Run one checker against a subject file, exactly as the kernel would."""
    payload = json.loads(Path(args.subject).read_text())
    res = runner.run_checker(
        repo_root=_repo(args),
        checker_path=Path(args.checker).resolve(),
        registered_sha=args.expect_sha,
        subject_payload=payload,
        subject_refs=payload["subject_refs"],
        facts=json.loads(Path(args.facts).read_text()) if args.facts else None,
        timeout_sec=args.timeout,
        emit_baseline=args.emit_baseline,
    )
    print(f"exit {res.exit_code} in {res.duration_ms} ms")
    if res.stdout:
        print(res.stdout)
    if res.stderr:
        print(res.stderr, file=sys.stderr)
    return res.exit_code


def cmd_host(args):
    from . import hosts, host_binding
    root = _repo(args)
    if args.action == "permissions":
        print('default_permissions = "vibeproof"\n')
        print("permissions.vibeproof = " + hosts.inline_toml(
            hosts.permission_profile(root, git_operations=args.git_operations)))
        return 0
    if args.action == "bind":
        conn = ledger.connect(root)
        try:
            result = host_binding.bind(conn, root, task_id=args.task,
                                       session=args.session, agent=args.agent, host=args.host)
        finally:
            conn.close()
        print(json.dumps(result))
        return 0
    conn = ledger.connect_readonly(root)
    try:
        print(json.dumps(hosts.evidence(conn, task_id=args.task, host=args.host,
                                       session=args.session, agent=args.agent), indent=2))
    finally:
        conn.close()
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="v4")
    p.add_argument("--repo", default=".")
    p.add_argument("--acceptance", default=".v4/acceptance.json")
    sub = p.add_subparsers(dest="cmd", required=True)

    host = sub.add_parser("host", help="bind a host agent to its task or read hook coverage")
    hp = host.add_subparsers(dest="action", required=True)
    permissions = hp.add_parser("permissions")
    permissions.add_argument("--git-operations", action="store_true",
                             help="include local Git index/objects/refs/worktree writes")
    permissions.set_defaults(fn=cmd_host)
    for verb in ("bind", "status"):
        h = hp.add_parser(verb)
        h.add_argument("--task", required=verb == "bind")
        h.add_argument("--session", required=verb == "bind")
        h.add_argument("--agent", default="")
        h.add_argument("--host", choices=("claude", "codex"), default="codex")
        h.set_defaults(fn=cmd_host)

    ini = sub.add_parser("init",
                         help="scaffold .v4/ for a repo adopting this")
    ini.add_argument("--hosts", choices=("claude", "codex", "both"),
                     help="coding hosts to configure; default is Claude")
    ini.set_defaults(fn=cmd_init)

    ins = sub.add_parser("install",
                         help="copy every checker, detector, fixture and lens "
                              "into this repo and register them")
    ins.add_argument("--check", action="store_true",
                     help="say what is behind or missing and write nothing")
    ins.add_argument("--hosts", choices=("claude", "codex", "both"),
                     help="install host assets; preserves the existing choice when omitted")
    ins.add_argument("--activate-hooks", action="store_true",
                     help="merge framework handlers into the selected host settings")
    ins.set_defaults(fn=cmd_install)

    ac = sub.add_parser("accept",
                        help="run this repo's gates in a tree with no local state")
    ac.add_argument("--here", action="store_true",
                    help="run in the working tree instead of a fresh archive")
    ac.add_argument("--tests", action="store_true", help="only the test command")
    ac.add_argument("--fixtures", action="store_true",
                    help="only the checker and detector registration gates")
    ac.add_argument("--docs", action="store_true",
                    help="only the document-against-code checkers")
    ac.set_defaults(fn=cmd_accept)

    v = sub.add_parser("verify", help="run a checker against its red/green fixtures")
    v.add_argument("--checker", required=True)
    v.add_argument("--fixtures", required=True)
    v.add_argument("--kind", required=True)
    v.add_argument("--facts")
    v.add_argument("--timeout", type=int, default=config_mod.DEFAULT_CHECKER_TIMEOUT)
    v.add_argument("--min-cases", type=int, default=5)
    v.set_defaults(fn=cmd_verify)

    r = sub.add_parser("register", help="verify, then add to the registry")
    r.add_argument("--id", required=True)
    r.add_argument("--checker", required=True)
    r.add_argument("--fixtures", required=True)
    r.add_argument("--kinds", required=True)
    r.add_argument("--registry", default=".v4/checkers.json")
    # Repeatable *and* comma-separated. It used to be comma-separated only, so
    # `--reads a --reads b` kept `b` and threw `a` away without a word --
    # argparse's default, and here it silently rewrote a registry entry. Measured:
    # `bundle-secret` went from seven globs to one, and the next install held the
    # kind back saying this repo had no file it could read, about a repo full of
    # TypeScript.
    r.add_argument("--reads", action="append", default=None,
                   help="globs this checker can read -- comma-separated, or the "
                        "flag repeated; `**` if it really is language-independent")
    r.add_argument("--timeout", type=int, default=config_mod.DEFAULT_CHECKER_TIMEOUT)
    r.set_defaults(fn=cmd_register)

    vd = sub.add_parser("verify-detector",
                        help="run a detector against fixtures that should and should not fire")
    vd.add_argument("--detector", required=True)
    vd.add_argument("--fixtures", required=True)
    vd.add_argument("--timeout", type=int, default=config_mod.DEFAULT_CHECKER_TIMEOUT)
    vd.add_argument("--min-cases", type=int, default=3)
    vd.set_defaults(fn=cmd_verify_detector)

    rdet = sub.add_parser("register-detector",
                          help="verify a detector, then record that it passed")
    rdet.add_argument("--detector", required=True)
    rdet.add_argument("--fixtures", required=True)
    rdet.add_argument("--timeout", type=int, default=config_mod.DEFAULT_CHECKER_TIMEOUT)
    rdet.add_argument("--min-cases", type=int, default=3)
    rdet.set_defaults(fn=cmd_register_detector)

    rd = sub.add_parser("round", help="open or close a measurement round")
    rd.add_argument("action", choices=["open", "close"])
    rd.add_argument("--label", required=True)
    rd.add_argument("--note")
    rd.set_defaults(fn=cmd_round)

    t = sub.add_parser("task", help="open a task")
    t.add_argument("--id", required=True)
    t.add_argument("--request", required=True,
                   help="the requester's own words, copied. Not a summary of "
                        "them and not what the work turned out to be: two "
                        "readers now treat this as evidence -- `v4 cover` "
                        "quotes against it with str.__contains__ so a piece of "
                        "the request cannot be paraphrased, and the "
                        "request-fidelity lens judges the diff against it. A "
                        "restatement passes both and is worth neither")
    t.add_argument("--scope", action="append", required=True,
                   help="comma-separated, or repeated -- the globs this task "
                        "may write")
    t.add_argument("--base", help="what the delta gates diff against. HEAD by "
                                  "default; an older commit only ever means a "
                                  "bigger diff")
    t.add_argument("--forbid", action="append", default=None,
                   help="comma-separated, or repeated -- globs this task must "
                        "not touch")
    t.add_argument("--after", help="the task this continues. Its request, scope "
                                   "and answered claims are carried into this "
                                   "one's request, so the next worker does not "
                                   "start from nothing")
    t.set_defaults(fn=cmd_task)

    ab = sub.add_parser("abandon",
                        help="end a task that will not be shipped, and say why")
    ab.add_argument("--task", required=True)
    ab.add_argument("--why", required=True)
    ab.set_defaults(fn=cmd_abandon)

    d = sub.add_parser("derive", help="run every detector, create claims")
    d.add_argument("--task", required=True)
    d.add_argument("--phase", default="open", choices=["open", "widen", "ship", "review"])
    d.set_defaults(fn=cmd_derive)

    ck = sub.add_parser("check", help="run checkers for a task's open claims")
    ck.add_argument("--task", required=True)
    ck.add_argument("--claim", action="append", nargs="*")
    ck.add_argument("--all", action="store_true",
                    help="run expensive claims even while something cheaper is "
                         "failing. Off by default because the edit that answers "
                         "the cheap one expires the expensive one's answer")
    ck.set_defaults(fn=cmd_check)

    st = sub.add_parser("status", help="derived state of every claim")
    st.add_argument("--json", action="store_true",
                     help="the same facts in a shape that does not move when "
                          "the wording does. The exit code is unchanged.")
    st.add_argument("--task", required=True)
    st.add_argument("--detail", action="store_true",
                    help="also print what each checker wrote to --out")
    st.set_defaults(fn=cmd_status)

    sh = sub.add_parser("ship", help="re-derive, then check one predicate")
    sh.add_argument("--task", required=True)
    sh.set_defaults(fn=cmd_ship)

    rk = sub.add_parser("risk", help="sign for something that cannot be proved")
    rk.add_argument("action", choices=["accept", "waiting"])
    rk.add_argument("--claim"); rk.add_argument("--kind", choices=list(risk.KINDS))
    rk.add_argument("--why"); rk.add_argument("--task")
    rk.add_argument("--ended", action="store_true",
                    help="waiting: include claims on tasks that have ended")
    rk.add_argument("--scope", choices=[risk.TASK, risk.REPO], default=risk.TASK,
                    help="repo: this repo structurally has no subject for that "
                         "checker (no SPEC.md, no lockfile, no declared layers). "
                         "Signed once instead of once per task; lapses the moment "
                         "the checker stops exiting 4")
    rk.add_argument("--no-tty-check", action="store_true",
                    help="for tests only; the check is friction, and skipping it is recorded")
    rk.add_argument("--as-monitor", action="store_true",
                    help="a session that did not raise this claim, signing one a "
                         "detector raised; refused on anything from `review add`, "
                         "and recorded as `signed_by: monitor`")
    rk.set_defaults(fn=cmd_risk)

    cv = sub.add_parser("coverage",
                        help="which operational-risk classes have a mechanism here")
    cv.add_argument("--predecessor", action="store_true",
                    help="the old denominator: the predecessor's obligation "
                         "catalogue, two thirds of which is generated per phase")
    cv.set_defaults(fn=cmd_coverage)

    tr = sub.add_parser("trend", help="arithmetic over the whole ledger, not one task")
    tr.add_argument("--since", help="ISO date; only tasks opened after it")
    tr.add_argument("--json", action="store_true")
    tr.set_defaults(fn=cmd_trend)

    fs = sub.add_parser("foresee",
                        help="before you cut: who outside this scope names what is inside it")
    fs.add_argument("--scope", action="append", required=True,
                    help="comma-separated, or repeated -- the scope you are about to declare")
    fs.add_argument("--min-name", type=int, default=5,
                    help="ignore names shorter than this; a short name matches by accident")
    fs.add_argument("--json", action="store_true")
    fs.set_defaults(fn=cmd_foresee)

    rm = sub.add_parser("remerge",
                        help="after a merge: whose answers are about a tree that moved")
    rm.add_argument("action", choices=["show", "record"])
    rm.add_argument("--task"); rm.add_argument("--head")
    rm.set_defaults(fn=cmd_remerge)

    co = sub.add_parser("cost", help="what this has cost, and who says so")
    co.add_argument("action", choices=["show", "record"])
    co.add_argument("--task"); co.add_argument("--tokens", type=int)
    co.add_argument("--wall-ms", type=int)
    co.set_defaults(fn=cmd_cost)

    sc = sub.add_parser("scope", help="widen or narrow a task's scope, or see "
                                      "how much it has")
    sc.add_argument("action", choices=["widen", "narrow", "show"])
    sc.add_argument("--task", required=True)
    sc.add_argument("--add", action="append", nargs="*", default=None)
    #: `narrow` takes back part of a declaration. A path this task has already
    #: changed cannot be dropped, which is what keeps this from being a way to
    #: put work out of sight.
    sc.add_argument("--drop", action="append", nargs="*", default=None)
    sc.add_argument("--why")
    sc.set_defaults(fn=cmd_scope)

    sw = sub.add_parser("sweep",
                        help="the periodic after-gate: is it due, and what to run")
    sw.add_argument("--if-due", action="store_true",
                    help="exit 1 when it is not due, so cron can stop there")
    sw.add_argument("--done", action="store_true", help="record that a sweep finished")
    sw.add_argument("--findings", type=int, help="how many the sweep raised")
    sw.add_argument("--note")
    sw.add_argument("--history", action="store_true")
    sw.set_defaults(fn=cmd_sweep)

    cv2 = sub.add_parser("cover",
                         help="account for one piece of this task's request")
    cv2.add_argument("--task", required=True)
    cv2.add_argument("--quote", help="an exact span of the request, copied out")
    cv2.add_argument("--symbol", help="what delivers that piece")
    cv2.add_argument("--test", help="a test that reaches it")
    cv2.add_argument("--acceptance",
                     help="what would make this piece done, in the requester's "
                          "terms. Not judged -- it exists so somebody can argue "
                          "with it in a diff")
    cv2.add_argument("--not-done", action="store_true",
                     help="this piece was deliberately not done -- say why")
    cv2.add_argument("--why")
    cv2.add_argument("--show", action="store_true",
                     help="what has been accounted for so far, and what has not")
    cv2.add_argument("--withdraw", action="store_true",
                     help="cancel an earlier entry for --quote and say --why. "
                          "Both rows stay in the ledger; only the accounting "
                          "moves. This is how a wrong entry is corrected -- "
                          "re-record the quote afterwards")
    cv2.set_defaults(fn=cmd_cover)

    e = sub.add_parser("engage", help="write the sentence a claim asks for")
    e.add_argument("--claim")
    e.add_argument("--task", help="with --kind: engage before any claim exists")
    e.add_argument("--kind", help="engage with a kind's rule before the code it "
                                  "is about is written. Half a task's claims are "
                                  "about code it is creating, and those cannot be "
                                  "raised until the code is there")
    e.add_argument("--actor", choices=engagement.BEFORE_ACTORS,
                   help="with --kind: who is writing this. A sentence handed "
                        "down by whoever cut the task is a constraint; the same "
                        "sentence from the agent about to write the code is the "
                        "same self-justification, earlier")
    e.add_argument("--text", help="omit to see the rule this claim is about")
    e.add_argument("--after-widen", action="store_true",
                   help="exempt from duplicate detection: a second widen in one task "
                        "has the same reason as the first, and refusing it makes the "
                        "cheap exit expensive")
    e.set_defaults(fn=cmd_engage)

    mt = sub.add_parser("maintain", help="coordinate review, authorized repair, host schedules and notifications")
    mt.add_argument("action", choices=["schema", "setup", "status", "start", "finish", "handoff", "schedule", "pause", "resume", "notify", "ack", "listen", "notifications", "receiver"])
    mt.add_argument("--data", help="JSON input file (or - for stdin) for setup, handoff, schedule or notification")
    mt.add_argument("--id", help="maintenance run, handoff or notification ID")
    mt.add_argument("--host", choices=["claude", "codex"])
    mt.add_argument("--trigger", choices=["manual", "scheduled"], default="manual")
    mt.add_argument("--job", help="start: configured host job ID for a scheduled run")
    mt.add_argument("--session", help="host session identity or explicit manual label")
    mt.add_argument("--task", help="task-bound review in its recorded worktree")
    mt.add_argument("--context-task", help="request/base context for repo-wide review; does not change finding ownership")
    mt.add_argument("--lens", action="append", help="explicit requested lens; repeat, otherwise select by available context")
    mt.add_argument("--why", help="finish: abandon the run with a recorded reason")
    mt.add_argument("--once", action="store_true", help="listen: collect once instead of continuing")
    from .maintenance_cli import command as maintain_command
    mt.set_defaults(fn=maintain_command)

    rv = sub.add_parser("review", help="raise a reviewer's finding, or close one")
    rv.add_argument("action",
                    choices=["add", "amend", "close", "lens", "defer", "group",
                             "done"])
    rv.add_argument("--name", help="group: one sentence naming the fact")
    rv.add_argument("--task"); rv.add_argument("--file"); rv.add_argument("--symbol")
    rv.add_argument("--note",
                    help="add: what is wrong, one sentence. amend: the "
                         "sentence that replaces it -- at the same coordinates "
                         "`add` opens a second claim, it does not rewrite the "
                         "first")
    rv.add_argument("--lens")
    rv.add_argument("--check-id", help="add: source responsibility ID; preserves migrated claim namespace")
    rv.add_argument("--run", help="lens/add/done: assigned maintenance run")
    rv.add_argument("--result", choices=["completed", "not_applicable", "not_evaluable", "failed"], help="done: review outcome, distinct from finding count")
    rv.add_argument("--evidence", action="append", help="done: evidence reference; repeat as needed")
    rv.add_argument("--claim", action="append",
                    help="repeat it for `group`; once for everything else")
    rv.add_argument("--test")
    rv.add_argument("--findings", type=int,
                    help="done: how many findings this lens reported. 0 is an "
                         "answer -- it is the only way `ran and found nothing` "
                         "exists as a fact, and `v4 ship` prints it")
    rv.add_argument("--withdraw", action="store_true",
                    help="cancel a deferral that was written about nothing "
                         "(defer only; refused when the claim exists)")
    rv.add_argument("--command"); rv.add_argument("--parent")
    rv.add_argument("--rename-commit", action="append",
                    help="close: committed same-file Python function rename; repeat in order for a chain. Requires unchanged body/signature/scope and full red-green proof")
    rv.add_argument("--declaration", action="store_true",
                    help="close: correct an old named-value coordinate using actual Node instruction/type observation in the same file; requires --why and red/green proof")
    rv.add_argument("--mutation-file",
                    help="close: the file to break, as the other way to be red. "
                         "For a finding whose repair is a test, there is no "
                         "parent the test fails at -- the code was always "
                         "right and nobody was looking. Break what it covers "
                         "instead")
    rv.add_argument("--mutation-gone",
                    help="close: the text to take out of --mutation-file. It "
                         "has to appear there exactly once")
    rv.add_argument("--mutation-now", default="",
                    help="close: what replaces it. Empty deletes the line")
    rv.add_argument("--gone", help="close a finding whose repair has no behaviour "
                                   "to test: quote the text that was in the file "
                                   "at --parent and is not there now")
    rv.add_argument("--now", help="the other direction: quote the text that was "
                                  "not there at --parent and is there now")
    rv.add_argument("--why", help="defer: why not fixed now; close --declaration: why the original coordinate names a value")
    rv.add_argument("--target", help="defer: where the work went -- an issue, a "
                                     "task id, a file, a dated review")
    rv.set_defaults(fn=cmd_review)

    dr = sub.add_parser("doctor", help="is this repo wired, or does it only look wired")
    dr.set_defaults(fn=cmd_doctor)

    fa = sub.add_parser("facts", help="draft, validate, verify or scan the facts table")
    fa.add_argument("command",
                    choices=("propose", "validate", "verify", "scan",
                            "restate"))
    # `nargs=argparse.REMAINDER`, so a flag meant for `kernel.facts` reaches
    # it instead of being eaten by this parser: `--gone-only` is the form CI
    # runs, and `v4 facts verify --gone-only` was an argparse error.
    fa.add_argument("rest", nargs=argparse.REMAINDER,
                    help="passed through; `verify --gone-only` is what CI runs")
    fa.set_defaults(fn=cmd_facts)

    ex = sub.add_parser("explain", help="what decides this claim's verdict")
    ex.add_argument("--claim")
    ex.add_argument("--kind")
    ex.set_defaults(fn=cmd_explain)

    dc = sub.add_parser("doctrine", help="generate the repo's standing rules")
    dc.add_argument("--write", action="store_true")
    dc.add_argument("--check", action="store_true")
    dc.add_argument("--audit", action="store_true",
                    help="which standing rules have been on screen and which "
                         "of those nobody has ever engaged with")
    dc.set_defaults(fn=cmd_doctrine)

    x = sub.add_parser("export", help="write the ledger out as portable JSONL")
    x.add_argument("--out", required=True)
    #: No `--task`. It raised OperationalError on every invocation, and a
    #: repaired version would export a slice of a hash chain, which
    #: `verify_exported` must reject -- see `ledger.export_jsonl`.
    x.set_defaults(fn=cmd_export)

    a = sub.add_parser("audit", help="walk the attempt hash chain")
    a.add_argument("--events", help="walk an exported JSONL instead of the live ledger")
    a.add_argument("--compositions", action="store_true",
                   help="also ask whether any two claims can never hold at once")
    a.set_defaults(fn=cmd_audit)

    c = sub.add_parser("run-checker", help="run one checker against a subject file")
    c.add_argument("--checker", required=True)
    c.add_argument("--subject", required=True)
    c.add_argument("--facts")
    c.add_argument("--expect-sha")
    c.add_argument("--timeout", type=int, default=config_mod.DEFAULT_CHECKER_TIMEOUT)
    # The route `derive`'s own advice names. A delta checker prints its baseline
    # under this flag and three of them accept it; until now no `v4` subcommand
    # could pass it, so the advice pointed at a door with no handle on this side.
    c.add_argument("--emit-baseline", action="store_true",
                   help="ask a delta checker to print the baseline that would "
                        "carry today's findings as inherited")
    c.set_defaults(fn=cmd_run_checker)

    args = p.parse_args(argv)
    # A malformed config, facts table or ledger is a thing the person running
    # this can fix, and they were being handed a 30-line traceback whose last
    # line was the only one that said anything. Measured on a first adoption: a
    # repo with no outbound write got the stack twice, from `derive` and from
    # `status`, and neither said which file to open.
    #
    # Only these three. A `except Exception` here would swallow the kernel's own
    # bugs into the same one-line shrug, and losing a traceback for those is how
    # a framework stops being debuggable.
    try:
        return args.fn(args)
    except (ledger.NoSuchTask, ledger.ExportProjectionError, scope_mod.WidenRefused) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (config_mod.ConfigError, facts_mod.FactsError, sqlite3.DatabaseError) as exc:
        print(f"v4 {args.cmd}: {exc}", file=sys.stderr)
        return 5


if __name__ == "__main__":
    sys.exit(main())
