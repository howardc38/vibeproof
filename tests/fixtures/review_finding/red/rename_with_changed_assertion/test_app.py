import unittest
from app import calculate
class Arithmetic(unittest.TestCase):
    def test_difference(self):
        self.assertEqual(calculate(7), 5)
