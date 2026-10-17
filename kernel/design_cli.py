"""JSON CLI adapter; document authorship and review remain host actions."""
import json
from pathlib import Path
import sqlite3
import sys

from . import design, hashing, ledger


def configure_parser(parser):
    parser.add_argument("action", choices=("schema", "status", "register", "disposition", "review"))
    parser.add_argument("--data", help="JSON input file, or - for stdin")
    parser.set_defaults(fn=command)
    return parser


def command(args):
    try:
        root = Path(args.repo).resolve()
        if args.action == "schema":
            if args.data:
                raise ValueError("schema takes no --data")
            result = design.schema()
        else:
            data = json.loads(sys.stdin.read() if args.data == "-" else Path(args.data).read_text()) if args.data else {}
            design.validate(args.action, data)
            conn = ledger.connect_readonly(root) if args.action == "status" else ledger.connect(root)
            try:
                result = design.snapshot(conn, root) if args.action == "status" else getattr(design, args.action)(conn, root, data)
            finally:
                conn.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error, ledger.NoSuchTask, hashing.SourceUnreadable) as exc:
        print("REFUSED: " + str(exc), file=sys.stderr)
        return 2
