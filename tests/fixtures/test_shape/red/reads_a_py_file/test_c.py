from pathlib import Path


def test_c():
    assert 'def f' in Path('app.py').read_text()
