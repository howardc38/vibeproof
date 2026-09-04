"""Ask Go's own parser what a Go file defines.  The bridge, and nothing else.

`kernel/analysis/pysource.py` is the Python side of this question and answers
it with `ast`, because Python ships one.  Go ships one too -- `go/ast`,
`go/parser`, `go/token` are all standard library -- and a repo written in Go
has a Go toolchain by definition, so reaching it through `subprocess` costs the
Python side no dependency at all.  `kernel/analysis/dependency_audit.py:28-32`
refused a *stdlib* module for adopter compatibility; a third-party parser would
have been the first dependency this framework has ever taken.

**`None` is not an empty set.**  Every failure here returns `None`: no
toolchain, a build that did not work, a file that will not parse, a timeout.
`docs/EVIDENCE.md` §4 is what an empty set costs -- eight checkers returned
PASS on a Go repo having parsed no Go, and this module exists on the other side
of that repair.

The compiled helper goes in the system temp directory, keyed by the source's
own sha.  Never inside the repo being judged: a Go binary in an adopter's tree
is 2 MB of untracked build output landing in whatever `git status` and
`v4 scope` read next.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

#: The Go program, beside this module.  `kernel/` is pointed at and never
#: copied (SPEC.md §8.8), so an adopter reaches this through `PYTHONPATH` and
#: `copy_files` needs no entry for it.
SOURCE = Path(__file__).resolve().parent / "_go" / "symbols.go"
#: The second Go program, for `test-shape`. Separate rather than one binary
#: with a mode flag: they answer different questions for different callers, and
#: `pysource.py` states when a shared abstraction is earned -- after the
#: repetition, not before it.
SHAPE_SOURCE = Path(__file__).resolve().parent / "_go" / "shape.go"

BUILD_TIMEOUT = 120
RUN_TIMEOUT = 30

#: One cache entry per program, keyed by that program's own sha.
_built: dict = {}


def available() -> bool:
    """Is there a Go toolchain to ask."""
    return shutil.which("go") is not None


def _build(source: Path = None) -> Path | None:
    """Compile once per source sha, into the system temp directory.

    `GOPROXY=off` and `GOFLAGS=-mod=mod`: the programs import nothing outside
    the standard library, and a benchmark container is air-gapped.  A build
    that reaches for the network would fail there and nowhere else.
    """
    source = source or SOURCE
    key = str(source)
    if key in _built:
        return _built[key]
    _built[key] = None                     # a failed build is not retried
    if not available() or not source.is_file():
        return None
    sha = hashlib.sha256(source.read_bytes()).hexdigest()[:16]
    out = Path(tempfile.gettempdir()) / f"v4-go-{source.stem}-{sha}"
    if out.is_file() and os.access(out, os.X_OK):
        _built[key] = out
        return out
    with tempfile.TemporaryDirectory() as td:
        work = Path(td) / source.name
        work.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        # Built beside the final name and moved onto it, never written to it.
        # The cache is one shared path per source sha, and `v4 accept` runs the
        # suite while a worktree may be running `v4 check`: two builds landing
        # on the same file means one of them can exec a binary the other is
        # half way through writing. Observed once as a suite that failed inside
        # `accept` and passed on its own a minute later, which is the shape a
        # race takes.
        staged = Path(td) / "built"
        env = dict(os.environ, GOPROXY="off", GOFLAGS="-mod=mod",
                   GOCACHE=os.environ.get("GOCACHE") or str(Path(td) / "cache"))
        try:
            r = subprocess.run(["go", "build", "-o", str(staged), str(work)],
                               cwd=td, capture_output=True, text=True,
                               env=env, timeout=BUILD_TIMEOUT)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0 or not staged.is_file():
            return None
        try:
            # Same filesystem: the system temp directory holds both, so this is
            # a rename and not a copy. A reader sees the old file or the new
            # one, never a partial.
            os.replace(staged, out)
        except OSError:
            # Another process got there first, which is the outcome we want.
            if not (out.is_file() and os.access(out, os.X_OK)):
                return None
    _built[key] = out
    return out


def _run(binary: Path, path: Path):
    try:
        r = subprocess.run([str(binary), str(path)], capture_output=True,
                           text=True, timeout=RUN_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None                      # syntax error, unreadable, anything
    try:
        return json.loads(r.stdout or "null")
    except ValueError:
        return None


def shape(path) -> dict | None:
    """The structural facts `test-shape` needs about one Go file, or `None`.

    Structure only: which functions there are, which calls each makes with
    which string literals, which names it references, and where it spawns a
    goroutine and whether that was inside a loop. Every judgement -- is this
    path a source file, is this fan-out bounded -- is made in Python beside the
    same judgement for Python code, so one rule has one home and two
    extractors.
    """
    path = Path(path)
    if path.suffix != ".go":
        return None
    binary = _build(SHAPE_SOURCE)
    if binary is None:
        return None
    got = _run(binary, path)
    return got if isinstance(got, dict) else None


def shape_source(text: str) -> dict | None:
    """`shape` for source that is not on disk.

    `test-weakened` compares a file against its own content at the task's base
    commit, and that content comes out of `git show` -- there is no path to
    hand a parser. Written to a temp file rather than piped, because
    `go/parser` reports positions against a file and the line numbers are part
    of what the callers use.
    """
    if text is None:
        return None
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "source.go"
        try:
            f.write_text(text, encoding="utf-8")
        except OSError:
            return None
        return shape(f)


def names_in(path) -> set | None:
    """Every name this Go file binds, or `None` if we could not read it.

    `None` covers all four failures on purpose, because a caller can do only
    one thing with any of them: not judge.
    """
    path = Path(path)
    if path.suffix != ".go":
        return None
    binary = _build(SOURCE)
    if binary is None:
        return None
    got = _run(binary, path)
    return set(got) if isinstance(got, list) else None
