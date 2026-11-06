"""Authenticated storage admission preserves audit data on quota rejection."""
import contextlib
import hashlib
import http.client
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

import uw_service as service
import uw_service_storage as storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uw-storage-quota-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'service'
        service.initialize(self.directory)
        self.tokens = {user: service.administer(self.directory, 'provision', user, org='alpha')['token']
                       for user in ('alice', 'bob')}
        self.client = service.MemoryService(self.directory, requests_per_minute=600)
        self.call('status')
        self.project = self.directory / 'state/individual/alpha/alice'
        self.database = self.project / '.universal-workflow/memory.sqlite3'

    def call(self, action, body=None, user='alice'):
        return self.client.request('Bearer ' + self.tokens[user], '/v1/individual/' + action, body or {})

    def denied(self, action, body=None, user='alice', status=413):
        with self.assertRaises(service.ServiceError) as error:
            self.call(action, body, user)
        self.assertEqual(error.exception.status, status)

    def put(self, body='Initial verified fact.', expected='NEW'):
        return self.call('remember', {'key': 'same-key', 'body': body, 'evidence': ['fixture'], 'expected_hash': expected})

    def event(self, identifier='event'):
        return self.call('record', {'event': {'id': identifier, 'session': 'fixture', 'status': 'failure', 'summary': 'Verified failure.'}})

    def prepare(self):
        return self.call('review-prepare', {'force': True})

    @staticmethod
    def candidate(packet, key='candidate'):
        return {'key': key, 'kind': 'lesson', 'body': 'Verified improvement.', 'reason': 'Recorded failure.',
                'evidence_event_ids': [packet['events'][0]['id']]}

    def rows(self, table):
        with contextlib.closing(sqlite3.connect(self.database)) as db:
            return db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()

    def size(self):
        return storage.StorageBudget(self.directory).total

    def test_repeated_updates_rollback_and_expiration_cannot_grow_history_past_cap(self):
        with patch.dict(storage.APPEND_LIMITS, history=3):
            head = self.put()
            for index in range(2):
                head = self.put(f'Verified revision {index}.', head['hash'])
            before = self.rows('history'), self.rows('memories'), self.size()
            for _ in range(3):
                self.denied('remember', {'key': 'same-key', 'body': 'Rejected.', 'evidence': ['fixture'], 'expected_hash': head['hash']})
                self.denied('rollback', {'key': 'same-key', 'revision': 1, 'expected_hash': head['hash']})
                self.denied('expire', {'key': 'same-key', 'expected_hash': head['hash']})
            self.assertEqual((self.rows('history'), self.rows('memories'), self.size()), before)
            self.assertEqual(self.call('status')['memories'], 1)

    def test_event_quota_allows_duplicate_without_append_or_counter_drift(self):
        with patch.dict(storage.APPEND_LIMITS, events=2):
            self.event('one'); self.event('two')
            before = self.rows('events'), self.size()
            for _ in range(3):
                self.denied('record', {'event': {'id': 'three', 'session': 'fixture', 'status': 'failure', 'summary': 'Verified failure.'}})
            self.assertTrue(self.event('one')['duplicate'])
            self.assertEqual((self.rows('events'), self.size()), before)

    def test_prepare_and_partial_submit_are_atomic_at_append_limits(self):
        self.event()
        with patch.dict(storage.APPEND_LIMITS, reviews=2, proposals=1):
            first, second = self.prepare(), self.prepare()
            retained = self.rows('reviews')
            self.denied('review-prepare', {'force': True})
            self.assertEqual(self.rows('reviews'), retained)
            self.denied('review-submit', {'packet': first, 'response': {'proposals': [self.candidate(first, 'one'), self.candidate(first, 'two')]}})
            self.assertEqual(self.rows('proposals'), [])
            self.assertEqual(self.rows('reviews'), retained)
            proposals = self.call('review-submit', {'packet': first, 'response': {'proposals': [self.candidate(first)]}})
            self.assertEqual(len(proposals), 1)
            # A failed review is still retrievable, and no prepared row vanished.
            self.assertEqual(self.call('review-get', {'review_id': second['review_id']}), second)
            self.assertEqual(len(self.rows('reviews')), 2)

    def test_decision_at_history_cap_retains_pending_proposal_and_current_hash(self):
        with patch.dict(storage.APPEND_LIMITS, history=1):
            head = self.put()
            self.event()
            packet = self.prepare()
            proposal = self.call('review-submit', {'packet': packet, 'response': {'proposals': [self.candidate(packet, 'same-key')]}})[0]
            before = self.rows('proposals'), self.rows('history'), self.rows('memories'), self.size()
            self.denied('proposal-decide', {'id': proposal['id'], 'accept': True})
            self.assertEqual((self.rows('proposals'), self.rows('history'), self.rows('memories'), self.size()), before)
            self.assertEqual(self.call('current', {'key': 'same-key'})['hash'], head['hash'])
            self.assertEqual(self.call('proposal-get', {'id': proposal['id']})['status'], 'pending')

    def test_sqlite_page_quota_stops_bytes_and_rejected_requests_leave_no_wal_growth(self):
        with patch.object(storage, 'MAX_DATABASE_BYTES', 128 * 1024):
            for index in range(100):
                before = self.rows('events'), self.size()
                try:
                    self.call('record', {'event': {'id': f'large-{index}', 'session': 'fixture', 'status': 'failure', 'summary': 'x' * 3999}})
                except service.ServiceError as error:
                    self.assertEqual(error.status, 413)
                    break
            else:
                self.fail('SQLite byte limit must stop appends before the row cap')
            self.assertGreater(index, 0)
            self.assertEqual((self.rows('events'), self.size()), before)
            self.assertLessEqual(self.database.stat().st_size, storage.MAX_DATABASE_BYTES)
            for attempt in range(3):
                self.denied('record', {'event': {'id': f'rejected-{attempt}', 'session': 'fixture', 'status': 'failure', 'summary': 'x' * 3999}})
            self.assertEqual((self.rows('events'), self.size()), before)
            # Page limits are service-connection-local; owner tools are unchanged.
            with contextlib.closing(service.uw.Store(self.project)) as owner:
                self.assertGreater(owner.db.execute('PRAGMA max_page_count').fetchone()[0], storage.MAX_DATABASE_BYTES // 4096)

    def test_global_reservation_rejects_new_namespace_before_read_initialization(self):
        current = storage.StorageBudget(self.directory)
        peak = 2 * storage.MAX_DATABASE_BYTES + storage.SQLITE_HEADROOM
        limit = current.total + peak - 1
        with patch.object(storage, 'MAX_SERVICE_BYTES', limit):
            self.call('status')  # Existing namespace has room for its reserved peak.
            before = self.size()
            self.denied('status', user='bob')
            self.assertFalse((self.directory / 'state/individual/alpha/bob').exists())
            self.assertEqual(self.size(), before)
            # Another namespace's bytes count toward the same service budget.
            extra = self.directory / 'state/organization/alpha'
            extra.mkdir(parents=True)
            service.uw.atomic(extra / 'owner-fixture', 'x' * 1000)
            with patch.object(storage, 'MAX_SERVICE_BYTES', self.size() - 1):
                self.denied('status')

    def test_inherited_initialization_cannot_spend_pending_target_reservation(self):
        self.event()
        snapshot = storage.StorageBudget(self.directory)
        target = ('individual', 'alpha', 'alice')
        snapshot.reserve(target)
        peak = 2 * storage.MAX_DATABASE_BYTES + storage.SQLITE_HEADROOM
        limit = snapshot.total + snapshot.reservations[target] + peak - 1
        before = self.rows('reviews'), self.size()
        with patch.object(storage, 'MAX_SERVICE_BYTES', limit):
            self.denied('review-prepare', {'force': True})
        self.assertEqual((self.rows('reviews'), self.size()), before)
        self.assertFalse((self.directory / 'state/organization/alpha').exists())

    def test_entry_admission_preserves_existing_readability(self):
        snapshot = storage.StorageBudget(self.directory)
        with patch.object(storage, 'MAX_STATE_ENTRIES', snapshot.entries + 3):
            self.call('status')
            self.denied('status', user='bob')
            self.assertFalse((self.directory / 'state/individual/alpha/bob').exists())
            self.call('status')
        with patch.object(storage, 'MAX_STATE_ENTRIES', snapshot.entries + 12):
            self.call('status', user='bob')
            self.call('status')
            self.call('status', user='bob')
            self.assertLessEqual(storage.StorageBudget(self.directory).entries, storage.MAX_STATE_ENTRIES)

    def test_preexisting_oversized_counts_files_and_wal_fail_closed(self):
        with contextlib.closing(service.uw.Store(self.project)) as owner:
            for index in range(3):
                owner.record({'id': f'owner-{index}', 'session': 'owner', 'status': 'failure', 'summary': 'Owner fixture.'})
        with patch.dict(storage.APPEND_LIMITS, events=2):
            self.denied('status')
        with patch.object(storage, 'MAX_DATABASE_BYTES', self.database.stat().st_size - 1), patch.object(service, 'ServiceStore', side_effect=AssertionError('Must reject before open')):
            self.denied('status')
        wal = Path(str(self.database) + '-wal')
        service.uw.atomic(wal, 'x' * 4096)
        with patch.object(storage, 'MAX_SERVICE_BYTES', storage.StorageBudget(self.directory).total - 1), patch.object(service, 'ServiceStore', side_effect=AssertionError('Must count WAL before open')):
            self.denied('status')
        wal.unlink()  # Remove only this synthetic, deliberately invalid fixture.

    def test_large_legacy_wal_reserves_checkpoint_growth_before_opening_database(self):
        # Accounting operates on file metadata before SQLite opens anything.
        # A sparse synthetic WAL makes the large-file bound inexpensive to test.
        wal = Path(str(self.database) + '-wal')
        with wal.open('wb') as fixture:
            fixture.truncate(storage.MAX_DATABASE_BYTES * 3 // 2)
        try:
            snapshot = storage.StorageBudget(self.directory)
            target = ('individual', 'alpha', 'alice')
            usage = snapshot.namespaces[target]
            growth = usage['wal'] + storage.SQLITE_HEADROOM
            self.assertGreater(growth, 0)
            before = self.database.stat().st_size, wal.stat().st_size
            with patch.object(storage, 'MAX_SERVICE_BYTES', snapshot.total + growth - 1), patch.object(service, 'ServiceStore', side_effect=AssertionError('Reject before checkpoint can grow DB')):
                self.denied('status')
            self.assertEqual((self.database.stat().st_size, wal.stat().st_size), before)
        finally:
            wal.unlink()

    def test_owner_reader_blocks_writes_before_more_wal_is_appended(self):
        head = self.put()
        with contextlib.closing(service.uw.Store(self.project)) as reader, contextlib.closing(service.uw.Store(self.project)) as writer:
            reader.db.execute('BEGIN')
            reader.db.execute('SELECT * FROM memories').fetchall()
            head = writer.put('same-key', 'Owner concurrent fixture.', ['fixture'], head['hash'])
            wal = Path(str(self.database) + '-wal')
            before = wal.stat().st_size
            self.denied('remember', {'key': 'same-key', 'body': 'Blocked.', 'evidence': ['fixture'], 'expected_hash': head['hash']}, status=503)
            self.assertEqual(wal.stat().st_size, before)
            reader.db.rollback()
        self.put('Reader released.', head['hash'])

    def test_crash_left_oversized_logical_database_is_rejected_without_checkpoint(self):
        script = ('import os,sqlite3,sys\n'
                  'db=sqlite3.connect(sys.argv[1])\n'
                  'db.execute("PRAGMA journal_mode=WAL")\n'
                  'db.execute("PRAGMA wal_autocheckpoint=0")\n'
                  'db.execute("INSERT INTO metadata VALUES(?,?)",("owner-large","x"*512000))\n'
                  'db.commit()\n'
                  'os._exit(0)\n')
        subprocess.run([sys.executable, '-c', script, str(self.database)], check=True, timeout=30)
        wal, shm = Path(str(self.database) + '-wal'), Path(str(self.database) + '-shm')
        before = {path: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()) for path in (self.database, wal)}
        self.assertLess(self.database.stat().st_size, 128 * 1024)
        self.assertGreater(wal.stat().st_size, 128 * 1024)
        for missing_shm in (False, True):
            with self.subTest(missing_shm=missing_shm):
                if missing_shm:
                    shm.unlink(missing_ok=True)
                with patch.object(storage, 'MAX_DATABASE_BYTES', 128 * 1024), patch.object(service, 'ServiceStore', side_effect=AssertionError('Do not open oversized logical DB read/write')):
                    self.denied('status')
                self.assertEqual({path: (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest()) for path in (self.database, wal)}, before)

    def test_two_service_instances_serialize_quota_admission(self):
        barrier = threading.Barrier(2)
        outcomes = []
        def write(index):
            client = service.MemoryService(self.directory)
            barrier.wait()
            try:
                client.request('Bearer ' + self.tokens['alice'], '/v1/individual/remember', {'key': f'key-{index}', 'body': 'Concurrent fixture.', 'evidence': ['fixture']})
                outcomes.append(200)
            except service.ServiceError as error:
                outcomes.append(error.status)
        with patch.dict(storage.APPEND_LIMITS, history=1):
            threads = [threading.Thread(target=write, args=(index,)) for index in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join(10)
            self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(sorted(outcomes), [200, 413])
        self.assertEqual(len(self.rows('history')), 1)

    def test_live_http_quota_rejection_is_413_and_keeps_history(self):
        with patch.dict(storage.APPEND_LIMITS, history=1):
            head = self.put()
            server = service.ServiceHTTPServer(self.directory)
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
            thread.start()
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                connection.request('POST', '/v1/individual/remember', json.dumps({'key': 'same-key', 'body': 'Rejected.', 'evidence': ['fixture'], 'expected_hash': head['hash']}),
                                   {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.tokens['alice']})
                response = connection.getresponse()
                self.assertEqual(response.status, 413)
                self.assertNotIn(str(self.directory), response.read().decode())
                self.assertEqual(len(self.rows('history')), 1)
            finally:
                connection.close(); server.shutdown(); server.server_close(); thread.join(10)


if __name__ == '__main__':
    unittest.main(verbosity=2)
