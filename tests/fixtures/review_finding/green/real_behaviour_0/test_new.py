import unittest, app
class T(unittest.TestCase):
    def test_truncates_0(self):
        self.assertEqual(app.notify('a'*20), '[' + 'a'*8 + '] hi')
