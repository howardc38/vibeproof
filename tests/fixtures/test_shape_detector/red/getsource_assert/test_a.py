import inspect
from app import f


def test_a():
    assert 'lock' in inspect.getsource(f)
