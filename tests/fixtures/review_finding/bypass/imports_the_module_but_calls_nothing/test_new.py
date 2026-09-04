import unittest
import app  # noqa
class T(unittest.TestCase):
    def test_i(self):
        self.assertTrue(hasattr(app, 'notify'))
