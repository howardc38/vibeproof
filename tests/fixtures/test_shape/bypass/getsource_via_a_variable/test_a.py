import inspect
from app import f


def test_a():
    g = inspect.getsource
    assert 'lock' in g(f)
