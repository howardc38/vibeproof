#!/usr/bin/env python3
"""Demo adapter: execute a real review claim, then ask the kernel for its state.

Used only inside run.py's disposable repository. This is not an installer or a
ship workflow. It never fabricates attempts or assigns a claim's state.
"""
import argparse
import json
from pathlib import Path
import sys

FRAMEWORK = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(FRAMEWORK))
from kernel import config, engagement, hashing, layout, ledger, lifecycle, review, state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "status"))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--parent")
    args = parser.parse_args()
    root = args.repo.resolve()
    if args.parent:
        kind = json.loads((FRAMEWORK / ".v4/claim_kinds.json").read_text())["review-finding"]
        entry = dict(json.loads((FRAMEWORK / ".v4/checkers.json").read_text())["review-finding"])
        checker = root / entry["path"]
        checker.parent.mkdir(parents=True, exist_ok=True)
        checker.write_bytes((FRAMEWORK / entry["path"]).read_bytes())
        # review-finding also supports browser proof. Its registered program
        # includes these executable adapters even for this Python-only case.
        # Copy the actual inputs; do not replace or drop the registration pin.
        for source in layout.surface_programs(FRAMEWORK):
            destination = root / source.relative_to(FRAMEWORK)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())
        # Use the published checker registration; no fixture verification is
        # claimed here. The entry and program pins still have to match.
        (root / ".v4/claim_kinds.json").write_text(json.dumps({"review-finding": kind}))
        (root / ".v4/checkers.json").write_text(json.dumps({"review-finding": entry}))
    cfg = config.RepoConfig(root)
    with ledger.connect(root) as conn:
        if args.parent:
            cid, _, _ = review.raise_finding(conn, cfg, task_id=None,
                file="checkout.py", symbol="total",
                note="A 20-unit discount must reduce a 100-unit cart to 80.")
            review.bind_closing_test(conn, claim_id=cid, root=root,
                test_path="tests/test_checkout.py", command="python3 {path}",
                parent_commit=args.parent)
            row = conn.execute("SELECT * FROM claim WHERE id=?", (cid,)).fetchone()
            sentence = "checkout.py total must subtract the discount; the regression calls total(100, 20) and expects 80, with the same assertion failing on the broken parent."
            accepted, reason = engagement.judge(conn, cfg, claim_row=row, sentence=sentence)
            engagement.record(conn, task_id=ledger.REVIEW_TASK, claim_id=cid,
                              sentence=sentence, verdict="accepted" if accepted else "refused", reason=reason)
            if not accepted:
                raise RuntimeError(f"Demo engagement refused: {reason}")
        row = conn.execute("SELECT * FROM claim WHERE task_id=?", (ledger.REVIEW_TASK,)).fetchone()
        if row is None:
            raise RuntimeError("Run the check with --parent before asking for its state")
        if args.action == "check":
            results = lifecycle.check(conn, cfg, ledger.REVIEW_TASK, only=[row["id"]])
            for _, skipped, result in results:
                if skipped:
                    raise RuntimeError(f"Review checker did not run: {skipped}")
                print(result.stdout.strip())
                if result.exit_code:
                    print(result.stderr, file=sys.stderr)
                    return result.exit_code
        kwargs = dict(kinds_cfg=cfg.kinds, config_sha=cfg.sha,
                      checker_sha_of=cfg.checker_sha_on_disk,
                      facts_sha_of=cfg.facts_sha_for, reads_of=cfg.reads_for)
        actual = state.claim_state(conn, root, row, **kwargs)
        attempt = ledger.latest_attempt(conn, row["id"])
        reason = state.stale_reason(conn, root, row, attempt, **kwargs) if attempt else "no attempt"
        print(json.dumps({"state": actual, "reason": reason,
                          "attempts": conn.execute("SELECT count(*) FROM attempt WHERE claim_id=?", (row["id"],)).fetchone()[0],
                          "source_sha256": hashing.file_sha(root / "checkout.py")}, sort_keys=True))
        return 0


if __name__ == "__main__":
    sys.exit(main())
