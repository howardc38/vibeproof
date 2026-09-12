"""Legacy redactions earn a public-byte commitment, never a secret preimage."""
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from kernel import ledger as L

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'api_key=' + 'K7' + 'x'*18 + '9Q'  # generated, never issued


class LegacyProjection(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        (self.root/'.v4').mkdir()
        self.conn = L.connect(self.root)
        self.addCleanup(self.conn.close)
        L.insert(self.conn, 'task', id='t', request='fixture', scope_globs=['**'],
                 base_commit='', created_at='2026')
        # Reproduce the old writer only in this disposable ledger. Production
        # inserts now redact before hashing and cannot create this old shape.
        for row in list(self.conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='event'")):
            self.conn.execute(f'DROP TRIGGER "{row[0]}"')
        row = {'task_id':'t','claim_id':None,'kind':'engagement','actor':'worker',
               'payload':json.dumps({'sentence':'a synthetic connection '+SECRET}),
               'created_at':'2026','prev_hash':L.GENESIS,'scheme':L.EVENT_SCHEME}
        row['row_hash'] = L._event_hash(L.GENESIS, row)
        self.conn.execute('INSERT INTO event ('+','.join(row)+') VALUES ('+','.join('?' for _ in row)+')', list(row.values()))
        self.conn.commit()
        self.out = self.root/'.v4/ledger_export.jsonl'
        self.original = [dict(r) for r in self.conn.execute('SELECT * FROM event')]

    def export(self, **kwargs):
        L.export_jsonl(self.conn, self.out, self.root, **kwargs)
        return L.projection_path(self.out)

    def test_old_rows_verify_as_projections_without_disclosing_or_rewriting_them(self):
        proof = self.export()
        details = {}
        self.assertEqual(L.verify_exported(self.out, details=details), (1, []))
        self.assertEqual(len(details['redacted_projection_events']), 1)
        self.assertEqual(details['original_hashes_rederived'], 0)
        self.assertEqual([dict(r) for r in self.conn.execute('SELECT * FROM event')], self.original)
        self.assertNotIn(SECRET, self.out.read_text()+proof.read_text())
        self.assertIn('[redacted]', self.out.read_text())
        # No database at the declared repo: the auditor only needs the export.
        result = subprocess.run([sys.executable, '-m', 'kernel.cli', '--repo', str(self.root/'absent'),
                                 'audit', '--events', str(self.out)], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        self.assertIn('redacted projections', result.stdout)
        self.assertIn('cannot be re-derived', result.stdout)
        self.assertNotIn('chain intact', result.stdout)

    def test_sealed_unmarked_history_stays_byte_identical_and_future_rows_verify(self):
        self.export().unlink()  # before sidecar support
        rows=[json.loads(x) for x in self.out.read_text().splitlines()]
        for row in rows: row.pop('_redacted', None)  # before that marker existed
        self.out.write_text(''.join(json.dumps(r,sort_keys=True)+'\n' for r in rows))
        old_bytes=self.out.read_bytes()
        self.assertTrue(L.verify_exported(self.out)[1])
        L.insert(self.conn,'event',task_id='t',kind='engagement',actor='worker',payload='new ordinary row',created_at='2027')
        self.export(seal_at=1)
        self.assertEqual(L.segment_files(self.out)[0].read_bytes(),old_bytes)
        details={}
        self.assertEqual(L.verify_exported(self.out,details=details),(2,[]))
        self.assertEqual(details['original_hashes_rederived'],1)
        self.assertEqual(details['redacted_projection_events'][0]['file'],'ledger_export.jsonl.0001')
        proof=L.projection_path(self.out).read_bytes()
        self.export()
        self.assertEqual(L.projection_path(self.out).read_bytes(),proof)

    def test_missing_stale_forged_or_extra_commitments_never_turn_green(self):
        proof=self.export(); original=proof.read_text(); data=json.loads(original)
        proof.unlink()
        self.assertTrue(L.verify_exported(self.out)[1])
        mutations=[lambda d:d.update(scheme='unknown'),
                   lambda d:d['files'][0].update(sha256='0'*64),
                   lambda d:d['events'][0].update(projection_hash='0'*64),
                   lambda d:d['events'][0].update(source_hash='0'*64),
                   lambda d:d['events'][0].update(id=True),
                   lambda d:d['events'][0].update(id=900),
                   lambda d:d['events'].append(dict(d['events'][0])),
                   lambda d:d['files'].append(dict(d['files'][0]))]
        for mutation in mutations:
            changed=copy.deepcopy(data);mutation(changed);proof.write_text(json.dumps(changed))
            self.assertTrue(L.verify_exported(self.out)[1])
        proof.write_text(original)
        self.out.write_text(self.out.read_text().replace('synthetic connection','EDITED connection'))
        self.assertTrue(L.verify_exported(self.out)[1])
        with self.assertRaises(L.ExportProjectionError):
            L._write_export_projection(self.conn,self.out,self.root)

    def test_deleting_the_legacy_event_and_proof_is_not_a_clean_empty_chain(self):
        self.export().unlink()
        rows=[json.loads(line) for line in self.out.read_text().splitlines()]
        self.out.write_text(''.join(json.dumps(r)+'\n' for r in rows if r.get('_table')!='event'))
        self.assertTrue(L.verify_exported(self.out)[1], 'the event anchor must retain the missing row')

    def test_removing_the_event_anchor_requires_reexport_not_a_downgrade(self):
        self.export()
        rows=[json.loads(line) for line in self.out.read_text().splitlines()]
        for row in rows:
            if row.get('_table')==L.ANCHOR_TABLE:
                row.pop('events');row.pop('event_head_hash')
        self.out.write_text(''.join(json.dumps(r)+'\n' for r in rows))
        L._write_export_projection(self.conn,self.out,self.root)
        self.assertTrue(any('no event anchor' in p for p in L.verify_exported(self.out)[1]))

    def test_a_bad_original_hash_is_not_certified(self):
        self.conn.execute("UPDATE event SET row_hash='bad' WHERE id=1")
        self.conn.commit()
        with self.assertRaisesRegex(L.ExportProjectionError,'original hash is invalid'):
            self.export()
        self.assertFalse(L.projection_path(self.out).exists())
        self.assertTrue(L.verify_exported(self.out)[1])

    def test_reexport_cannot_bless_a_missing_sealed_event(self):
        self.export()
        L.insert(self.conn,'event',task_id='t',kind='engagement',actor='worker',payload='later',created_at='2027')
        self.export(seal_at=1)
        sealed=L.segment_files(self.out)[0]
        rows=[json.loads(line) for line in sealed.read_text().splitlines()]
        sealed.write_text(''.join(json.dumps(r)+'\n' for r in rows if r.get('_table')!='event'))
        with self.assertRaisesRegex(L.ExportProjectionError,'sealed event count'):
            self.export()

    def test_a_missing_original_is_not_certified(self):
        self.export().unlink()
        self.conn.execute('DELETE FROM event');self.conn.commit()
        with self.assertRaisesRegex(L.ExportProjectionError,'no original row'):
            L._write_export_projection(self.conn,self.out,self.root)
        self.assertTrue(L.verify_exported(self.out)[1])

    def test_projection_cannot_waive_a_broken_predecessor_link(self):
        # A valid original row whose predecessor is absent from this export.
        row=dict(self.conn.execute('SELECT * FROM event').fetchone())
        row['prev_hash']='a'*64;row['row_hash']=L._event_hash(row['prev_hash'],row)
        self.conn.execute('UPDATE event SET prev_hash=?,row_hash=?',(row['prev_hash'],row['row_hash']))
        self.conn.commit()
        self.export()
        self.assertTrue(any('does not follow' in p for p in L.verify_exported(self.out)[1]))

    def test_file_change_during_the_walk_invalidates_the_projection(self):
        self.export()
        original_rows = L._rows_of
        def changing_rows(path):
            for index, row in enumerate(original_rows(path)):
                if index == 0:
                    self.out.write_text(self.out.read_text().replace('fixture', 'changed'))
                yield row
        with patch.object(L, '_rows_of', changing_rows):
            self.assertTrue(L.verify_exported(self.out)[1])

    def test_doctor_reports_legacy_projection_as_warn_not_original_chain_clean(self):
        from kernel import doctor
        self.export()
        folder=self.root/'.github/workflows';folder.mkdir(parents=True)
        (folder/'audit.yml').write_text('jobs:\n  audit:\n    steps:\n      - run: v4 audit --events .v4/ledger_export.jsonl\n')
        out=[];doctor._check_ci_can_actually_walk_the_chain(self.root,out)
        self.assertEqual(out[0]['status'],doctor.WARN)
        self.assertIn('projection',out[0]['detail'])
        self.assertNotIn('walks clean',out[0]['detail'])


if __name__ == '__main__':
    unittest.main()
