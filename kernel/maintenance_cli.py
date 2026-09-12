"""CLI adapter for the maintenance workflow; hosts own actual job dispatch."""
import inspect
import json
from pathlib import Path
import sys

from . import maintenance as m
from .notifications import DeliveryError


def command(args):
    root = Path(args.repo).resolve()
    try:
        data = json.loads(sys.stdin.read() if args.data == "-" else Path(args.data).read_text()) if args.data else {}
        if not isinstance(data, dict):
            raise ValueError("--data must contain a JSON object")
        if args.action == "schema":
            result = m.schema()
        elif args.action == "setup":
            unknown = set(data) - {"mode", "repair_scope", "limits"}
            if unknown:
                raise ValueError("unknown setup fields: " + ", ".join(sorted(unknown)))
            previous = m.settings(root)
            result = m.configure(root, mode=data.get("mode", previous["mode"]),
                                 repair_scope=data.get("repair_scope", previous["repair_scope"]),
                                 limits=data.get("limits", previous.get("limits", {})))
            result = {"configuration": result, "host_action_required": "Create/update the native scheduled task, read it back, then record its observation with maintain schedule. No job was created by this command."}
        elif args.action == "status":
            result = m.status(root)
        elif args.action == "start":
            if not args.host:
                raise ValueError("start needs --host")
            result = m.start(root, run_id=args.id, host=args.host, session=args.session or "manual",
                             task=args.task, context_task=args.context_task, selected=args.lens,
                             trigger=args.trigger, job_id=args.job)
        elif args.action == "finish":
            result = m.finish(root, args.id, abandon_reason=args.why, coordination=data.get("coordination"))
        elif args.action == "handoff":
            result = m.handoff(root, data)
        elif args.action == "schedule":
            # Validate against the producer's signature before it can write.
            # Copying an observation's output-only observed_at back into this
            # CLI used to escape as TypeError; missing fields did the same.
            try:
                inspect.signature(m.observe_schedule).bind(root, **data)
            except TypeError as exc:
                raise ValueError("invalid schedule arguments: " + str(exc)) from exc
            result = m.observe_schedule(root, **data)
        elif args.action == "resume" and args.id:
            result = m.resume_run(root, args.id)
        elif args.action in ("pause", "resume"):
            result = {"requested": args.action, "schedules": m.settings(root).get("schedules", {}),
                      "host_action_required": "Update the actual host job and record the returned observation with maintain schedule; local configuration is not scheduler truth."}
        else:
            from . import notifications
            result = notifications.command(root, args.action, data, identity=args.id, once=args.once)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if args.action == "finish" and result["status"] not in ("complete", "abandoned"):
            return 1
        if args.action == "notify" and (result.get("status") in ("failed", "unknown", "sending") or any(r.get("status") in ("failed", "unknown", "sending") for r in result.get("results", []))):
            return 1
        return 0
    except (ValueError, OSError, KeyError, DeliveryError) as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 2
