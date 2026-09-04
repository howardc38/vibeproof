"""The path literal is one variable further away than the fixture next door.

`path_built_by_join` puts `'core.py'` in the expression that builds the path.
Here it is bound to a name first, so a rule that only looks inside the call --
or only one assignment back -- stops seeing it.

This exists because the fix for a false positive narrowed that search, and the
narrowing has to be a narrowing rather than a hole.
"""
from pathlib import Path


def test_b():
    where = 'core.py'
    p = Path('app') / where
    assert 'def f' in p.read_text()
