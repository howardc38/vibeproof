import unittest
from app import calculate
class Arithmetic(unittest.TestCase):
    def test_difference(self):
        self.assertEqual(calculate(5), 3)
    def test_another(self):
        self.assertEqual(calculate(5), 3)
