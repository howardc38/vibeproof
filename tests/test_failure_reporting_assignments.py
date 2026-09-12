"""A verified UI failure channel is repo vocabulary, not a global DOM exemption."""
import json
from pathlib import Path
import tempfile
import unittest

from kernel.analysis import fail_closed as rule


class FailureReportingAssignments(unittest.TestCase):
    def source(self, handler):
        return "async function save() { try { await fetch('/save'); } catch (e) { " + handler + " } }"

    def configured(self, targets):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root / ".v4").mkdir()
            (root / ".v4/fail_closed.json").write_text(json.dumps({
                "vocabulary": {"failure_reporting_assignments": targets}}))
            return rule.vocabulary_for(root)

    def test_only_a_declared_report_channel_answers_the_handler(self):
        source = self.source("view.error.textContent = 'Could not save';")
        self.assertEqual(len(rule.ts_findings('ui.js', source)), 1)
        vocab = self.configured(['view.error.textContent'])
        self.assertEqual(rule.ts_findings('ui.js', source, vocab), [])
        spaced = self.source("view . error . textContent = message;")
        self.assertEqual(rule.ts_findings('ui.js', spaced, vocab), [])
        self.assertTrue(rule.ts_findings('ui.js', self.source(''), vocab))
        self.assertTrue(vocab.http_verb_tails >= rule.SHIPPED.http_verb_tails)

    def test_mentions_comparisons_and_other_targets_do_not_count(self):
        vocab = self.configured(['view.error.textContent'])
        for body in ("// view.error.textContent = message;\n",
                     "const note = 'view.error.textContent = message';",
                     "view.error.textContent === message;",
                     "other.view.error.textContent = message;",
                     "other . view.error.textContent = message;",
                     "objects[key] . view.error.textContent = message;",
                     "preview.error.textContent = message;",
                     "view.error.textContent.extra = message;",
                     "unrelated.textContent = message;"):
            with self.subTest(handler=body):
                self.assertEqual(len(rule.ts_findings('ui.js', self.source(body), vocab)), 1)

    def test_a_declaration_cannot_be_a_wildcard_or_expression(self):
        for target in ('', '*', 'view', 'view.*', 'view[0].textContent', 'x = y', 7):
            with self.subTest(target=target):
                with self.assertRaisesRegex(ValueError, 'failure_reporting_assignments'):
                    self.configured([target])


class FailureReportingCalls(unittest.TestCase):
    def source(self, body):
        return "async function save(){try{await fetch('/save');}catch(e){"+body+"}}"

    def test_declared_bare_and_dotted_calls_require_actual_invocation(self):
        for target,call in [('sendReply','sendReply(res, 500, {error:e.message});'),
                            ('http.replyFailure','http . replyFailure(res,e);')]:
            source=self.source(call)
            self.assertTrue(rule.ts_findings('handler.js',source))
            vocab=rule.SHIPPED.union({'failure_reporting_calls':[target]})
            self.assertEqual(rule.ts_findings('handler.js',source,vocab),[])
            self.assertEqual(vocab.http_verb_tails,rule.SHIPPED.http_verb_tails)

    def test_lookalikes_mentions_and_declarations_do_not_report(self):
        vocab=rule.SHIPPED.union({'failure_reporting_calls':['sendReply','http.replyFailure']})
        for body in ['// sendReply(e);\n',"const text='sendReply(e)';",'const reference=sendReply;',
                     'other.sendReply(e);','other . sendReply(e);','objects[key] . sendReply(e);',
                     'sendReplyElse(e);','http.replyFailureExtra(e);','other.http.replyFailure(e);',
                     'function sendReply(e){}','const x={sendReply(e){}};',
                     'const x={sendReply(e): void {}};','new sendReply(e);']:
            with self.subTest(body=body):
                self.assertTrue(rule.ts_findings('handler.ts',self.source(body),vocab))

    def test_control_syntax_cannot_be_declared_as_a_function_call(self):
        vocab=rule.SHIPPED.union({'failure_reporting_calls':['if']})
        self.assertTrue(rule.ts_findings('handler.js',self.source('if(e){}'),vocab))

    def test_call_targets_cannot_be_patterns_or_expressions(self):
        for target in ['', '*', 'http.*', 'http[0].reply', 'f()', 'x = y', 7]:
            with self.subTest(target=target),self.assertRaisesRegex(ValueError,'failure_reporting_calls'):
                rule.SHIPPED.union({'failure_reporting_calls':[target]})

    def test_bad_container_does_not_become_individual_character_calls(self):
        for value in ['sendReply', {'sendReply':True}, 7]:
            with self.subTest(value=value),tempfile.TemporaryDirectory() as name:
                root=Path(name);(root/'.v4').mkdir()
                (root/'.v4/fail_closed.json').write_text(json.dumps({'vocabulary':{'failure_reporting_calls':value}}))
                with self.assertRaisesRegex(ValueError,'failure_reporting_calls'):
                    rule.vocabulary_for(root)


if __name__ == '__main__':
    unittest.main()
