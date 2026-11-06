"""Policy snapshots and bounded context also protect memory-only reviews."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import uw
import uw_maintenance as maintenance
import uw_policy as policy


class MaintenancePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uw-maintenance-policy-')
        self.root = Path(self.temp.name).resolve()
        self.store = uw.Store(self.root, create=True)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def configure(self, value):
        return policy.set_policy(self.store, value, policy.get_policy(self.store)['hash'])

    def put(self, key, body='Verified project fact.'):
        return self.store.put(key, body, ['fixture:' + key], ttl_days=0)

    def retire(self, key='a'):
        return {'action': 'retire', 'key': key, 'reason': 'Verified obsolete fact.'}

    def proposal(self):
        self.put('a')
        packet = maintenance.prepare(self.store)
        return maintenance.submit(self.store, packet, {'proposals': [self.retire()]})[0]['id']

    def test_memory_count_limit_keeps_sweep_complete(self):
        snapshot = self.configure({'limits': {'max_memories': 2}})
        for i in range(7): self.put('k' + str(i))
        visited = []
        while len(visited) < 7:
            packet = maintenance.prepare(self.store, limit=100)
            self.assertEqual(packet['review_policy'], snapshot)
            self.assertLessEqual(len(packet['memories']), 2)
            visited.extend(packet['scan_keys'])
            maintenance.submit(self.store, packet, {'proposals': []})
        self.assertEqual(visited, ['k' + str(i) for i in range(7)])

    def test_character_budget_stops_before_next_memory_without_skipping(self):
        self.configure({'limits': {'memory_char_budget': 1000}})
        for key in ('a', 'b', 'c'): self.put(key, key * 450)
        visited = []
        while len(visited) < 3:
            packet = maintenance.prepare(self.store, limit=100)
            self.assertLessEqual(sum(len(uw.canon(m)) for m in packet['memories']), 1000)
            self.assertEqual(len(packet['scan_keys']), 1)
            visited.extend(packet['scan_keys'])
            maintenance.submit(self.store, packet, {'proposals': []})
        self.assertEqual(visited, ['a', 'b', 'c'])

    def test_oversized_first_memory_does_not_advance_cursor(self):
        self.configure({'limits': {'memory_char_budget': 1000}})
        self.put('a', 'x' * 1500)
        self.put('b', 'Small later record.')
        with self.assertRaisesRegex(ValueError, 'First memory.*increase memory_char_budget'):
            maintenance.prepare(self.store)
        self.assertEqual(maintenance._state(self.store), maintenance.INITIAL)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM maintenance_reviews').fetchone()[0], 0)
        self.configure({'limits': {'memory_char_budget': 3000}})
        self.assertEqual(maintenance.prepare(self.store)['scan_keys'], ['a', 'b'])

    def test_related_candidates_share_memory_count_and_character_budgets(self):
        self.configure({'limits': {'max_memories': 1}})
        for key in ('a', 'b'): self.put(key, 'Same verified body.')
        first = maintenance.prepare(self.store, limit=1)
        maintenance.submit(self.store, first, {'proposals': []})
        second = maintenance.prepare(self.store, limit=1)
        self.assertEqual(second['scan_keys'], ['b'])
        self.assertEqual([m['key'] for m in second['memories']], ['b'])
        self.assertEqual(second['candidates'], [])
        maintenance.cancel(self.store, second['review_id'])
        self.configure({'limits': {'max_memories': 2}})
        again = maintenance.prepare(self.store, limit=1)
        self.assertEqual({m['key'] for m in again['memories']}, {'a', 'b'})
        self.assertEqual(again['candidates'][0]['type'], 'duplicate')

    def test_related_candidate_cannot_exceed_character_budget(self):
        self.configure({'limits': {'memory_char_budget': 1000}})
        for key in ('a', 'b'): self.put(key, 'x' * 450)
        first = maintenance.prepare(self.store, limit=1)
        maintenance.submit(self.store, first, {'proposals': []})
        second = maintenance.prepare(self.store, limit=1)
        self.assertEqual([m['key'] for m in second['memories']], ['b'])
        self.assertEqual(second['candidates'], [])
        self.assertLessEqual(sum(len(uw.canon(m)) for m in second['memories']), 1000)

    def test_changed_pending_policy_requires_cancel_and_reprepare(self):
        self.put('a')
        original = maintenance.prepare(self.store)
        self.configure({'focus': ['repeated failures']})
        with self.assertRaisesRegex(ValueError, 'maintenance-cancel --id ' + original['review_id']):
            maintenance.prepare(self.store)
        with self.assertRaisesRegex(ValueError, 'policy changed'):
            maintenance.submit(self.store, original, {'proposals': []})
        self.assertEqual(maintenance._state(self.store)['generation'], 0)
        maintenance.cancel(self.store, original['review_id'])
        replacement = maintenance.prepare(self.store)
        self.assertNotEqual(original['review_id'], replacement['review_id'])
        self.assertEqual(original['scan_keys'], replacement['scan_keys'])
        maintenance.submit(self.store, replacement, {'proposals': []})

    def test_proposal_limit_rejection_is_atomic_and_zero_can_finish(self):
        self.configure({'limits': {'max_proposals': 1}})
        self.put('a'); self.put('b')
        packet = maintenance.prepare(self.store)
        with self.assertRaisesRegex(ValueError, 'proposal limit'):
            maintenance.submit(self.store, packet, {'proposals': [self.retire('a'), self.retire('b')]})
        self.assertEqual(self.store.status()['pending_proposals'], 0)
        self.assertEqual(maintenance._state(self.store)['generation'], 0)
        maintenance.cancel(self.store, packet['review_id'])
        self.configure({'limits': {'max_proposals': 0}})
        packet = maintenance.prepare(self.store)
        self.assertEqual(maintenance.submit(self.store, packet, {'proposals': []}), [])

    def test_policy_change_blocks_accept_but_not_reject(self):
        pid = self.proposal()
        self.configure({'exclude': ['retirement']})
        with self.assertRaisesRegex(ValueError, 'policy changed'):
            maintenance.decide(self.store, pid, accept=True)
        self.assertFalse(self.store.current('a')['payload'].get('retired', False))
        self.assertEqual(maintenance.decide(self.store, pid, accept=False)['decision'], 'rejected')

    def test_policy_change_blocks_evaluator_launch(self):
        pid = self.proposal()
        self.configure({'exclude': ['retirement']})
        with patch.object(maintenance, 'run_process') as worker:
            with self.assertRaisesRegex(ValueError, 'policy changed'):
                maintenance.evaluate(self.store, pid, self.root / 'unused.json')
            worker.assert_not_called()

    def test_policy_change_during_evaluation_blocks_receipt(self):
        pid = self.proposal()
        cases = self.root / 'cases.json'
        cases.write_text(json.dumps([{'id': str(i)} for i in range(3)]), encoding='utf-8')
        self.store.config['evaluator'] = [sys.executable, '-c', 'pass']
        def worker(*args):
            other = uw.Store(self.root)
            try:
                policy.set_policy(other, {'focus': ['new priorities']}, policy.get_policy(other)['hash'])
            finally:
                other.close()
            return json.dumps({'cases': [{'id': str(i), 'baseline_pass': False, 'candidate_pass': True,
                                         'fixed_constraints_pass': True} for i in range(3)]})
        with patch.object(maintenance, 'run_process', side_effect=worker):
            with self.assertRaisesRegex(ValueError, 'policy changed'):
                maintenance.evaluate(self.store, pid, cases)
        self.assertIsNone(self.store.proposal(pid)['receipt'])

    def test_maintenance_worker_receives_subordinate_policy_context(self):
        self.configure({'focus': ['duplicate build facts'], 'exclude': ['cosmetic preferences']})
        self.put('a')
        prompts = []
        def worker(command, prompt, cwd, timeout):
            prompts.append(prompt)
            return '{"proposals":[]}'
        with patch.object(maintenance.shutil, 'which', return_value='/fake/claude'), patch.object(maintenance, 'run_process', side_effect=worker):
            result = maintenance.dream_run(self.store, 'claude')
        self.assertFalse(result['promoted'])
        self.assertIn('duplicate build facts', prompts[0])
        self.assertIn('cosmetic preferences', prompts[0])
        self.assertIn('subordinate to fixed user goals', prompts[0])
        self.assertIn('Do not change user goals or permissions', prompts[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)
