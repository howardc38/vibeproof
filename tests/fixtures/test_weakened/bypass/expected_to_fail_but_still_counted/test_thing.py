import unittest


def test_a():
    assert 1


@unittest.expectedFailure
def test_b():
    assert 2
