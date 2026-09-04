import unittest, app
class T(unittest.TestCase):
    def test_truncates_2(self):
        self.assertEqual(app.notify('a'*22), '[' + 'a'*8 + '] hi')
