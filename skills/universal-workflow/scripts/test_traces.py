#!/usr/bin/env python3
"""Trace import contract tests with selected, synthetic evidence only."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import uw
import uw_traces


class TraceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uw-traces-')
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.store = uw.Store(self.project, create=True)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def document(self):
        return {'schema': 1, 'project_id': self.store.config['project_id'],
                'source': 'selected-session-export', 'session_id': 'session-1', 'turns': [
                    {'id': 'turn-1', 'status': 'failure', 'summary': 'Build failed because the project uses a different target.',
                     'messages': [{'role': 'user', 'content': 'Build the web project.'},
                                  {'role': 'assistant', 'content': 'The build command exited with target missing.'}],
                     'tool_calls': [{'name': 'shell', 'input_summary': 'Ran make build.',
                                     'output_summary': 'No rule to make target build; make web-build exists.'}],
                     'skills_used': ['web-build'],
                     'user_corrections': ['Use make web-build in this project.']}]}

    def test_context_survives_import_prepare_and_proposal_evidence(self):
        imported = uw_traces.import_session(self.store, self.document())
        self.assertEqual(imported['imported'], 1)
        packet = self.store.prepare(force=True)
        event = packet['events'][0]
        trace = event['trace']
        self.assertEqual(trace['source'], 'selected-session-export')
        self.assertEqual(trace['turn_id'], 'turn-1')
        self.assertEqual(trace['messages'][0]['role'], 'user')
        self.assertIn('make web-build', trace['tool_calls'][0]['output_summary'])
        self.assertEqual(trace['skills_used'], ['web-build'])
        self.assertEqual(trace['user_corrections'], ['Use make web-build in this project.'])
        proposals = self.store.submit(packet, {'proposals': [{'key': 'build-target', 'kind': 'lesson',
            'body': 'Use make web-build.', 'reason': 'The selected correction and tool result agree.',
            'evidence_event_ids': [event['id']]}]})
        self.assertEqual(proposals[0]['matched_sessions'], 1)
        self.assertEqual(proposals[0]['evidence_event_ids'], [event['id']])
        self.assertEqual(self.store.status()['memories'], 0)

    def test_credentials_and_selected_pii_are_absent_from_database_and_packets(self):
        doc = self.document()
        values = ['sk-' + 'x' * 32, 'ghp_' + 'a' * 36, 'Bearer abcdef012345',
                  'Basic c2VjcmV0OmFiYw==', 'password="two word secret"',
                  '-----BEGIN PRIVATE KEY-----\nmy-key-data\n-----END PRIVATE KEY-----',
                  'https://alice:secretvalue@example.test/path', 'https://example.test/?sig=opaque-signed-secret',
                  'user@example.test',
                  '/home/alice/private/config', 'C:\\Users\\alice\\secret']
        doc['source'] = 'https://example.test/export?api_key=source-credential&format=json'
        doc['session_id'] = 'selected-session token=session-credential'
        doc['turns'][0]['id'] = 'turn token=turn-credential'
        doc['turns'][0]['messages'].append({'role': 'tool', 'content': '\n'.join(values)})
        doc['turns'][0]['tool_calls'][0]['input_summary'] = 'Authorization: Bearer abcdef012345; token=short'
        doc['turns'][0]['user_corrections'].append('Do not reuse api_key="another secret value"')
        uw_traces.import_session(self.store, doc)
        self.assertEqual(uw_traces.import_session(self.store, doc)['duplicates'], 1)
        packet = self.store.prepare(force=True)
        stored = uw.canon(packet)
        for value in values:
            self.assertNotIn(value, stored)
        for value in ('abcdef012345', 'another secret value', 'my-key-data', 'secretvalue', 'two word secret',
                      'source-credential', 'session-credential', 'turn-credential', 'opaque-signed-secret'):
            self.assertNotIn(value, stored)
            for path in self.store.root.glob('memory.sqlite3*'):
                self.assertNotIn(value.encode(), path.read_bytes())
        self.assertIn('make web-build', stored)
        self.assertIn('[REDACTED]', stored)

    def test_multiple_turns_are_idempotent_and_keep_one_session(self):
        doc = self.document()
        doc['turns'].append({**doc['turns'][0], 'id': 'turn-2', 'status': 'success'})
        first = uw_traces.import_session(self.store, doc)
        again = uw_traces.import_session(self.store, doc)
        self.assertEqual(first['imported'], 2)
        self.assertEqual(again['duplicates'], 2)
        self.assertEqual(again['imported'], 0)
        packet = self.store.prepare(force=True)
        self.assertEqual(len({event['session'] for event in packet['events']}), 1)
        self.assertEqual(len({event['id'] for event in packet['events']}), 2)

    def test_conflicting_later_turn_rolls_back_new_earlier_turn(self):
        original = self.document()
        uw_traces.import_session(self.store, original)
        changed = copy.deepcopy(original)
        changed['turns'][0]['summary'] = 'Different evidence for the same selected turn.'
        changed['turns'].insert(0, {**original['turns'][0], 'id': 'new-turn'})
        with self.assertRaisesRegex(ValueError, 'different content'):
            uw_traces.import_session(self.store, changed)
        self.assertEqual(self.store.status()['events'], 1)
        self.assertFalse(self.store.db.in_transaction)

    def test_foreign_projects_and_direct_record_traces_are_rejected(self):
        doc = self.document()
        doc['project_id'] = 'foreign-project'
        with self.assertRaisesRegex(ValueError, 'project mismatch'):
            uw_traces.import_session(self.store, doc)
        trace = {'schema': 1, 'project_id': 'foreign-project', 'source': 'source', 'session_id': 's',
                 'turn_id': 't', 'messages': [], 'tool_calls': [], 'skills_used': [], 'user_corrections': []}
        with self.assertRaisesRegex(ValueError, 'project mismatch'):
            self.store.record({'session': 's', 'summary': 'Selected trace.', 'status': 'observed', 'trace': trace})
        self.assertEqual(self.store.status()['events'], 0)

    def test_unsupported_or_malformed_fields_fail_without_partial_import(self):
        for kind in ('unknown', 'analysis', 'nested-tool', 'duplicate', 'schema-bool'):
            with self.subTest(kind=kind):
                doc = self.document()
                last = copy.deepcopy(doc['turns'][0])
                last['id'] = 'turn-2'
                doc['turns'].append(last)
                if kind == 'unknown': last['private_native_memory_path'] = '/never-read'
                elif kind == 'analysis': last['messages'][0]['role'] = 'analysis'
                elif kind == 'nested-tool': last['tool_calls'][0]['input_summary'] = {'arbitrary': 'data'}
                elif kind == 'duplicate': last['id'] = 'turn-1'
                else: doc['schema'] = True
                with self.assertRaises(ValueError):
                    uw_traces.import_session(self.store, doc)
                self.assertEqual(self.store.status()['events'], 0)

    def test_counts_depth_bytes_and_individual_turn_budgets(self):
        cases = []
        doc = self.document(); doc['turns'] = [{**doc['turns'][0], 'id': str(i)} for i in range(101)]; cases.append(doc)
        doc = self.document(); doc['turns'][0]['messages'] *= 11; cases.append(doc)
        doc = self.document(); doc['turns'][0]['messages'] = [{'role': 'user', 'content': '界' * 4000} for _ in range(4)]; cases.append(doc)
        doc = self.document(); doc['source'] = 'x' * 1_000_001; cases.append(doc)
        doc = self.document(); deep = 'text'
        for _ in range(12): deep = [deep]
        doc['turns'][0]['messages'] = deep; cases.append(doc)
        for number, doc in enumerate(cases):
            with self.subTest(case=number), self.assertRaises(ValueError):
                uw_traces.import_session(self.store, doc)
        self.assertEqual(self.store.status()['events'], 0)

    def test_many_bounded_turns_are_reviewed_without_skipping_evidence(self):
        doc = self.document()
        doc['turns'] = []
        for i in range(70):
            doc['turns'].append({'id': str(i), 'status': 'observed', 'summary': 'Selected evidence.',
                                'messages': [{'role': 'user', 'content': ('bounded diagnostic context ' * 100)}]})
        imported = uw_traces.import_session(self.store, doc)
        ids = []
        while True:
            packet = self.store.prepare(force=True)
            if packet.get('due') is False: break
            self.assertLess(len(uw.canon(packet).encode('utf-8')), 510_000)
            ids.extend(event['id'] for event in packet['events'])
            self.store.submit(packet, {'proposals': []})
        self.assertEqual(set(ids), {event['id'] for event in imported['events']})
        self.assertEqual(len(ids), 70)

    def test_loader_handles_invalid_utf8_deep_json_and_size_without_echoing_input(self):
        path = self.root / 'selected.json'
        for raw in (b'\xffprivate', b'[' * 2000 + b']' * 2000, b'x' * 1_000_001):
            path.write_bytes(raw)
            with self.assertRaises(ValueError) as error:
                uw_traces.load_session(path)
            self.assertNotIn('private', str(error.exception))
            self.assertEqual(self.store.status()['events'], 0)

    def test_cli_reads_only_selected_file_and_requires_initialized_project(self):
        selected = self.root / 'chosen.json'
        selected.write_text(json.dumps(self.document()), encoding='utf-8')
        unselected = self.project / 'unselected.json'
        unselected.write_text('invalid unrelated input', encoding='utf-8')
        command = [sys.executable, str(Path(__file__).with_name('uw.py')), '--project', str(self.project),
                   'trace-import', '--file', str(selected)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['imported'], 1)
        self.assertEqual(unselected.read_text(), 'invalid unrelated input')
        self.assertFalse((self.project / 'trace.json').exists())
        other = self.root / 'uninitialized'; other.mkdir()
        command[command.index(str(self.project))] = str(other)
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertFalse((other / '.universal-workflow').exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
