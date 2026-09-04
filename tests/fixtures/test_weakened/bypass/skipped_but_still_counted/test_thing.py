import unittest


def test_a():
    assert 1


@unittest.skip('later')
def test_b():
    assert 2
