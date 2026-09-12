import unittest
from app import compute
class Arithmetic(unittest.TestCase):
    def test_old(self):
        self.assertEqual(compute(5), 3)
