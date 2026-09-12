"""Read committed rename evidence; never rewrite a claim or infer a verdict."""
from pathlib import Path, PurePosixPath
import subprocess

from .analysis import symbol_rename


class InvalidRename(ValueError):
    pass


def _git(root, *args):
    try:
        result = subprocess.run(["git", *args], cwd=root, capture_output=True,
                                text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        raise InvalidRename(f"could not read rename history: {exc}") from exc
    if result.returncode:
        raise InvalidRename(f"rename history unavailable: {result.stderr.strip()[:200] or 'Git refused the requested ancestry'}")
    return result.stdout


def resolve_rename(root, file, original_symbol, commits, *, red_parent=None):
    """Resolve explicit, ordered, same-file Python rename commits to live code.

    Each immutable commit must prove a pure rename against its sole parent.
    The current body may have evolved afterwards; ordinary red/green proof
    still judges it. The producer and checker both call this same validator.
    """
    root = Path(root).resolve()
    if not isinstance(file, str) or not file or "\\" in file or "\x00" in file:
        raise InvalidRename("rename proof needs the claim's repository-relative Python file")
    rel = PurePosixPath(file)
    if rel.is_absolute() or ".." in rel.parts or rel.suffix != ".py":
        raise InvalidRename("rename proof supports same-file Python functions/methods only")
    path = root / file
    if not path.resolve().is_relative_to(root) or path.is_symlink():
        raise InvalidRename("rename target escapes the repository or is a symlink")
    if not isinstance(original_symbol, str) or not original_symbol.isidentifier():
        raise InvalidRename("rename proof needs the original named function/method")
    if not isinstance(commits, list) or not commits or any(
            not isinstance(ref, str) or not ref.strip() for ref in commits):
        raise InvalidRename("rename_commits must be a nonempty list of commit references")
    _git(root, "ls-files", "--error-unmatch", "--", file)
    current, scope, previous = original_symbol, None, None
    normalized, steps = [], []
    try:
        for ref in commits:
            commit = _git(root, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}").strip()
            if commit in normalized:
                raise InvalidRename("a rename commit may appear only once")
            ancestry = _git(root, "rev-list", "--parents", "-n", "1", commit).split()
            if len(ancestry) != 2:
                raise InvalidRename("each rename commit must have exactly one parent")
            parent = ancestry[1]
            _git(root, "merge-base", "--is-ancestor", commit, "HEAD")
            if previous:
                _git(root, "merge-base", "--is-ancestor", previous, parent)
            before = _git(root, "show", f"{parent}:{file}")
            after = _git(root, "show", f"{commit}:{file}")
            renamed, where = symbol_rename.prove(before, after, current)
            if scope is not None and where != scope:
                raise InvalidRename("rename chain changed the enclosing scope")
            steps.append({"commit": commit, "from": current, "to": renamed})
            current, scope, previous = renamed, where, commit
            normalized.append(commit)
        live = path.read_text(encoding="utf-8")
        _, where, _ = symbol_rename.unique(live, current)
        if where != scope or any(name == original_symbol for name, _, _ in symbol_rename.definitions(live)):
            raise InvalidRename("current code no longer has an unambiguous renamed target")
    except (ValueError, SyntaxError, OSError, UnicodeError) as exc:
        raise InvalidRename(str(exc)) from exc
    proof = {"file": file, "original_symbol": original_symbol, "symbol": current,
             "rename_commits": normalized, "renames": steps}
    if red_parent is not None:
        if not isinstance(red_parent, str) or not red_parent.strip():
            raise InvalidRename("the red parent must name a commit")
        parent = _git(root, "rev-parse", "--verify", "--end-of-options", red_parent + "^{commit}").strip()
        try:
            _git(root, "merge-base", "--is-ancestor", normalized[-1], parent)
            _git(root, "merge-base", "--is-ancestor", parent, "HEAD")
            _, parent_scope, _ = symbol_rename.unique(_git(root, "show", f"{parent}:{file}"), current)
            if parent_scope != scope:
                raise ValueError("the red parent has a different enclosing scope")
        except (ValueError, SyntaxError) as exc:
            raise InvalidRename("the red parent must already contain the verified renamed target; a missing name cannot supply a red control. Use a mutation or a parent after the rename") from exc
        proof["parent_commit"] = parent
    return proof
