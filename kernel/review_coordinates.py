"""Bind an old named-value finding to actual declaration execution evidence."""
from pathlib import Path, PurePosixPath

from .analysis import review_coordinate


def resolve_declaration(root, file, symbol):
    root = Path(root).resolve()
    rel = PurePosixPath(file or '')
    if (not file or rel.is_absolute() or '..' in rel.parts or '\\' in file or
            rel.suffix.lower() not in review_coordinate.TS_SUFFIXES):
        raise ValueError('declaration proof needs the original repository-relative JS/TS file')
    path = root / file
    if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():
        raise ValueError('declaration target is missing, outside the repository or a symlink')
    coordinate = review_coordinate.declaration(path.read_bytes().decode('utf-8'), symbol)
    if coordinate is None:
        raise ValueError('the original symbol is not one unique supported top-level value declaration; callable or unknown coordinates cannot be downgraded')
    return {'file':file, **coordinate}
