#!/usr/bin/env python3
"""Deterministic review-policy, transport, and concurrency regression tests."""
import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import uw
import uw_policy


class ReviewPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uw-policy-')
        self.project = Path(self.temp.name).resolve()
        self.store = uw.Store(self.project, create=True)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def configure(self, value):
        return uw_policy.set_policy(self.store, value, uw_policy.get_policy(self.store)['hash'])

    def event(self, number=1, summary='Repeated build failure.'):
        return self.store.record({'id': f'e{number}', 'session': f's{number}', 'status': 'failure',
                                  'summary': summary, 'evidence': [f'fixture:{number}']})

    def candidate(self, key='build', kind='lesson'):
        return {'key': key, 'kind': kind, 'body': 'Use the verified build command.',
                'reason': 'The supplied event records the failure.', 'ttl_days': 30,
                'evidence_event_ids': ['e1']}

    def proposal(self, kind='lesson'):
        self.event()
        packet = self.store.prepare(force=True)
        result = self.store.submit(packet, {'proposals': [self.candidate(kind=kind)]})
        return result[0]['id']

    def test_default_policy_is_read_only_and_preserves_schema_one(self):
        first = uw_policy.get_policy(self.store)
        self.assertEqual(first['revision'], 0)
        self.assertEqual(first['policy']['limits'], uw_policy.DEFAULT_LIMITS)
        self.assertEqual(self.store.db.execute('PRAGMA user_version').fetchone()[0], 1)
        self.assertIsNone(self.store.db.execute('SELECT value FROM metadata WHERE key=?', (uw_policy.KEY,)).fetchone())
        self.assertEqual(self.configure({}), first)

    def test_topic_guidance_never_filters_evidence_or_rewrites_task(self):
        policy = self.configure({'include': ['build reliability'], 'focus': ['repeated failures'],
                                 'exclude': ['visual styling'], 'limits': {'max_memories': 1}})
        self.store.put('first', 'First observation.', ['fixture:first'])
        self.store.put('second', 'Second observation.', ['fixture:second'])
        self.event(1, 'Visual styling failed.')
        self.event(2, 'Build failed.')
        packet = self.store.prepare(force=True)
        self.assertEqual(packet['review_policy'], policy)
        self.assertEqual(len(packet['memories']), 1)
        self.assertEqual([e['id'] for e in packet['events']], ['e1', 'e2'])
        task = 'Build the page. Preserve the approved colors and never deploy.'
        self.assertIn(task, uw.assemble(self.store, task, 'web'))
        self.assertNotIn('visual styling', uw.assemble(self.store, task, 'web'))
        self.store.submit(packet, {'proposals': []})
        self.assertFalse(self.store.prepare(force=True)['due'])

    def test_external_worker_receives_subordinate_policy_context(self):
        self.configure({'focus': ['missing restart state'], 'exclude': ['cosmetic preferences']})
        self.event()
        captured = []
        def worker(command, prompt, cwd, timeout):
            captured.append((command, prompt))
            return '{"proposals": []}'
        with patch.object(uw.shutil, 'which', return_value='/fake/claude'), patch.object(uw, 'run_process', side_effect=worker):
            result = uw.dream_run(self.store, 'claude', force=True)
        self.assertFalse(result['promoted'])
        command, prompt = captured[0]
        self.assertEqual(command[command.index('--tools') + 1], '')
        self.assertIn('missing restart state', prompt)
        self.assertIn('cosmetic preferences', prompt)
        self.assertIn('subordinate to fixed user goals', prompt)
        self.assertIn('or suppress security/constraint violations', prompt)
        self.assertIn('never modify owner policy, permissions', prompt)

    def test_invalid_and_secret_policy_fields_are_rejected_without_writes(self):
        invalid = [[], {'permissions': ['admin']}, {'schema': True}, {'focus': 'build'},
                   {'focus': ['x'] * 13}, {'focus': ['same', 'Same']}, {'exclude': ['a'], 'include': ['A']},
                   {'focus': ['line\nbreak']}, {'focus': ['password=notarealcredential123']},
                   {'limits': {'max_proposals': 11}}, {'limits': {'max_memories': True}},
                   {'limits': {'memory_char_budget': 999}}, {'limits': {'tool_access': 1}},
                   {'focus': [str(i) + '界' * 150 for i in range(12)],
                    'include': [str(i) + '海' * 150 for i in range(12)]}]
        baseline = uw_policy.get_policy(self.store)
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    uw_policy.set_policy(self.store, value, baseline['hash'])
                self.assertEqual(uw_policy.get_policy(self.store), baseline)

    def test_simultaneous_policy_updates_require_matching_hash(self):
        baseline = uw_policy.get_policy(self.store)
        barrier = threading.Barrier(2)
        def update(number):
            other = uw.Store(self.project)
            try:
                barrier.wait(timeout=5)
                try:
                    uw_policy.set_policy(other, {'focus': [f'priority {number}']}, baseline['hash'])
                    return 'success'
                except ValueError:
                    return 'conflict'
            finally:
                other.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(update, [1, 2]))
        self.assertCountEqual(results, ['success', 'conflict'])
        self.assertEqual(uw_policy.get_policy(self.store)['revision'], 1)

    def test_changed_and_reverted_policy_invalidates_prepared_review(self):
        self.event()
        packet = self.store.prepare(force=True)
        self.configure({'focus': ['build reliability']})
        self.configure({})
        with self.assertRaisesRegex(ValueError, 'policy changed'):
            self.store.submit(packet, {'proposals': [self.candidate()]})
        self.assertEqual(self.store.status()['pending_proposals'], 0)
        fresh = self.store.prepare(force=True)
        self.assertEqual([e['id'] for e in fresh['events']], ['e1'])
        self.assertEqual(self.store.submit(fresh, {'proposals': []}), [])

    def test_same_policy_reapply_does_not_invalidate_review(self):
        current = self.configure({'focus': ['build reliability']})
        self.event()
        packet = self.store.prepare(force=True)
        self.assertEqual(self.configure(current['policy']), current)
        self.store.submit(packet, {'proposals': []})

    def test_tampered_policy_snapshot_is_rejected(self):
        self.event()
        packet = self.store.prepare(force=True)
        packet['review_policy']['policy']['focus'] = ['unauthorized replacement']
        with self.assertRaisesRegex(ValueError, 'packet changed'):
            self.store.submit(packet, {'proposals': []})
        self.assertEqual(uw_policy.get_policy(self.store)['policy']['focus'], [])

    def test_proposal_limit_rejects_whole_response_atomically(self):
        self.configure({'limits': {'max_proposals': 1}})
        self.event()
        packet = self.store.prepare(force=True)
        with self.assertRaisesRegex(ValueError, 'proposal limit'):
            self.store.submit(packet, {'proposals': [self.candidate('first'), self.candidate('second')]})
        self.assertEqual(self.store.status()['pending_proposals'], 0)
        self.store.submit(packet, {'proposals': [self.candidate()]})

    def test_zero_proposals_can_complete_review_without_promotion(self):
        self.configure({'limits': {'max_proposals': 0}})
        self.event()
        packet = self.store.prepare(force=True)
        with self.assertRaisesRegex(ValueError, 'proposal limit'):
            self.store.submit(packet, {'proposals': [self.candidate()]})
        self.assertEqual(self.store.submit(packet, {'proposals': []}), [])

    def test_policy_change_blocks_pending_accept_but_allows_reject(self):
        pid = self.proposal()
        self.configure({'exclude': ['build changes']})
        with self.assertRaisesRegex(ValueError, 'policy changed'):
            self.store.decide(pid, accept=True)
        self.assertIsNone(self.store.current('build'))
        self.assertEqual(self.store.decide(pid, accept=False)['decision'], 'rejected')

    def test_policy_change_blocks_evaluator_before_process_launch(self):
        pid = self.proposal('workflow')
        self.configure({'exclude': ['build changes']})
        with patch.object(uw, 'run_process') as worker:
            with self.assertRaisesRegex(ValueError, 'policy changed'):
                self.store.evaluate(pid, self.project / 'not-read.json')
            worker.assert_not_called()

    def test_policy_change_during_evaluation_cannot_save_receipt(self):
        pid = self.proposal('workflow')
        cases = self.project / 'cases.json'
        cases.write_text(json.dumps([{'id': str(i)} for i in range(3)]), encoding='utf-8')
        self.store.config['evaluator'] = [sys.executable, '-c', 'pass']
        def worker(*args):
            other = uw.Store(self.project)
            try:
                uw_policy.set_policy(other, {'focus': ['new priorities']}, uw_policy.get_policy(other)['hash'])
            finally:
                other.close()
            return json.dumps({'cases': [{'id': str(i), 'baseline_pass': False, 'candidate_pass': True,
                                         'fixed_constraints_pass': True} for i in range(3)]})
        with patch.object(uw, 'run_process', side_effect=worker):
            with self.assertRaisesRegex(ValueError, 'policy changed'):
                self.store.evaluate(pid, cases)
        self.assertIsNone(self.store.proposal(pid)['receipt'])

    def test_corrupt_stored_policy_fails_closed(self):
        for value in ('[]', '{"revision":1,"policy":{"permissions":["admin"]}}', '{"revision":true,"policy":{}}'):
            self.store.db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', (uw_policy.KEY, value))
            self.store.db.commit()
            with self.assertRaises(ValueError):
                self.store.prepare(force=True)

    def test_legacy_packet_only_accepted_before_first_policy_change(self):
        self.event()
        packet = self.store.prepare(force=True)
        del packet['review_policy']
        self.store.db.execute('UPDATE reviews SET packet=? WHERE id=?', (uw.canon(packet), packet['review_id']))
        self.store.db.commit()
        self.configure({'focus': ['new priorities']})
        with self.assertRaisesRegex(ValueError, 'policy changed'):
            self.store.submit(packet, {'proposals': []})

    def test_legacy_packet_with_unchanged_default_policy_still_submits(self):
        self.event()
        packet = self.store.prepare(force=True)
        del packet['review_policy']
        self.store.db.execute('UPDATE reviews SET packet=? WHERE id=?', (uw.canon(packet), packet['review_id']))
        self.store.db.commit()
        self.assertEqual(self.store.submit(packet, {'proposals': []}), [])

    def test_cli_show_and_compare_and_swap_update(self):
        script = Path(__file__).with_name('uw.py')
        command = [sys.executable, str(script), '--project', str(self.project), 'review-policy']
        first = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(first.returncode, 0, first.stderr)
        snapshot = json.loads(first.stdout)
        file = self.project / 'priorities.json'
        file.write_text(json.dumps({'focus': ['repeated build failures']}), encoding='utf-8')
        changed = subprocess.run(command + ['--file', str(file), '--expected-hash', snapshot['hash']],
                                 capture_output=True, text=True, timeout=10)
        self.assertEqual(changed.returncode, 0, changed.stderr)
        self.assertEqual(json.loads(changed.stdout)['revision'], 1)
        stale = subprocess.run(command + ['--file', str(file), '--expected-hash', snapshot['hash']],
                               capture_output=True, text=True, timeout=10)
        self.assertEqual(stale.returncode, 2)
        self.assertIn('conflict', json.loads(stale.stderr)['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
