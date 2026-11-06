"""Memory-only sweep, proposal, and independent-evaluation regressions."""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import uw
import uw_maintenance as maintenance


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uw-maintenance-test-')
        self.root = Path(self.temp.name).resolve()
        self.store = uw.Store(self.root, create=True)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def put(self, key, body=None, evidence=None):
        return self.store.put(key, body or 'Verified value for ' + key, evidence or ['fixture:' + key], ttl_days=0)

    def proposal(self, candidate):
        packet = maintenance.prepare(self.store)
        return maintenance.submit(self.store, packet, {'proposals': [candidate]})[0]['id']

    def merge(self, key='a', sources=None, kind='lesson'):
        return {'action':'merge', 'key':key, 'source_keys':sources or ['a','b'],
                'kind':kind, 'body':'Consolidated verified fact.', 'reason':'Both records document the same fact.'}

    def evaluator(self):
        script = self.root/'evaluate.py'
        script.write_text('import json,sys\np=json.load(sys.stdin)\n'
            'assert isinstance(p["baseline"],dict) and isinstance(p["candidate"],dict)\n'
            'assert any(m.get("retired") for m in p["candidate"].values())\n'
            'print(json.dumps({"cases":[{"id":c["id"],"baseline_pass":False,"candidate_pass":True,"fixed_constraints_pass":True} for c in p["cases"]]}))\n',encoding='utf-8')
        cases = self.root/'cases.json'
        cases.write_text(json.dumps([{'id':'heldout-' + str(n)} for n in range(3)]),encoding='utf-8')
        self.store.config['evaluator'] = [sys.executable, str(script)]
        return cases

    def workflow(self, key):
        # Fixture for an already accepted workflow; production creation is gated.
        self.store.transaction(lambda: self.store._put(key, {'kind':'workflow','body':'Run the verified workflow.',
            'evidence':['fixture:approved'], 'expires_at':None}, 'NEW', 'fixture'))

    def test_memory_only_review_and_empty_store(self):
        self.assertFalse(maintenance.prepare(self.store)['due'])
        self.put('older')
        self.assertFalse(self.store.prepare(force=True)['due'])
        packet = maintenance.prepare(self.store)
        self.assertEqual(packet['events'], [])
        self.assertEqual(packet['scan_keys'], ['older'])
        maintenance.submit(self.store, packet, {'proposals':[]})
        self.assertEqual(self.store.status()['events'], 0)

    def test_bounded_sweep_visits_older_expired_and_newly_inserted_keys(self):
        for n in range(125): self.put('k' + str(n).zfill(3))
        old = self.store.current('k000')
        self.store.expire('k000',old['hash'])
        visited = []
        while len(visited) < 125:
            packet = maintenance.prepare(self.store, limit=7)
            self.assertLessEqual(len(packet['scan_keys']),7)
            visited.extend(packet['scan_keys'])
            maintenance.submit(self.store,packet,{'proposals':[]})
            if len(visited)==7: self.put('aaa-late')
        self.assertEqual(visited,['k' + str(n).zfill(3) for n in range(125)])
        following = maintenance.prepare(self.store,limit=7)
        self.assertEqual(following['cursor_after']['sweep'],2)
        self.assertIn('aaa-late',following['scan_keys'])

    def test_concurrent_prepare_reuses_pending_packet_and_submit_once(self):
        self.put('a')
        def prepare(_):
            other=uw.Store(self.root)
            try: return maintenance.prepare(other)
            finally: other.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            packets=list(pool.map(prepare,range(4)))
        self.assertTrue(all(packet==packets[0] for packet in packets))
        maintenance.submit(self.store,packets[0],{'proposals':[]})
        with self.assertRaises(ValueError): maintenance.submit(self.store,packets[1],{'proposals':[]})
        self.assertEqual(maintenance._state(self.store)['generation'],1)

    def test_cancel_and_failed_worker_do_not_skip_coverage(self):
        self.put('a'); self.put('b')
        packet=maintenance.prepare(self.store,limit=1)
        with patch.object(maintenance.shutil,'which',return_value='/fixture/claude'), patch.object(maintenance,'run_process',side_effect=ValueError('fixture timeout')):
            with self.assertRaisesRegex(ValueError,'timeout'): maintenance.dream_run(self.store,'claude',limit=1)
        self.assertEqual(maintenance.prepare(self.store,limit=1),packet)
        self.assertEqual(maintenance._state(self.store)['generation'],0)
        maintenance.cancel(self.store,packet['review_id'])
        refreshed=maintenance.prepare(self.store,limit=1)
        self.assertNotEqual(refreshed['review_id'],packet['review_id'])
        self.assertEqual(refreshed['scan_keys'],packet['scan_keys'])

    def test_cross_batch_duplicate_and_conflict_candidates(self):
        self.put('a','Use the verified compiler.',['manual:compiler'])
        self.put('b',' use  the VERIFIED compiler. ',['manual:other'])
        self.put('c','Use a different compiler.',['manual:compiler'])
        first=maintenance.prepare(self.store,limit=1)
        maintenance.submit(self.store,first,{'proposals':[]})
        second=maintenance.prepare(self.store,limit=1)
        self.assertIn({'type':'duplicate','keys':['a','b'],'basis':'normalized body equality'},second['candidates'])
        self.assertEqual({m['key'] for m in second['memories']},{'a','b'})
        maintenance.submit(self.store,second,{'proposals':[]})
        third=maintenance.prepare(self.store,limit=1)
        self.assertTrue(any(c['type']=='possible_conflict' and c['keys']==['a','c'] for c in third['candidates']))

    def test_retirement_is_versioned_and_restore_uses_hash_guard(self):
        original=self.put('a')
        pid=self.proposal({'action':'retire','key':'a','reason':'Obsolete fact, verified manually.'})
        self.assertEqual(len(self.store.recall()),1)
        self.store.decide(pid,True)
        retired=self.store.current('a')
        self.assertTrue(retired['payload']['retired'])
        self.assertEqual(self.store.recall(include_expired=True),[])
        self.assertEqual(len(self.store.history('a')),2)
        with self.assertRaises(ValueError): self.store.rollback('a',1,original['hash'])
        self.store.rollback('a',1,retired['hash'])
        self.assertEqual(self.store.recall()[0]['key'],'a')

    def test_merge_preserves_history_and_retires_sources(self):
        self.put('a'); self.put('b')
        pid=self.proposal(self.merge())
        result=self.store.decide(pid,True)
        self.assertEqual(len(result['revisions']),2)
        self.assertEqual(self.store.current('a')['payload']['body'],'Consolidated verified fact.')
        self.assertEqual(self.store.current('b')['payload']['merged_into'],'a')
        self.assertEqual([m['key'] for m in self.store.recall()],['a'])
        self.assertEqual(len(self.store.history('a')),2)
        self.assertEqual(len(self.store.history('b')),2)

    def test_new_target_merge_rolls_back_every_write_on_failure(self):
        self.put('a'); self.put('b')
        before={k:self.store.current(k) for k in ('a','b')}
        pid=self.proposal(self.merge(key='combined'))
        self.store.db.execute("CREATE TRIGGER fail_merge BEFORE INSERT ON history WHEN NEW.key='combined' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        self.store.db.commit()
        with self.assertRaises(Exception): self.store.decide(pid,True)
        self.assertEqual({k:self.store.current(k) for k in ('a','b')},before)
        self.assertIsNone(self.store.current('combined'))
        self.assertEqual(self.store.proposal(pid)['status'],'pending')

    def test_changed_sources_and_new_targets_reject_stale_acceptance(self):
        self.put('a'); self.put('b')
        pid=self.proposal(self.merge(key='combined'))
        self.put('combined','A concurrent owner supplied this fact.')
        with self.assertRaisesRegex(ValueError,'target changed'): self.store.decide(pid,True)
        self.assertFalse(self.store.current('a')['payload'].get('retired',False))
        self.store.decide(pid,False)
        pid=self.proposal(self.merge())
        old=self.store.current('b')
        self.store.put('b','A newer corrected source.',['fixture:update'],expected=old['hash'])
        with self.assertRaisesRegex(ValueError,'source changed'): self.store.decide(pid,True)
        self.assertEqual(self.store.proposal(pid)['status'],'pending')

    def test_invalid_or_tampered_submission_is_atomic_and_does_not_advance(self):
        self.put('a'); self.put('b')
        packet=maintenance.prepare(self.store)
        bad={'action':'delete','key':'b','reason':'Unsupported action'}
        with self.assertRaises(ValueError): maintenance.submit(self.store,packet,{'proposals':[{'action':'retire','key':'a','reason':'Obsolete'},bad]})
        self.assertEqual(self.store.status()['pending_proposals'],0)
        self.assertEqual(maintenance._state(self.store)['generation'],0)
        tampered=json.loads(json.dumps(packet)); tampered['memories'][0]['payload']['body']='Tampered'
        with self.assertRaises(ValueError): maintenance.submit(self.store,tampered,{'proposals':[]})
        self.assertEqual(maintenance.prepare(self.store),packet)

    def test_invalid_foreign_missing_and_overlapping_sources_are_rejected(self):
        self.put('a'); self.put('b')
        packet=maintenance.prepare(self.store)
        wrong=dict(packet,project_id='foreign')
        with self.assertRaises(ValueError): maintenance.submit(self.store,wrong,{'proposals':[]})
        for candidates in ([self.merge(sources=['a','missing'])],
                           [self.merge(),{'action':'retire','key':'b','reason':'Double modification'}],
                           [{'action':'retire','key':'a','reason':'Obsolete','body':'Unexpected change'}]):
            with self.assertRaises(ValueError): maintenance.submit(self.store,packet,{'proposals':candidates})
        self.assertEqual(self.store.status()['pending_proposals'],0)

    def test_workflow_merge_cannot_bypass_full_state_evaluation(self):
        self.workflow('a'); self.put('b')
        pid=self.proposal(self.merge(kind='fact'))
        with self.assertRaisesRegex(ValueError,'evaluator receipt'): self.store.decide(pid,True)
        cases=self.evaluator()
        receipt=self.store.evaluate(pid,cases)
        self.assertTrue(receipt['passed'])
        self.store.decide(pid,True)
        self.assertEqual(self.store.current('a')['payload']['kind'],'fact')

    def test_workflow_retirement_requires_evaluation_and_rechecks_sources(self):
        self.workflow('a')
        pid=self.proposal({'action':'retire','key':'a','reason':'Superseded workflow.'})
        with self.assertRaises(ValueError): self.store.decide(pid,True)
        self.store.evaluate(pid,self.evaluator())
        old=self.store.current('a')
        self.store.expire('a',old['hash'])
        with self.assertRaisesRegex(ValueError,'source changed'): self.store.decide(pid,True)

    def test_evaluator_cannot_save_receipt_after_source_changes(self):
        self.workflow('a')
        pid=self.proposal({'action':'retire','key':'a','reason':'Superseded workflow.'})
        cases=self.evaluator()
        original=maintenance.run_process
        def change_then_evaluate(*args,**kwargs):
            other=uw.Store(self.root)
            try:
                item=other.current('a')
                other.expire('a',item['hash'])
            finally: other.close()
            return original(*args,**kwargs)
        with patch.object(maintenance,'run_process',side_effect=change_then_evaluate):
            with self.assertRaisesRegex(ValueError,'source changed'): self.store.evaluate(pid,cases)
        self.assertIsNone(self.store.proposal(pid)['receipt'])

    def test_event_watermark_and_export_are_independent(self):
        self.put('a')
        self.store.record({'id':'event-1','session':'s','status':'observed','summary':'Observed build outcome.','evidence':[]})
        packet=maintenance.prepare(self.store)
        maintenance.submit(self.store,packet,{'proposals':[]})
        event_packet=self.store.prepare(force=True)
        self.assertEqual(event_packet['events'][0]['id'],'event-1')
        dest=self.root/'export'
        self.store.export(dest)
        tables=json.loads((dest/'snapshot.json').read_text(encoding='utf-8'))['tables']
        self.assertTrue(set(maintenance.TABLES)<=set(tables))
        self.assertEqual(len(tables['maintenance_state']),1)

    def test_cli_prepare_submit_and_cancel(self):
        self.put('a')
        command=[sys.executable,str(Path(__file__).with_name('uw.py')),'--project',str(self.root)]
        packet_file=self.root/'packet.json'
        response=subprocess.run(command+['maintenance-prepare','--out',str(packet_file)],capture_output=True,text=True)
        self.assertEqual(response.returncode,0,response.stderr)
        packet=json.loads(packet_file.read_text(encoding='utf-8'))
        cancelled=subprocess.run(command+['maintenance-cancel','--id',packet['review_id']],capture_output=True,text=True)
        self.assertEqual(cancelled.returncode,0,cancelled.stderr)
        response=subprocess.run(command+['maintenance-prepare','--out',str(packet_file)],capture_output=True,text=True)
        self.assertEqual(response.returncode,0,response.stderr)
        response_file=self.root/'response.json'; response_file.write_text('{"proposals":[]}',encoding='utf-8')
        submitted=subprocess.run(command+['maintenance-submit','--packet',str(packet_file),'--response',str(response_file)],capture_output=True,text=True)
        self.assertEqual(submitted.returncode,0,submitted.stderr)

    def test_large_unicode_batches_remain_bounded_without_skipping(self):
        for n in range(12): self.put('large-' + str(n).zfill(2),'\U0001f600'*8000)
        visited=[]
        while len(visited)<12:
            packet=maintenance.prepare(self.store,limit=100)
            self.assertLessEqual(len(uw.canon(packet).encode('utf-8')),500_000)
            visited.extend(packet['scan_keys'])
            maintenance.submit(self.store,packet,{'proposals':[]})
        self.assertEqual(len(set(visited)),12)


if __name__=='__main__':
    unittest.main(verbosity=2)
