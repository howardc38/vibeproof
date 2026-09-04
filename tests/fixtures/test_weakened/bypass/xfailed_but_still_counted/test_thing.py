import pytest


def test_a():
    assert 1


@pytest.mark.xfail
def test_b():
    assert 2
