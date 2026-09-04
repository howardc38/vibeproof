import unittest, app
class T(unittest.TestCase):
    def test_truncates_1(self):
        self.assertEqual(app.notify('a'*21), '[' + 'a'*8 + '] hi')
