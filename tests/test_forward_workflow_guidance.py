"""Real forward use: sentence punctuation and assigned test-only findings."""
import contextlib
import io
import types
import unittest

from kernel import cli, ledger, lifecycle, request_cover
from tests.test_the_numbers_beside_a_widen_and_a_request import _repo


class QuoteBoundaries(unittest.TestCase):
    def test_one_exact_sentence_including_its_delimiter_is_one_piece(self):
        for request,quote in [('Return the port. Reject invalid values. Add tests.', 'Reject invalid values.'),
                              ('保存資料。顯示結果。', '保存資料。'),
                              ('Save data! Show result?', 'Save data!')]:
            self.assertFalse(request_cover.spans_a_boundary(request,quote))

    def test_real_cross_clause_quotes_still_need_separate_entries(self):
        for request,quote in [('Return the port. Reject invalid values. Add tests.', 'Return the port. Reject invalid values.'),
                              ('保存資料。顯示結果。', '保存資料。顯示結果。')]:
            self.assertTrue(request_cover.spans_a_boundary(request,quote))

    def test_paths_and_decimal_points_are_not_clause_boundaries(self):
        self.assertFalse(request_cover.spans_a_boundary('Read src/app.py at 08:00. Add tests.', 'Read src/app.py at 08:00.'))


class AssignedFindingGuidance(unittest.TestCase):
    def output(self,origin):
        conn,cfg,root=_repo(self)
        lifecycle.open_task(conn,cfg,task_id='tests-only',request='Add tests for existing application behavior.',scope_globs=['tests/**'])
        ledger.insert(conn,'claim',id='finding',task_id='tests-only',kind='review-finding',question='Is this behavior covered?',
                      subject_refs=[{'kind':'file','path':'app/x.py'}],checker='review-finding',origin=origin,
                      file='app/x.py',symbol='',note='The unchanged application has no failure-path test.',created_at='2026-09-10')
        output=io.StringIO()
        with contextlib.redirect_stdout(output):
            cli.cmd_derive(types.SimpleNamespace(repo=str(root),task='tests-only',phase='before'))
        return output.getvalue()

    def test_hand_raised_finding_is_not_reclassified_as_inherited_debt(self):
        output=self.output('review')
        self.assertNotIn('inherited debt',output)
        self.assertNotIn('--emit-baseline',output)

    def test_detector_findings_still_report_unchanged_subjects(self):
        self.assertIn('file(s) this task has not changed',self.output('derive'))
