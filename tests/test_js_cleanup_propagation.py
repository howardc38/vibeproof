"""Cleanup can ignore its own failure while propagating the original failure."""
import unittest
from kernel.analysis.fail_closed import ts_findings


class CleanupPropagation(unittest.TestCase):
    def findings(self, source):
        return ts_findings('store.js', source)

    def test_cleanup_then_unconditional_primary_throw_is_not_silent(self):
        source = """function save() {
          try { fs.writeFileSync(file, data); }
          catch (primary) {
            try { fs.unlinkSync(tmp); } catch { /* best effort cleanup */ }
            throw primary;
          }
        }"""
        self.assertEqual(self.findings(source), [])

    def test_throw_immediately_after_try_also_propagates(self):
        self.assertEqual(self.findings("try { fetch(url); } catch {} ; throw error;"), [])

    def test_conditional_or_mentioned_throw_does_not_answer(self):
        for suffix in ('if (condition) throw error;',
                       '// throw error;\n continueWithSuccess();',
                       "const note = 'throw error';", 'throwError();'):
            with self.subTest(suffix=suffix):
                self.assertTrue(self.findings('try { fetch(url); } catch {} ' + suffix))

    def test_an_enclosing_catch_or_finally_can_intercept_the_throw(self):
        for source in (
            'try { try { fetch(url); } catch {} throw error; } catch { return true; }',
            'try { writeFile(data); } catch (primary) { '
            'try { unlinkSync(tmp); } catch {} throw primary; } finally { return true; }',
        ):
            with self.subTest(source=source):
                self.assertTrue(self.findings(source))


if __name__ == '__main__':
    unittest.main()
