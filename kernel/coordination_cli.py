"""Validated JSON adapter; hosts own interpretation, dispatch and stop actions."""
import json
from pathlib import Path
import subprocess
import sys

from . import coordination, hashing, ledger


def command(args):
    root = Path(args.repo).resolve()
    conn = None
    try:
        if args.action == "schema":
            result = coordination.schema()
        else:
            if args.data:
                raw = sys.stdin.read() if args.data == "-" else Path(args.data).read_text(encoding="utf-8")
                data = json.loads(raw)
            else:
                data = {}
            coordination.validate(args.action, data)
            if args.action in ("status", "check-dispatch"):
                conn = ledger.connect_readonly(root)
            else:
                conn = ledger.connect(root)
            result = coordination.transition(conn, root, args.action, data)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if args.action == "check-dispatch" and not result["ready"] else 0
    except (ValueError, OSError, subprocess.CalledProcessError, hashing.SourceUnreadable) as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()


def configure_parser(parser):
    actions = parser.add_subparsers(dest="action", required=True)
    for name in ("schema", *coordination.schema()["commands"]):
        action = actions.add_parser(name)
        action.add_argument("--data", help="JSON file, or - for stdin")
        action.set_defaults(fn=command)
    return parser
