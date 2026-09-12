#!/usr/bin/env bash
# The surface of a framework whose surface is a command line.
#
# `surface-proof` asks whether the thing a person actually touches still works,
# and for an app that is a browser suite. Here it is `bin/v4`: the walkthrough
# in docs/USING.md is the surface, and until this existed nothing ran it --
# every test in this repo imports `kernel` directly, so a launcher that could
# not start, a subcommand removed from the parser, or a `--repo` that stopped
# resolving would all have been green.
#
# Runs the documented first five commands against a throwaway repo, through the
# launcher, the way section 1 of USING.md tells a newcomer to. Any non-zero
# exit fails the probe; so does an empty answer from a command that must print.
#
# Named in `docs/SPEC.md`, beside `runtime-proof`, and it had to be: that
# section told a reader that `surface-proof` here always exits 4 because this
# repo has no second suite, which stopped being true on 2026-08-20 when
# `.v4/config.json` pointed `surface_command` at this file. A contract that
# denies the existence of the artefact its own config names is worse than one
# that says nothing, and until now this file appeared in no document at all --
# so there was nowhere for a reader to find out which of the two was current.
#
# Where the throwaway repo goes, and why it is not one fixed place.
#
# It was `${TMPDIR:-/tmp}/v4-surface-probe`, the same path on every run, and
# this script `rm -rf`s it on entry. Nothing in `checkers/surface_proof.py` or
# in the kernel ever set `V4_SURFACE_PROBE`, so every run took the default --
# and this repo's own instructions are to work several worktrees at once. Two
# concurrent `v4 check` runs therefore deleted each other's tree mid-run, and
# the answer that came back was "the surface failed", about nothing.
#
# So the default is a fresh directory per run, removed when this exits.
# `V4_RUNTIME_PROBE` is the opposite case and stays as it is: `truth_command`
# is shared by every runtime proof and must not move per run, so that probe
# reuses one repo and separates runs by the run id inside it. There is no
# truth to ask here, so there is nothing to keep.
#
# `V4_SURFACE_PROBE` still names a location, and a caller that names one owns
# it -- it is kept rather than removed, which is what lets a test look inside
# afterwards.
set -euo pipefail
export V4_SURFACE_PROBE_CWD="$(pwd -P)"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -n "${V4_SURFACE_PROBE:-}" ]; then
  PROBE="$V4_SURFACE_PROBE"
  rm -rf "$PROBE"
  mkdir -p "$PROBE"
else
  PROBE="$(mktemp -d "${TMPDIR:-/tmp}/v4-surface-probe.XXXXXX")"
  trap 'rm -rf "$PROBE"' EXIT
fi
cd "$PROBE"

git init -q .
git config user.email probe@local
git config user.name probe
printf 'def go(u):\n    return u\n' > app.py
git add -A
git commit -qm "the change this task is about"

v4() { "$HERE/bin/v4" --repo "$PROBE" "$@"; }

say() { printf '  %s\n' "$1"; }

echo "surface probe: the documented walkthrough, through bin/v4"

v4 init > /dev/null
say "init: $(ls .v4 | tr '\n' ' ')"

set +e
out="$(v4 doctor 2>&1)"
doctor_exit=$?
set -e
if [ "$doctor_exit" -eq 1 ]; then
  [[ "$out" == *"not wired -- the BAD lines above are silent in normal use" ]] || exit 1
elif [ "$doctor_exit" -ne 0 ]; then
  printf '%s\n' "$out" >&2
  exit 1
fi
[ -n "$out" ] || { echo "doctor printed nothing" >&2; exit 1; }
say "doctor: $(printf '%s' "$out" | grep -c '^  ') row(s)"

v4 task --id t-surface --scope "**/*.py" \
   --request "prove the command line still starts" > /dev/null
say "task: t-surface opened"

v4 derive --task t-surface > /dev/null
say "derive: $(v4 status --task t-surface --json | python3 -c 'import json,sys; print(len(json.load(sys.stdin)["claims"]))') claim(s)"

out="$(v4 status --task t-surface)"
[ -n "$out" ] || { echo "status printed nothing" >&2; exit 1; }
say "status: answered"

echo "5 command(s) ran through the launcher and each printed what it did"

# The checker injects a unique receipt path outside the judged source tree.
# This block is reached only after the actual launcher operations above.
if [ -n "${V4_SURFACE_RESULT:-}" ]; then
  python3 - <<'RECEIPT'
import json, os
from pathlib import Path
cases = ["init", "doctor", "task", "derive", "status"]
Path(os.environ["V4_SURFACE_RESULT"]).write_text(json.dumps({
    "schema": 1, "run_id": os.environ["V4_SURFACE_RUN_ID"],
    "kind": "command", "cwd": os.environ["V4_SURFACE_PROBE_CWD"],
    "planned": cases, "checks": [{"id": case, "status": "passed"} for case in cases],
    "errors": []}))
RECEIPT
fi
