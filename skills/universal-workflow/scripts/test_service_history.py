#!/usr/bin/env python3
"""Live loopback checks for bounded, authorized history pagination."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import uw_service as service


class ServiceHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uw-service-history-')
        self.directory = Path(self.temp.name) / 'service'
        service.initialize(self.directory)
        self.tokens = {}
        for user, org in (('alice', 'alpha'), ('bob', 'alpha'), ('mallory', 'foreign')):
            self.tokens[user] = service.administer(
                self.directory, 'provision', user, org=org, teams=['design'],
                team_writers=['design'] if user == 'alice' else [])['token']
        self.server = service.ServiceHTTPServer(self.directory, requests_per_minute=600)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': 0.01}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def request(self, action, data, user='alice', scope='individual'):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        headers = {'Content-Type': 'application/json'}
        if user is not None:
            headers['Authorization'] = 'Bearer ' + self.tokens[user]
        connection.request('POST', f'/v1/{scope}/{action}', json.dumps(data), headers)
        response = connection.getresponse()
        raw = response.read()
        status, body = response.status, json.loads(raw)
        self.assertEqual(int(response.getheader('Content-Length')), len(raw))
        connection.close()
        return status, body, len(raw)

    def ok(self, action, data, **options):
        status, body, size = self.request(action, data, **options)
        self.assertEqual(status, 200, body)
        return body['result'], size

    def revisions(self, count, *, body='Fixture procedure.', evidence=None):
        expected = 'NEW'
        for _ in range(count):
            result, _ = self.ok('remember', {'key': 'procedure', 'body': body,
                'evidence': evidence or ['fixture:verified'], 'expected_hash': expected})
            expected = result['hash']

    def collect(self, **options):
        revisions, pages, cursor = [], 0, None
        while True:
            data = {'key': 'procedure', **options}
            if cursor is not None:
                data['cursor'] = cursor
            page, size = self.ok('history', data)
            self.assertIsInstance(page, list)  # Preserve the original response shape.
            self.assertLessEqual(size, service.MAX_HISTORY_PAGE_BYTES)
            if not page:
                break
            revisions.extend(row['revision'] for row in page)
            cursor = page[-1]['revision']
            pages += 1
            self.assertLess(pages, 250, 'Pagination must make progress')
        self.assertEqual(len(revisions), len(set(revisions)))
        return revisions, pages

    def test_large_valid_history_remains_readable_across_byte_bounded_pages(self):
        self.revisions(75, body='b' * 8000, evidence=['fixture:' + 'e' * 992] * 20)
        revisions, pages = self.collect()
        self.assertEqual(revisions, list(range(75, 0, -1)))
        self.assertGreater(pages, 1)

    def test_unicode_history_is_bounded_by_escaped_wire_bytes(self):
        self.revisions(7, body='\U0001f600' * 8000, evidence=['\U0001f600' * 1000] * 20)
        first, size = self.ok('history', {'key': 'procedure'})
        self.assertEqual(len(first), 2)
        self.assertLessEqual(size, service.MAX_HISTORY_PAGE_BYTES)
        revisions, pages = self.collect()
        self.assertEqual(revisions, list(range(7, 0, -1)))
        self.assertGreater(pages, 1)

    def test_limit_cursor_and_appended_revision_do_not_duplicate_or_skip_old_rows(self):
        self.revisions(5)
        page, _ = self.ok('history', {'key': 'procedure', 'limit': 2})
        self.assertEqual([row['revision'] for row in page], [5, 4])
        current, _ = self.ok('current', {'key': 'procedure'})
        self.ok('remember', {'key': 'procedure', 'body': 'Appended while paging.',
                            'evidence': ['fixture'], 'expected_hash': current['hash']})
        remaining, pages = self.collect(limit=2, cursor=page[-1]['revision'])
        self.assertEqual(remaining, [3, 2, 1])
        self.assertEqual(pages, 2)
        self.assertEqual(self.ok('history', {'key': 'procedure', 'cursor': 1})[0], [])
        self.assertEqual(self.ok('history', {'key': 'missing'})[0], [])
        self.assertEqual(self.ok('history', {'key': 'procedure', 'limit': 1})[0][0]['revision'], 6)

    def test_page_byte_boundary_includes_envelope_and_commas(self):
        self.revisions(3)
        rows, _ = self.ok('history', {'key': 'procedure'})
        exact = len(json.dumps({'result': rows[:2]}, ensure_ascii=True,
                               separators=(',', ':')).encode())
        with patch.object(service, 'MAX_HISTORY_PAGE_BYTES', exact):
            page, size = self.ok('history', {'key': 'procedure'})
            self.assertEqual(page, rows[:2])
            self.assertEqual(size, exact)
            self.assertEqual(self.collect()[0], [3, 2, 1])
        with patch.object(service, 'MAX_HISTORY_PAGE_BYTES', exact - 1):
            page, size = self.ok('history', {'key': 'procedure'})
            self.assertEqual(page, rows[:1])
            self.assertLessEqual(size, exact - 1)

    def test_limit_and_cursor_validation_is_strict(self):
        invalid_limits = (0, -1, 201, True, False, 1.0, None, '2', [], {})
        invalid_cursors = (0, -1, 1 << 63, 10 ** 100, True, False, 1.0, None, '2', [], {})
        for field, values in (('limit', invalid_limits), ('cursor', invalid_cursors)):
            for value in values:
                with self.subTest(field=field, value=value):
                    status, body, _ = self.request('history', {'key': 'procedure', field: value})
                    self.assertEqual(status, 400)
                    self.assertEqual(body, {'error': 'Invalid request'})
        self.assertEqual(self.request('history', {'key': 'procedure', 'offset': 1})[0], 400)
        self.assertEqual(self.request('current', {'key': 'procedure', 'limit': 1})[0], 400)
        self.assertEqual(self.ok('history', {'key': 'procedure', 'limit': 200,
                                           'cursor': (1 << 63) - 1})[0], [])

    def test_history_pagination_preserves_namespace_authorization(self):
        self.revisions(3)
        data = {'key': 'procedure', 'limit': 1, 'cursor': 3}
        self.assertEqual(self.request('history', data, user=None)[0], 401)
        self.assertEqual(self.ok('history', data, user='bob')[0], [])
        self.assertEqual(self.ok('history', data, user='mallory')[0], [])
        self.assertEqual(self.request('history', {**data, 'namespace': 'alice'}, user='bob')[0], 403)
        self.ok('remember', {'namespace': 'design', 'key': 'procedure',
                            'body': 'Authorized shared history.', 'evidence': ['fixture']}, scope='team')
        shared = {**data, 'namespace': 'design'}
        self.assertEqual(len(self.ok('history', shared, user='bob', scope='team')[0]), 1)
        self.assertEqual(self.ok('history', shared, user='mallory', scope='team')[0], [])
        self.assertEqual(self.request('history', {**data, 'namespace': 'finance'},
                                      user='bob', scope='team')[0], 403)


if __name__ == '__main__':
    unittest.main(verbosity=2)
