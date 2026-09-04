import unittest, inspect, app
class T(unittest.TestCase):
    def test_source(self):
        self.assertIn('name[:8]', inspect.getsource(app.notify))
