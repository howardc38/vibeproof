from pathlib import Path
import json


def test_f():
    d = json.loads(Path('data.json').read_text())
    assert d
