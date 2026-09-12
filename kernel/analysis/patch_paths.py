"""Read the file operations in a Codex apply_patch without applying it.

Patch content is not a shell command. Headers are accepted only outside hunks;
added text that happens to look like a header remains file content.
"""
from pathlib import PurePosixPath


class InvalidPatch(ValueError):
    pass


def paths(text: str) -> list[str]:
    """Every source/destination path, or an explicit parse failure."""
    if not isinstance(text, str):
        raise InvalidPatch("tool_input.command must be patch text")
    lines = text.splitlines()
    if len(lines) < 3 or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise InvalidPatch("expected a complete Begin Patch / End Patch envelope")
    found = []
    current = None
    has_body = False
    for line in lines[1:-1]:
        op = next((name for name in ("Add File", "Update File", "Delete File")
                   if line.startswith(f"*** {name}: ")), None)
        if op:
            name = line[len(op) + 6:]
            _path(name)
            found.append(name)
            current, has_body = op, op == "Delete File"
        elif line.startswith("*** Move to: "):
            if current != "Update File" or has_body:
                raise InvalidPatch("Move to must follow an Update File header")
            name = line[len("*** Move to: "):]
            _path(name)
            found.append(name)
        elif current == "Add File" and line.startswith("+"):
            has_body = True
        elif current == "Update File" and (
                line.startswith(("@@", " ", "+", "-")) or line == "*** End of File"):
            has_body = True
        else:
            raise InvalidPatch("unsupported or malformed patch line")
    if not found:
        raise InvalidPatch("patch has no complete file operation")
    return list(dict.fromkeys(found))


def _path(value: str) -> None:
    if not value or "\x00" in value or "\r" in value or "\\" in value:
        raise InvalidPatch("empty or ambiguous patch path")
    if value in (".", "..") or ".." in PurePosixPath(value).parts:
        raise InvalidPatch("patch paths may not traverse a parent directory")
