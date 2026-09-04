import argparse, ast, json, sys
from pathlib import Path
def main() -> int:
    """The program, as a function.

    A module-level script has no symbol a stack frame can be named after,
    so `review.resolve_symbol` refuses every `--symbol` for it and a
    finding raised with none has nothing for `redgreen` to trace -- which
    leaves a signature as the only exit, the outcome that function exists
    to prevent.

    `sys.exit` inside stays `sys.exit`: it raises, so it travels out
    through `main()` unchanged and the verdict is the one it always was.
    """
    p = argparse.ArgumentParser(); p.add_argument("--subject", required=True)
    p.add_argument("--facts"); p.add_argument("--out")
    a = p.parse_args()
    s = json.loads(Path(a.subject).read_text())
    root = Path(s["repo_root"]); bad = []
    for ref in s["subject_refs"]:
        if ref["kind"] != "file": continue
        f = root / ref["path"]
        if f.suffix != ".py": continue
        tree = ast.parse(f.read_text())
        for n in ast.walk(tree):
            if not isinstance(n, ast.Try): continue
            has_out = any(isinstance(x, ast.Call) and "post" in ast.dump(x.func) for x in ast.walk(n))
            if not has_out: continue
            for h in n.handlers:
                if not any(isinstance(x, ast.Raise) for x in ast.walk(h)):
                    bad.append(f"{ref['path']}:{h.lineno} swallows after outbound write")
    for b in bad: print(b)
    sys.exit(1 if bad else 0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
