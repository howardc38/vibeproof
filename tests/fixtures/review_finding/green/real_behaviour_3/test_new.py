import unittest, app
class T(unittest.TestCase):
    def test_truncates_3(self):
        self.assertEqual(app.notify('a'*23), '[' + 'a'*8 + '] hi')
