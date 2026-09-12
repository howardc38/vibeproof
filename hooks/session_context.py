#!/usr/bin/env python3
"""Give Codex a stable identity to bind after its V4 task has been opened."""
import json
import sys
from pathlib import Path
import _framework


def main():
    try:
        p = json.load(sys.stdin)
        session = p["session_id"]
        if not isinstance(session, str) or not session:
            raise ValueError("missing session_id")
    except (ValueError, KeyError, TypeError) as exc:
        print(f"v4 session_context: cannot identify host session ({exc})", file=sys.stderr)
        print("{}")
        return 0
    _framework.set_context(p)
    root = _framework.repo_root()
    if _framework.db_path(root) is not None:
        failure = _framework.record_seen(root, None, "(host identity)",
                                         session=session, hook="session_context", basis="identity")
        if failure:
            print("v4 session_context: identity event was not recorded: " + failure, file=sys.stderr)
    agent = str(p.get("agent_id") or "")
    context = (
        "Vibeproof host identity: " + json.dumps({"host": _framework.host(), "session": session, "agent": agent})
        + ". If this task is using the vibeproof development workflow, after opening or receiving "
        "its V4 task, bind from that task's worktree with: ./bin/v4 --repo <absolute-worktree> "
        "host bind --task <V4-task-id> --host " + _framework.host() + " --session " + json.dumps(session)
        + (" --agent " + json.dumps(agent) if agent else "")
        + ". A subagent should use the identity carrying its agent_id, rather than an inherited parent identity. Do not open or bind a development task for a read-only question. "
        "Reviewers and monitors without an assigned open development task do not bind the standing repo-review task. "
        "Workers return results to the orchestrator; they do not ship or accept risk for themselves."
    )
    event = p.get("hook_event_name", "SessionStart")
    if event == "SessionStart":
        try:
            from kernel.maintenance import session_summary
            context += "\n" + session_summary(root)
        except ImportError:
            context += "\nVibeproof maintenance summary unavailable: framework import failed."
    print(json.dumps({"hookSpecificOutput": {"hookEventName": event, "additionalContext": context}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
