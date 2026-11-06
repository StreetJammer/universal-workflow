"""Offline parallel-review checks; subprocess fixtures never call a model."""
import concurrent.futures
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import uw
import uw_parallel


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uw-parallel-')
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.store = uw.Store(self.project, create=True)
        self.which = uw.shutil.which

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def events(self, sessions=('a', 'a', 'b', 'c')):
        for i, session in enumerate(sessions):
            self.store.record({'id': f'e{i}', 'session': session, 'status': 'failure',
                               'summary': 'A reproducible build failure.', 'evidence': [f'fixture:{i}']})

    def candidate(self, shard, key='build-fix', body='Run the verified build task.'):
        return {'key': key, 'kind': 'lesson', 'body': body, 'reason': 'Observed build failures.',
                'ttl_days': 90, 'evidence_event_ids': [e['id'] for e in shard['events']]}

    def run_review(self, reviewer, **kwargs):
        def which(name):
            return 'fixture-claude' if name == 'claude' else self.which(name)
        with patch.object(uw, 'review_packet', side_effect=reviewer), patch.object(uw.shutil, 'which', side_effect=which):
            return uw_parallel.run_parallel(self.store, engine='claude', force=True, **kwargs)

    def assert_unconsumed(self):
        self.assertEqual(self.store.status()['pending_proposals'], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM leases').fetchone()[0], 0)
        self.assertTrue(self.store.prepare(force=True).get('events'))

    def fixture_reviewer(self, mode):
        script = self.root / 'worker.py'
        ready = self.root / 'workers'
        ready.mkdir()
        script.write_text(
            'import json,sys,time\nfrom pathlib import Path\n'
            f'root=Path({str(ready)!r})\nmode={mode!r}\n'
            'packet=json.load(sys.stdin)\nindex=packet["parallel_shard"]["index"]\n'
            '(root/str(index)).write_text("started")\n'
            'deadline=time.monotonic()+5\n'
            'while len(list(root.iterdir()))<packet["parallel_shard"]["count"]:\n'
            ' if time.monotonic()>deadline: raise RuntimeError("reviewers did not run concurrently")\n'
            ' time.sleep(0.01)\n'
            'if mode=="timeout": time.sleep(20)\n'
            'if mode=="failure":\n'
            ' if index==0: raise SystemExit(7)\n'
            ' time.sleep(20)\n'
            'print(json.dumps({"proposals":[{"key":"build-fix","kind":"lesson",'
            '"body":"Run the verified build task.","reason":"Observed build failures.",'
            '"ttl_days":90,"evidence_event_ids":[e["id"] for e in packet["events"]]*2}]}))\n',
            encoding='utf-8')
        def reviewer(packet, engine, timeout, **options):
            output = uw.run_process([sys.executable, str(script)], uw.canon(packet), self.root, timeout, **options)
            return uw.parse_model_json(output)
        return reviewer

    def test_sessions_are_not_split_and_metadata_is_preserved(self):
        self.events()
        packet = self.store.prepare(force=True)
        packet['review_policy'] = {'policy': {'focus': ['build reliability']}, 'revision': 1, 'hash': 'fixture'}
        original = copy.deepcopy(packet)
        shards = uw_parallel.shard_packet(packet, 3)
        self.assertEqual(shards, uw_parallel.shard_packet(packet, 3))
        self.assertEqual(packet, original)
        observed = {}
        for index, shard in enumerate(shards):
            self.assertEqual(shard['review_policy'], packet['review_policy'])
            for event in shard['events']:
                self.assertEqual(observed.setdefault(event['session'], index), index)
        self.assertEqual(sum(len(s['events']) for s in shards), len(packet['events']))
        self.assertEqual(set(observed), {'a', 'b', 'c'})

    def test_subprocess_reviewers_run_concurrently_and_merge_exact_agreement(self):
        self.events()
        main_thread = threading.get_ident()
        original_submit = self.store.submit
        def submit(*args):
            self.assertEqual(threading.get_ident(), main_thread)
            return original_submit(*args)
        with patch.object(self.store, 'submit', side_effect=submit):
            result = self.run_review(self.fixture_reviewer('success'), workers=2, timeout=8)
        self.assertTrue(result['submitted'])
        self.assertFalse(result['promoted'])
        self.assertEqual(result['statistics']['total_calls'], 2)
        self.assertEqual(len(result['proposals']), 1)
        item = result['proposals'][0]
        self.assertEqual(item['evidence_event_ids'], ['e0', 'e1', 'e2', 'e3'])
        self.assertEqual(item['matched_sessions'], 3)
        self.assertEqual(item['total_sessions'], 3)
        self.assertFalse(self.store.prepare(force=True)['due'])
        self.assertEqual(self.store.status()['memories'], 0)

    def test_foreign_shard_evidence_is_rejected_even_if_in_full_packet(self):
        self.events(('a', 'b'))
        def reviewer(shard, *args, **kwargs):
            item = self.candidate(shard)
            item['evidence_event_ids'] = ['e1' if shard['events'][0]['id'] == 'e0' else 'e0']
            return {'proposals': [item]}
        with self.assertRaisesRegex(ValueError, 'own shard'):
            self.run_review(reviewer)
        self.assert_unconsumed()

    def test_retry_budget_is_counted_without_duplicate_evidence(self):
        self.events(('a', 'b'))
        attempts = {}
        lock = threading.Lock()
        def reviewer(shard, *args, **kwargs):
            index = shard['parallel_shard']['index']
            with lock:
                attempts[index] = attempts.get(index, 0) + 1
                attempt = attempts[index]
            if index == 0 and attempt == 1:
                raise ValueError('Controlled transient transport failure')
            return {'proposals': [self.candidate(shard)]}
        result = self.run_review(reviewer, retries=1)
        self.assertEqual(result['statistics']['attempts'], [2, 1])
        self.assertEqual(result['statistics']['total_calls'], 3)
        self.assertEqual(result['proposals'][0]['matched_sessions'], 2)

    def test_exhausted_retries_leave_batch_unconsumed(self):
        self.events(('a',))
        attempts = []
        def reviewer(*args, **kwargs):
            attempts.append(1)
            raise ValueError('Controlled permanent failure')
        with self.assertRaisesRegex(ValueError, '3 attempt'):
            self.run_review(reviewer, workers=1, retries=2)
        self.assertEqual(len(attempts), 3)
        self.assert_unconsumed()

    def test_worker_failure_cancels_other_running_subprocess(self):
        self.events(('a', 'b'))
        start = time.monotonic()
        with self.assertRaisesRegex(ValueError, 'exited 7'):
            self.run_review(self.fixture_reviewer('failure'), workers=2, timeout=10)
        self.assertLess(time.monotonic() - start, 5, 'Sibling worker was not cancelled promptly')
        self.assert_unconsumed()

    def test_one_deadline_bounds_every_attempt_and_cleans_lease(self):
        self.events(('a', 'b'))
        start = time.monotonic()
        with self.assertRaisesRegex(ValueError, 'timed out'):
            self.run_review(self.fixture_reviewer('timeout'), workers=2, timeout=1, retries=2)
        self.assertLess(time.monotonic() - start, 5)
        self.assert_unconsumed()

    def test_incompatible_candidates_require_explicit_reconciliation(self):
        self.events(('a', 'b'))
        def reviewer(shard, *args, **kwargs):
            return {'proposals': [self.candidate(shard, body='Run command ' + str(shard['parallel_shard']['index']))]}
        result = self.run_review(reviewer)
        self.assertEqual(result['status'], 'needs_reconciliation')
        self.assertFalse(result['submitted'])
        self.assertEqual(result['conflicts'][0]['kind'], 'incompatible_candidates')
        self.assert_unconsumed()
        chosen = result['response']['proposals'][0]
        accepted = self.store.submit(result['packet'], {'proposals': [chosen]})
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]['total_sessions'], 2)

    def test_identical_body_under_different_keys_is_not_arbitrarily_selected(self):
        self.events(('a', 'b'))
        def reviewer(shard, *args, **kwargs):
            return {'proposals': [self.candidate(shard, key='fix-' + str(shard['parallel_shard']['index']))]}
        result = self.run_review(reviewer)
        self.assertEqual(result['conflicts'][0]['kind'], 'duplicate_body_keys')
        self.assert_unconsumed()

    def test_merged_evidence_limit_is_not_silently_truncated(self):
        self.events(tuple(f's-{i:02d}' for i in range(40)))
        packet = self.store.prepare(force=True)
        shards = uw_parallel.shard_packet(packet, 2)
        responses = [{'proposals': [self.candidate(shard)]} for shard in shards]
        aggregate = uw_parallel.aggregate_responses(packet, shards, responses)
        self.assertEqual(aggregate['conflicts'][0]['kind'], 'evidence_limit')
        self.assertEqual(len(aggregate['proposals'][0]['evidence_event_ids']), 40)
        self.assertEqual(aggregate['coverage'][0]['matched_sessions'], 40)
        self.assertEqual(aggregate['coverage'][0]['total_sessions'], 40)

    def test_policy_proposal_cap_requires_reconciliation_without_dropping_findings(self):
        self.events(('a', 'b'))
        packet = self.store.prepare(force=True)
        packet['review_policy'] = {'policy': {'limits': {'max_proposals': 0}}}
        shards = uw_parallel.shard_packet(packet, 2)
        responses = [{'proposals': [self.candidate(shard)]} for shard in shards]
        aggregate = uw_parallel.aggregate_responses(packet, shards, responses)
        self.assertEqual(aggregate['conflicts'][0]['kind'], 'proposal_limit')
        self.assertEqual(len(aggregate['proposals']), 1)

    def test_invalid_limits_fail_before_preparing_or_starting_workers(self):
        for kwargs in ({'workers': 0}, {'workers': 5}, {'workers': True}, {'retries': -1},
                       {'retries': 3}, {'timeout': 0}, {'timeout': 301}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                uw_parallel.run_parallel(self.store, engine='claude', **kwargs)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 0)

    def test_policy_guidance_reaches_every_worker_and_caps_aggregation(self):
        from uw_policy import get_policy, set_policy
        self.events(('a', 'b'))
        policy = set_policy(self.store, {'focus': ['build reliability'],
                            'limits': {'max_proposals': 0}}, get_policy(self.store)['hash'])
        def reviewer(shard, *args, **kwargs):
            self.assertEqual(shard['review_policy'], policy)
            prompt = uw.review_prompt(shard)
            self.assertIn('build reliability', prompt)
            self.assertIn('subordinate to fixed user goals', prompt)
            return {'proposals': [self.candidate(shard)]}
        result = self.run_review(reviewer)
        self.assertEqual(result['conflicts'][0]['kind'], 'proposal_limit')
        self.assert_unconsumed()

    def test_policy_change_during_worker_run_rejects_final_submission(self):
        from uw_policy import get_policy, set_policy
        self.events(('a', 'b'))
        changed = threading.Event()
        def reviewer(shard, *args, **kwargs):
            if shard['parallel_shard']['index'] == 0:
                writer = uw.Store(self.project)
                try:
                    set_policy(writer, {'focus': ['new owner priority']}, get_policy(writer)['hash'])
                finally:
                    writer.close()
                changed.set()
            else:
                self.assertTrue(changed.wait(5))
            return {'proposals': [self.candidate(shard)]}
        with self.assertRaisesRegex(ValueError, 'Review policy changed'):
            self.run_review(reviewer)
        self.assert_unconsumed()

    def test_no_due_events_does_not_launch_a_reviewer(self):
        with patch.object(uw, 'review_packet') as reviewer, patch.object(uw.shutil, 'which', return_value='fixture'):
            result = uw_parallel.run_parallel(self.store, engine='claude')
        self.assertFalse(result['due'])
        reviewer.assert_not_called()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM leases').fetchone()[0], 0)

    def test_existing_dream_lease_blocks_parallel_review(self):
        self.events()
        token = self.store.lease('dream')
        try:
            with self.assertRaisesRegex(ValueError, 'Another review'):
                self.run_review(lambda *args, **kwargs: {'proposals': []})
        finally:
            self.store.release('dream', token)

    def test_cancellation_before_process_launch_does_not_execute_command(self):
        event = threading.Event()
        event.set()
        marker = self.root / 'should-not-exist'
        with self.assertRaisesRegex(ValueError, 'cancelled'):
            uw.run_process([sys.executable, '-c', f'from pathlib import Path; Path({str(marker)!r}).touch()'],
                           '', self.root, 5, cancel_event=event)
        self.assertFalse(marker.exists())

    def test_coordinator_interrupt_cancels_workers_and_releases_lease(self):
        self.events(('a', 'b'))
        finished = []
        def reviewer(shard, engine, timeout, *, cancel_event, deadline):
            cancel_event.wait(5)
            finished.append(shard['parallel_shard']['index'])
            raise ValueError('Worker cancelled')
        with patch.object(concurrent.futures, 'wait', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_review(reviewer, timeout=8)
        self.assert_unconsumed()
        self.assertLessEqual(len(finished), 2)

    def test_cli_dispatch_rejects_invalid_worker_limit(self):
        command = [sys.executable, str(Path(uw.__file__)), '--project', str(self.project),
                   'dream-parallel', '--engine', 'claude', '--workers', '5']
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 2)
        self.assertIn('workers must be', json.loads(result.stderr)['error'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
