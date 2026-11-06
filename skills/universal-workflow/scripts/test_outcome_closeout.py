"""Outcome recording through real CLI processes, not agent-quality evaluation."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


RUNTIME = Path(__file__).resolve().with_name('uw.py')


class OutcomeCloseoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uw-closeout-')
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.cli('init')
        self.evidence = self.project / '.universal-workflow' / 'evidence'
        self.evidence.mkdir()

    def cli(self, *args, expected=0):
        result = subprocess.run(
            [sys.executable, str(RUNTIME), '--project', str(self.project), *map(str, args)],
            text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, expected, result.stderr)
        return json.loads(result.stdout if expected == 0 else result.stderr)

    def check(self, attempt, expected_exit):
        result = subprocess.run(
            [sys.executable, '-c',
             'import sys; print("fixture check result"); sys.exit(int(sys.argv[1]))',
             str(expected_exit)], text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, expected_exit)
        log = self.evidence / (attempt + '.json')
        log.write_text(json.dumps({'exit_code': result.returncode,
                                  'stdout': result.stdout, 'stderr': result.stderr}), encoding='utf-8')
        return {'id': attempt, 'session': 'closeout-task',
                'status': 'success' if result.returncode == 0 else 'failure',
                'summary': f'Controlled check returned exit code {result.returncode}.',
                'evidence': [str(log)], 'domain': 'software'}

    def record(self, event, expected=0):
        path = self.evidence / 'event-to-save.json'
        path.write_text(json.dumps(event), encoding='utf-8')
        return self.cli('record', '--file', path, expected=expected)

    def reviewed_events(self):
        # Force only for this explicit developer test; ordinary tasks keep the threshold.
        packet = self.cli('prepare', '--force')
        self.assertIn('review_id', packet)
        return packet['events']

    def test_success_reaches_review_without_creating_a_lesson(self):
        event = self.check('validation-attempt-1', 0)
        receipt = self.record(event)
        self.assertEqual(receipt['id'], event['id'])
        self.assertEqual(receipt['seq'], 1)
        status = self.cli('status')
        self.assertEqual(status['events'], 1)
        self.assertEqual(status['memories'], 0)
        events = self.reviewed_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['status'], 'success')
        self.assertEqual(events[0]['evidence'], event['evidence'])

    def test_failure_and_recovery_remain_distinct_and_ordered(self):
        failure = self.check('validation-attempt-1', 1)
        recovery = self.check('validation-attempt-2', 0)
        self.record(failure)
        conflicting = dict(recovery, id=failure['id'])
        rejected = self.record(conflicting, expected=2)
        self.assertIn('different content', rejected['error'])
        self.record(recovery)
        events = self.reviewed_events()
        self.assertEqual([e['id'] for e in events], [failure['id'], recovery['id']])
        self.assertEqual([e['status'] for e in events], ['failure', 'success'])
        self.assertEqual(events[0]['evidence'], failure['evidence'])
        self.assertEqual(events[1]['evidence'], recovery['evidence'])
        self.assertEqual(len({e['session'] for e in events}), 1)

    def test_same_write_retry_returns_receipt_without_duplicate_event(self):
        event = self.check('validation-attempt-1', 0)
        self.record(event)
        receipt = self.record(event)
        self.assertEqual(receipt, {'id': event['id'], 'duplicate': True})
        self.assertEqual(self.cli('status')['events'], 1)
        self.assertEqual(len(self.reviewed_events()), 1)


if __name__ == '__main__':
    unittest.main()
