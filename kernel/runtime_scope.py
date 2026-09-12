"""One applicability decision for the runtime detector and checker.

`covers` selects probes; it is not permission to ignore arbitrary new code.
Only a known, nonempty test/documentation-only diff outside every scoped
probe is not applicable. Unknown files/diffs keep the runtime obligation.
"""
import ast
import subprocess
from pathlib import Path

from . import hashing
from .analysis import subject_files


def changed_paths(root, subject):
    """The complete Git change, or None when its base cannot be read."""
    base = subject.get("diff_base")
    if not base:
        return None
    try:
        return sorted(subject_files.changed_since(root, base))
    except (OSError, subject_files.DiffUnreadable):
        return None


def _non_runtime_source(path, source):
    p = Path(path)
    if p.suffix.lower() in (".md", ".rst"):
        return True
    # Rust implementation modules may contain inline tests. is_test correctly
    # recognizes those tests, but does not say the whole file is test-only.
    if p.suffix == ".rs" and not {"tests", "benches"}.intersection(p.parts):
        return False
    if p.suffix == ".py":
        try:
            ast.parse(source)
        except SyntaxError:
            return False
    return subject_files.is_test(path, source=source)


def _non_runtime_path(root, base, path):
    """Check both versions: deleting/replacing production code is runtime work.

    Symlinks, executable files, unreadable content and unknown file types are
    deliberately not inferred to be documentation or test-only work.
    """
    sources = []
    p = Path(root) / path
    try:
        if p.is_symlink():
            return False
        if p.exists():
            if not p.is_file() or p.stat().st_mode & 0o111:
                return False
            sources.append(p.read_text(encoding="utf-8"))
        old = subprocess.run(["git", "ls-tree", "-z", base, "--", path],
                             cwd=root, capture_output=True, check=True)
        if old.stdout:
            mode, kind, oid = old.stdout.split(b"\t", 1)[0].split()
            if mode != b"100644" or kind != b"blob":
                return False
            blob = subprocess.run(["git", "cat-file", "blob", oid.decode("ascii")],
                                  cwd=root, capture_output=True, check=True)
            sources.append(blob.stdout.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, subprocess.CalledProcessError):
        return False
    return bool(sources) and all(_non_runtime_source(path, s) for s in sources)


def select(root, subject, proofs):
    """Return (selected proofs, durable scope explanation).

    No base retains the existing repo-level execution. Unscoped legacy proofs
    also still run. Explicit covers takes precedence over file conventions.
    """
    changed = changed_paths(root, subject)
    scope = {"status": "applicable", "changed": changed}
    if changed is None:
        scope.update(status="unknown", reason="change base unavailable; run declared proofs")
        return proofs, scope
    # Recording a proof writes an anchor/export; these already have owners in
    # hashing/scope and ledger audit. They cannot turn a test-only task into a
    # runtime change on its second check. Adopter-edited config stays visible.
    outputs = [f for f in changed if hashing.kernel_written(f, root)]
    if outputs:
        scope["kernel_outputs"] = outputs
        changed = [f for f in changed if f not in outputs]
        scope["changed"] = changed
    scoped = [p for p in proofs if p.get("covers")]
    unscoped = [p for p in proofs if not p.get("covers")]
    if not scoped:
        return proofs, scope
    live = [p for p in scoped
            if any(subject_files.matches(f, p["covers"]) for f in changed)]
    if live or unscoped:
        return live + unscoped, scope
    if changed and all(_non_runtime_path(root, subject["diff_base"], f) for f in changed):
        scope.update(status="not_applicable",
                     reason="only test/documentation files changed, outside all declared covers")
    else:
        scope.update(status="uncovered", reason="no declared proof covers this change")
    return [], scope
