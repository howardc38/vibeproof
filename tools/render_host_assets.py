#!/usr/bin/env python3
"""Regenerate or check the Codex projections of maintained framework prompts."""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from kernel.hosts import codex_assets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    drift = []
    for rel, content in codex_assets(ROOT).items():
        p = ROOT / rel
        if p.is_file() and p.read_text() == content:
            continue
        drift.append(rel)
        if args.write:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
    for rel in drift:
        print(("written " if args.write else "DRIFT ") + rel)
    return 0 if args.write or not drift else 1


if __name__ == "__main__":
    sys.exit(main())
