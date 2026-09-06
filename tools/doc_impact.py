#!/usr/bin/env python3
"""List documentation to review for a source diff; this is not a semantic gate."""
import argparse
import fnmatch
import json
from pathlib import Path
import subprocess

RULES = [
    (["kernel/cli.py"], ["docs/SPEC.md", "docs/USING.md", "docs/REFERENCE.md", ".claude/commands/"]),
    (["kernel/state.py", "kernel/hashing.py", "kernel/ledger.py", "kernel/risk.py", "kernel/lifecycle.py",
      "kernel/config.py", ".v4/config.json", ".v4/risk_rubric.json"],
     ["docs/SPEC.md", "docs/REFERENCE.md", "docs/FEATURES.md", "README*.md"]),
    (["hooks/*", "kernel/install.py", "kernel/init.py", "kernel/facts.py", "kernel/analysis/facts_grammar.py",
      ".claude/settings*.json", ".v4/facts*.json"],
     ["docs/GETTING_STARTED*.md", "docs/FACTS.md", "docs/SPEC.md", ".claude/", ".github/monitor/"]),
    (["checkers/*", "detectors/*", "kernel/analysis/*", ".v4/checkers.json", ".v4/detectors.json", ".v4/layers.json"],
     ["docs/SPEC.md", "docs/FEATURES.md", "docs/REFERENCE.md", "docs/launch/CLAIMS.md", "docs/launch/VIDEO.md"]),
    (["kernel/doctrine.py", ".v4/claim_kinds.json", ".v4/lenses/*"],
     ["CLAUDE.md (regenerate)", "docs/SPEC.md", "docs/FEATURES.md", "docs/USING.md"]),
    (["tools/export_public.py", "tools/publish_public.py", "publishing/*", ".github/workflows/*"],
     ["docs/SYNC.md", "CONTRIBUTING.md", "AGENTS.md"]),
]


def affected(paths):
    by_doc = {}
    for path in paths:
        matched = False
        for patterns, docs in RULES:
            if any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns):
                matched = True
                for doc in docs:
                    by_doc.setdefault(doc, set()).add(path)
        if not matched and path.startswith(("kernel/", "hooks/", "checkers/", "detectors/")):
            by_doc.setdefault("docs/SPEC.md (new/unmapped source)", set()).add(path)
    return {doc: sorted(sources) for doc, sources in sorted(by_doc.items())}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base", default="HEAD^")
    p.add_argument("--head", default="HEAD")
    p.add_argument("--root", type=Path, default=Path.cwd())
    a = p.parse_args()
    result = subprocess.run(["git", "-C", str(a.root), "diff", "--name-only", a.base, a.head, "--"],
                            capture_output=True, text=True, check=True)
    print(json.dumps({"base": a.base, "head": a.head, "review": affected(result.stdout.splitlines()),
                      "note": "Review aid only. Pins, flags, examples and semantic source review remain necessary."}, indent=2))


if __name__ == "__main__":
    main()
