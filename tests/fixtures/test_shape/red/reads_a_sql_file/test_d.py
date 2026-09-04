from pathlib import Path


def test_d():
    assert 'CREATE' in Path('m.sql').read_text()
