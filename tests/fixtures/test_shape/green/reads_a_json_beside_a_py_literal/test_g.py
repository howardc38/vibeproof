"""Reads a JSON manifest, in a test that also mentions a `.py` path as data.

Taken from this repo's own `test_a_kind_held_back_does_not_lose_its_checker_s_record`,
which was reported as reading source text. It reads `.v4/installed.json`; the
`.py` string is a key it asserts about, and the call has no path to it.
"""
import json
from pathlib import Path


def test_g(tmp_path):
    manifest = Path(tmp_path) / 'installed.json'
    manifest.write_text('{"checkers/c.py": "abc"}')
    shipped = json.loads(manifest.read_text())
    assert 'checkers/c.py' in shipped
