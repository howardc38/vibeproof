import inspect
from app import f


def test_b():
    assert inspect.getsourcelines(f)
