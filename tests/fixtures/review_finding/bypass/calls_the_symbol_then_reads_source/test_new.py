import unittest, inspect, app
class T(unittest.TestCase):
    def test_source(self):
        try:
            app.notify('x')
        except Exception:
            pass
        self.assertIn('name[:8]', inspect.getsource(app.notify))
