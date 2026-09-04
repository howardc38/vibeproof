import unittest, app
class T(unittest.TestCase):
    def test_truncates_4(self):
        self.assertEqual(app.notify('a'*24), '[' + 'a'*8 + '] hi')
