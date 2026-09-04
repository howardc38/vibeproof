#!/usr/bin/env bash
# Trigger for `runtime-proof`: open a task, for real, in a throwaway repo.
#
# The point of the kind is that a claim about a feature working is a claim
# about a row somebody can go and look at. `v4 task` is this framework's own
# smallest such feature, and `kernel/ledger.py`'s schema is the truth owner for
# what a task is -- so the proof is: run the command, then ask the table.
#
# A throwaway repo, not this one. Triggering into the live ledger would add a
# task row on every check, and a proof whose side effect is data is a proof
# nobody can run twice.
set -euo pipefail
# Resolved before any `cd`, because `$BASH_SOURCE` is relative to where this was
# invoked from and the script changes directory twice.
V4_HOME="${V4_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
# The run id `runtime-proof` substitutes into `trigger` and `truth`. It names
# what this run produced, so a query for it cannot be answered by anything an
# earlier run left behind -- which is the difference between a proof and a
# sentence.
RUN_ID="${1:?usage: runtime_probe.sh <run-id>}"
# One repo, reused. The run id is the task id inside it, not the path to it:
# `truth_command` is shared by every proof and must not move per run, which is
# also what a real adopter has -- one database, one row per run.
PROBE="${V4_RUNTIME_PROBE:-${TMPDIR:-/tmp}/v4-runtime-probe}"
mkdir -p "$PROBE"
cd "$PROBE"
git init -q . 2>/dev/null || true
git config user.email probe@local
git config user.name probe
printf 'x = 1\n' > app.py
git add -A
git commit -qm base 2>/dev/null || true
mkdir -p .v4
printf '{"test_command": "true", "policy": "allow_accepted_risk"}' > .v4/config.json
printf '{}' > .v4/claim_kinds.json
# Idempotent. Every proof in one `v4 check` shares one run id and runs its own
# trigger, so this executes once per proof -- and a trigger that dies the second
# time is reported as "the trigger failed before anything could be read back",
# which is true and useless. A re-run of a step is not an error anywhere else.
#
# Asked of the ledger directly. The first version asked `v4 status --task`,
# which exits 0 for a task that does not exist -- no claims, nothing blocked --
# so the guard skipped the work every time and the proof failed with count 0.
LEDGER=".git/v4/ledger.db"
HAVE=0
if [ -f "$LEDGER" ]; then
  HAVE=$(printf "select count(*) from task where id='%s';" "$RUN_ID" \
         | sqlite3 "$LEDGER" 2>/dev/null || echo 0)
fi
if [ "$HAVE" = "0" ]; then
  PYTHONPATH="$V4_HOME" python3 -m kernel.cli --repo . task \
    --id "$RUN_ID" --request 'runtime-proof probe' --scope '**' >/dev/null
fi
