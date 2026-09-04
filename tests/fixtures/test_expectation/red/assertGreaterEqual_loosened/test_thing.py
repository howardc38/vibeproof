import unittest


class T(unittest.TestCase):
    def test_x(self):
        self.assertGreaterEqual(compute(), 1)
