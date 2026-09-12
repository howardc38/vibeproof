"""An anchor identifies one real prefix; later legitimate rows may follow it."""
import json
import unittest
from datetime import datetime, timezone
from kernel import ledger
from tests import test_maintenance as fixture_module

class AnchorCoordinates(unittest.TestCase):
    def setUp(self):
        f=fixture_module.Maintenance();f.setUp();self.addCleanup(f.doCleanups);self.root=f.root
        self.c=ledger.connect(self.root);self.addCleanup(self.c.close)
        self.when=datetime.now(timezone.utc).isoformat()
        with ledger.writing(self.c):
            ledger.insert(self.c,'task',id='t',request='audit fixture',scope_globs=['**'],base_commit='',created_at=self.when)
            ledger.insert(self.c,'claim',id='c',task_id='t',kind='test',checker='test',question='q',origin='derive',subject_refs=[],created_at=self.when)
    def append(self):
        ledger.append_attempt(self.c,claim_id='c',subject_digest='{}',checker_sha='s',config_sha='c',head_commit='h',worktree=str(self.root),argv='[]',exit_code=1,stdout='audit fixture',stderr='',started_at=self.when,ended_at=self.when,duration_ms=1)
        with ledger.writing(self.c):ledger.insert(self.c,'event',kind='hook_seen',actor='hook',payload={'fixture':True},created_at=self.when)
    def test_counts_and_ids_cannot_contradict_the_recorded_prefix(self):
        for _ in range(3):self.append()
        ledger.write_chain_head(self.c,self.root);p=ledger.chain_head_path(self.root);original=json.loads(p.read_text())
        self.assertTrue(ledger.audit_chain(self.c,self.root)[0])
        for key,value in [('attempts',1),('last_id',1),('last_id',99),('events',1),('last_event_id',1),('last_event_id',99),('attempts',True),('events',-1)]:
            with self.subTest(key=key,value=value):
                data=dict(original);data[key]=value;p.write_text(json.dumps(data))
                ok,problems=ledger.audit_chain(self.c,self.root)
                self.assertFalse(ok);self.assertTrue(problems)
        p.write_text(json.dumps(original))
        self.append()
        self.assertTrue(ledger.audit_chain(self.c,self.root)[0],ledger.audit_chain(self.c,self.root))
    def test_zero_anchor_and_malformed_shape_have_explicit_answers(self):
        ledger.write_chain_head(self.c,self.root);p=ledger.chain_head_path(self.root)
        self.assertTrue(ledger.audit_chain(self.c,self.root)[0])
        original=json.loads(p.read_text())
        for changed in [[],{'attempts':0},{**original,'head_hash':None},{**original,'head_hash':'bad'},{**original,'last_id':-1}]:
            with self.subTest(changed=changed):
                p.write_text(json.dumps(changed));ok,problems=ledger.audit_chain(self.c,self.root)
                self.assertFalse(ok);self.assertTrue(problems)

if __name__=='__main__':unittest.main()
