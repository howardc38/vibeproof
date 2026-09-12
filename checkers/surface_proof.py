#!/usr/bin/env python3
"""Run the declared surface suite and validate its fresh execution receipt.

Exit 0: required cases ran and passed; 1: command or case failed;
4: no declared/available runner; 5: checker/configuration error.
See docs/SPEC.md's surface-proof contract and .v4/surface/INTEGRATION.md.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kernel import surface
from kernel import config as config_mod  # noqa: E402

KIND = "surface-proof"

#: What a repo calls the command that drives its own surface. Read from config
#: rather than guessed: guessing `npm test` would run a unit suite and report it
#: as a surface answer, which is the substitution this whole framework refuses.
CONFIG_KEY = "surface_command"


def _facts(root: Path) -> dict:
    # `config.facts_path_for` -- the only answer. This spelled it again, with
    # no preference for `facts.<repo>.json`, so a repo carrying a second table
    # is judged against another project's vocabulary.
    from kernel.config import facts_path_for
    p = facts_path_for(root)
    if p is None:
        return {}
    try:
        f = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return f if isinstance(f, dict) else {}



def surface_command(root: Path):
    """`(command, cwd)` or `(None, why)`."""
    try:
        cfg = json.loads((root / ".v4" / "config.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, f"cannot read .v4/config.json: {exc}"
    # `declared` rather than `get`: `v4 init` writes the sentinel for a field
    # only this repo can answer, and a sentinel that reaches the shell is a
    # command-not-found reported as a failing surface.
    cmd = config_mod.declared(cfg, CONFIG_KEY)
    if not cmd:
        globs = _facts(root).get("ui_globs") or []
        hint = (f" This repo declares ui_globs ({', '.join(globs[:3])}), so it "
                f"has a surface; nothing says how to drive it." if globs else "")
        return None, (
            f"no {CONFIG_KEY!r} in .v4/config.json.{hint}\n\n"
            f"    \"{CONFIG_KEY}\": \"the command that drives this repo's own "
            f"surface\",\n"
            f"    \"surface_cwd\":     \"where to run it from, if not the "
            f"repo root\"\n\n"
            f"It is not guessed. A repo's unit-test command usually exits 0 "
            f"without touching the surface at all, so guessing one would hand "
            f"out a green claim about something that never ran. This is the "
            f"suite you already maintain -- name it.")
    return cmd, cfg.get("surface_cwd") or "."


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--subject", required=True)
    p.add_argument("--facts")
    p.add_argument("--out")
    a = p.parse_args()
    try:
        subject = json.loads(Path(a.subject).read_text())
        root = Path(subject["repo_root"]).resolve()
        cfg = json.loads((root / ".v4/config.json").read_text())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"cannot read surface configuration: {exc}", file=sys.stderr)
        return 5
    cmd, where = surface_command(root)
    if cmd is None:
        print(where)
        return 4
    kind, required = cfg.get("surface_kind", "command"), cfg.get("surface_required", [])
    if (kind not in ("command", "browser") or not isinstance(required, list) or
            not all(isinstance(x, str) and x.strip() for x in required) or
            len(set(required)) != len(required)):
        print("invalid surface_kind or surface_required", file=sys.stderr)
        return 5
    if not isinstance(cmd, str) or not isinstance(where, str):
        print("surface_command and surface_cwd must be strings", file=sys.stderr)
        return 5
    cwd = (root / where).resolve()
    if not cwd.is_dir() or not cwd.is_relative_to(root):
        print("surface_cwd must be a directory in this repository", file=sys.stderr)
        return 5
    # The registered runner owns timeout/process-group cleanup. One owner.
    try:
        env = surface.prepare(root, kind)
        run = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                             text=True, env=env)
        code, why, receipt = surface.validate(Path(env["V4_SURFACE_RESULT"]),
            env["V4_SURFACE_RUN_ID"], root, cwd, kind, required)
    except (OSError, ValueError) as exc:
        print(f"could not run surface command: {exc}", file=sys.stderr)
        return 5
    if run.returncode != 0:
        code = 4 if run.returncode == 127 and receipt is None else 1
        why = f"surface command exited {run.returncode}; inspect runner/server output"
    evidence = {"command": cmd, "cwd": str(cwd), "exit": run.returncode,
                "result_path": env["V4_SURFACE_RESULT"], "receipt": receipt,
                "tail": (run.stdout + run.stderr).splitlines()[-20:], "reason": why}
    if a.out:
        Path(a.out).write_text(json.dumps(evidence, indent=2))
    print(why, file=sys.stdout if code == 0 else sys.stderr)
    if code:
        print("\n".join(evidence["tail"]), file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
