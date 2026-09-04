from pathlib import Path


def test_b():
    p = Path('app') / 'core.py'
    assert 'def f' in p.read_text()
