#!/usr/bin/env python3
"""Live loopback HTTP checks for namespace authorization and review provenance."""
import contextlib
import http.client
import io
import json
import os
import socket
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import traceback
import unittest
from unittest.mock import patch

import uw_service as service


class FixtureHTTPServer(service.ServiceHTTPServer):
    def __init__(self, *args, **kwargs):
        self.errors = []
        super().__init__(*args, **kwargs)

    def handle_error(self, request, client_address):
        # Synthetic fixtures may expose diagnostics; production stays silent.
        self.errors.append(traceback.format_exc())


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uw-service-')
        self.root = Path(self.temp.name).resolve()
        self.directory = self.root / 'service'
        service.initialize(self.directory)
        self.tokens = {}
        self.add_user('alice', 'alpha', ['design'], True, ['design'])
        self.add_user('bob', 'alpha', ['design'])
        self.add_user('charlie', 'alpha', ['finance'], False, ['finance'])
        self.add_user('mallory', 'foreign', ['design'], True, ['design'])
        # Authorization checks use initialized stores. Cold SQLite/schema/fsync
        # work can exceed the real five-second connection deadline on a loaded
        # Windows runner; its correctness is checked separately. Initialization
        # exceptions still fail setup directly, without HTTP error suppression.
        fixture = service.MemoryService(self.directory)
        targets = set()
        for user, principal in service.read_policy(self.directory)['principals'].items():
            org = principal['org']
            targets.add(('organization', org))
            targets.update(('team', org, team) for team in principal['teams'])
            targets.update((scope, org, user) for scope in ('individual', 'scratch'))
        for target in sorted(targets):
            with fixture._store(target):
                pass
        self.server = FixtureHTTPServer(self.directory, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.temp.cleanup()

    def add_user(self, user, org, teams=(), org_writer=False, team_writers=()):
        result = service.administer(self.directory, 'provision', user, org=org, teams=teams,
                                    org_writer=org_writer, team_writers=team_writers)
        self.tokens[user] = result['token']
        return result

    def request(self, user, scope, action, payload=None, *, token=None, extra_headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        headers = {'Content-Type': 'application/json'}
        if token is not None:
            headers['Authorization'] = 'Bearer ' + token
        elif user is not None:
            headers['Authorization'] = 'Bearer ' + self.tokens[user]
        if extra_headers:
            headers.update(extra_headers)
        try:
            connection.request('POST', f'/v1/{scope}/{action}', json.dumps(payload or {}), headers)
            response = connection.getresponse()
            status, data = response.status, json.loads(response.read())
        except (http.client.HTTPException, OSError) as error:
            diagnostics = '\n'.join(self.server.errors) or 'No server exception captured; the connection deadline may have expired.'
            raise AssertionError('HTTP fixture transport failed. ' + diagnostics) from error
        finally:
            connection.close()
        return status, data

    def ok(self, user, scope, action, payload=None):
        status, data = self.request(user, scope, action, payload)
        self.assertEqual(status, 200, data)
        return data['result']

    def remember(self, user, scope, key, body, namespace=None):
        data = {'key': key, 'body': body, 'evidence': ['fixture:verified']}
        if namespace is not None:
            data['namespace'] = namespace
        return self.ok(user, scope, 'remember', data)

    def event(self, user='alice', scope='individual', namespace=None):
        data = {'event': {'id': 'failure', 'session': 'session', 'status': 'failure',
                          'summary': 'Verified fixture failure', 'evidence': ['fixture:log']}}
        if namespace is not None:
            data['namespace'] = namespace
        return self.ok(user, scope, 'record', data)

    def prepare(self, user='alice', scope='individual', namespace=None):
        data = {'force': True}
        if namespace is not None:
            data['namespace'] = namespace
        return self.ok(user, scope, 'review-prepare', data)

    def proposal(self, user='alice', scope='individual', namespace=None):
        self.event(user, scope, namespace)
        packet = self.prepare(user, scope, namespace)
        response = {'proposals': [{'key': 'lesson', 'kind': 'lesson', 'body': 'Use the verified build command.',
                    'reason': 'Observed in a recorded failure.', 'ttl_days': 30,
                    'evidence_event_ids': [packet['events'][0]['id']]}]}
        payload = {'packet': packet, 'response': response}
        if namespace is not None:
            payload['namespace'] = namespace
        proposals = self.ok(user, scope, 'review-submit', payload)
        return packet, proposals[0]['id']

    def test_no_credentials_forged_credentials_and_claims_are_denied(self):
        for token in (None, 'invalid', 'A' * 43):
            status, data = self.request(None, 'individual', 'status', token=token)
            self.assertEqual(status, 401)
            self.assertEqual(data, {'error': 'Authentication required'})
        status, _ = self.request('bob', 'organization', 'remember', {
            'key': 'evil', 'body': 'A forged claim', 'evidence': ['fixture'], 'actor': 'alice', 'role': 'owner'})
        self.assertEqual(status, 403)
        status, _ = self.request('bob', 'individual', 'remember', {
            'key': 'evil', 'body': 'A forged claim', 'evidence': ['fixture'], 'actor': 'alice'})
        self.assertEqual(status, 400)

    def test_org_team_individual_and_scratch_isolation_with_inheritance(self):
        self.remember('alice', 'organization', 'org-rule', 'Alpha organization convention.')
        self.remember('alice', 'team', 'team-rule', 'Design team convention.', 'design')
        self.remember('charlie', 'team', 'finance-rule', 'Finance confidential convention.', 'finance')
        self.remember('alice', 'individual', 'personal', 'Alice private knowledge.')
        self.remember('alice', 'scratch', 'scratch', 'Alice private temporary knowledge.')
        bob = json.dumps(self.ok('bob', 'individual', 'recall', {'limit': 20}))
        self.assertIn('Alpha organization convention.', bob)
        self.assertIn('Design team convention.', bob)
        self.assertNotIn('Finance confidential', bob)
        self.assertNotIn('Alice private', bob)
        foreign = json.dumps(self.ok('mallory', 'individual', 'recall', {'limit': 20}))
        self.assertNotIn('Alpha organization', foreign)
        self.assertNotIn('Design team', foreign)
        alice_scratch = json.dumps(self.ok('alice', 'scratch', 'recall', {'limit': 20}))
        self.assertIn('Alice private knowledge.', alice_scratch)
        self.assertIn('Alice private temporary knowledge.', alice_scratch)
        self.assertIsNone(self.ok('bob', 'scratch', 'current', {'key': 'scratch'}))
        self.assertEqual(self.request('bob', 'individual', 'current', {'namespace': 'alice', 'key': 'personal'})[0], 403)
        self.assertEqual(self.request('bob', 'team', 'history', {'namespace': 'finance', 'key': 'finance-rule'})[0], 403)
        self.assertEqual(self.request('mallory', 'organization', 'current', {'namespace': 'alpha', 'key': 'org-rule'})[0], 403)

    def test_inherited_knowledge_does_not_grant_write_permission(self):
        org = self.remember('alice', 'organization', 'rule', 'Organization source.')
        team = self.remember('alice', 'team', 'rule', 'Team source.', 'design')
        for scope, namespace, old in (('organization', 'alpha', org), ('team', 'design', team)):
            for action, extra in (('remember', {'body': 'Unauthorized change', 'evidence': ['fixture']}),
                                  ('expire', {}), ('rollback', {'revision': 1})):
                status, _ = self.request('bob', scope, action, {'namespace': namespace, 'key': 'rule',
                                                               'expected_hash': old['hash'], **extra})
                self.assertEqual(status, 403)
            self.assertEqual(self.request('bob', scope, 'review-prepare', {'namespace': namespace, 'force': True})[0], 403)
        self.remember('bob', 'individual', 'rule', 'Bob local interpretation.')
        self.assertEqual(self.ok('alice', 'organization', 'current', {'key': 'rule'})['payload']['body'], 'Organization source.')

    def test_review_packet_contains_only_target_and_authorized_ancestors(self):
        self.remember('alice', 'organization', 'org', 'Organization context.')
        self.remember('alice', 'team', 'design', 'Design context.', 'design')
        self.remember('charlie', 'team', 'finance', 'Private finance context.', 'finance')
        self.remember('bob', 'individual', 'personal', 'Bob private context.')
        self.remember('alice', 'scratch', 'scratch', 'Alice scratch context.')
        self.event()
        packet = self.prepare()
        rendered = json.dumps(packet)
        self.assertIn('Organization context.', rendered)
        self.assertIn('Design context.', rendered)
        self.assertNotIn('Private finance', rendered)
        self.assertNotIn('Bob private', rendered)
        self.assertNotIn('Alice scratch', rendered)
        self.assertEqual(packet['service_acl']['target'], ['individual', 'alpha', 'alice'])
        self.assertEqual(packet['service_acl']['principal'], 'alice')
        # An upper namespace never receives private/lower namespace context.
        self.event(scope='organization')
        org_packet = self.prepare(scope='organization')
        self.assertEqual(org_packet['inherited_context'], [])
        self.assertNotIn('Design context.', json.dumps(org_packet))
        self.assertEqual(self.request('bob', 'individual', 'review-get', {'review_id': packet['review_id']})[0], 404)
        self.assertEqual(self.request('bob', 'individual', 'review-submit', {'packet': packet, 'response': {'proposals': []}})[0], 403)
        self.assertEqual(self.request('alice', 'organization', 'review-submit', {'packet': packet, 'response': {'proposals': []}})[0], 403)

    def test_pending_proposals_remain_principal_and_namespace_bound(self):
        packet, pid = self.proposal()
        self.assertEqual(self.request('bob', 'individual', 'proposal-get', {'id': pid})[0], 404)
        self.assertEqual(self.request('bob', 'individual', 'proposal-decide', {'id': pid, 'accept': True})[0], 404)
        self.assertEqual(self.request('alice', 'organization', 'proposal-decide', {'id': pid, 'accept': True})[0], 404)
        result = self.ok('alice', 'individual', 'proposal-decide', {'id': pid, 'accept': True})
        self.assertEqual(result['decision'], 'accepted')
        history = self.ok('alice', 'individual', 'history', {'key': 'lesson'})
        self.assertEqual(history[0]['actor'], 'principal:alice')
        self.assertEqual(self.ok('bob', 'individual', 'history', {'key': 'lesson'}), [])
        self.assertEqual(self.ok('bob', 'individual', 'proposals'), [])

    def test_changed_membership_invalidates_prepared_and_pending_reviews_immediately(self):
        packet, pid = self.proposal()
        service.administer(self.directory, 'membership', 'alice', org='alpha', teams=[], org_writer=False)
        self.assertEqual(self.request('alice', 'team', 'status', {'namespace': 'design'})[0], 403)
        self.assertEqual(self.request('alice', 'individual', 'review-get', {'review_id': packet['review_id']})[0], 403)
        self.assertEqual(self.request('alice', 'individual', 'review-submit', {'packet': packet, 'response': {'proposals': []}})[0], 403)
        self.assertEqual(self.request('alice', 'individual', 'proposal-get', {'id': pid})[0], 403)
        self.assertEqual(self.request('alice', 'individual', 'proposal-decide', {'id': pid, 'accept': True})[0], 403)
        self.assertEqual(self.ok('alice', 'individual', 'proposals'), [])
        self.assertIsNone(self.ok('alice', 'individual', 'current', {'key': 'lesson'}))

    def test_rotation_and_revocation_take_effect_without_server_restart(self):
        old = self.tokens['alice']
        new = service.administer(self.directory, 'rotate', 'alice')['token']
        self.assertEqual(self.request(None, 'individual', 'status', token=old)[0], 401)
        self.assertEqual(self.request(None, 'individual', 'status', token=new)[0], 200)
        service.administer(self.directory, 'revoke', 'alice')
        self.assertEqual(self.request(None, 'individual', 'status', token=new)[0], 401)
        policy_text = (self.directory/'policy.json').read_text()
        self.assertNotIn(old, policy_text)
        self.assertNotIn(new, policy_text)

    def test_revocation_is_serialized_with_authorized_writes(self):
        entered, release, admin_started, admin_done = (threading.Event() for _ in range(4))
        responses, failures = [], []
        original_put = service.uw.Store.put
        def delayed_put(store, *args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError('Timed out waiting for test release')
            return original_put(store, *args, **kwargs)
        def write():
            try:
                responses.append(self.request('alice', 'individual', 'remember', {
                    'key': 'ordered', 'body': 'Committed before revocation.', 'evidence': ['fixture']}))
            except BaseException as error:
                failures.append(error)
        def revoke():
            admin_started.set()
            try:
                service.administer(self.directory, 'revoke', 'alice')
            except BaseException as error:
                failures.append(error)
            finally:
                admin_done.set()
        with patch.object(service.uw.Store, 'put', delayed_put):
            writer = threading.Thread(target=write)
            writer.start()
            self.assertTrue(entered.wait(5))
            admin = threading.Thread(target=revoke)
            admin.start()
            try:
                self.assertTrue(admin_started.wait(5))
                self.assertFalse(admin_done.wait(0.1), 'Revocation crossed the authorization/write critical section')
            finally:
                release.set()
                writer.join(6)
                admin.join(6)
        self.assertFalse(writer.is_alive())
        self.assertFalse(admin.is_alive())
        self.assertEqual(failures, [])
        self.assertEqual(responses[0][0], 200)
        self.assertTrue(admin_done.is_set())
        self.assertEqual(self.request('alice', 'individual', 'status')[0], 401)

    def test_foreign_evidence_and_unevaluated_workflows_cannot_be_promoted(self):
        self.event()
        packet = self.prepare()
        candidate = {'key': 'workflow', 'kind': 'workflow', 'body': 'Run the verified project check.',
                     'reason': 'Supported by the recorded failure.', 'ttl_days': 30,
                     'evidence_event_ids': ['foreign-event']}
        status, _ = self.request('alice', 'individual', 'review-submit', {
            'packet': packet, 'response': {'proposals': [candidate]}})
        self.assertEqual(status, 400)
        candidate['evidence_event_ids'] = [packet['events'][0]['id']]
        proposals = self.ok('alice', 'individual', 'review-submit', {
            'packet': packet, 'response': {'proposals': [candidate]}})
        status, _ = self.request('alice', 'individual', 'proposal-decide', {'id': proposals[0]['id'], 'accept': True})
        self.assertEqual(status, 400)
        self.assertIsNone(self.ok('alice', 'individual', 'current', {'key': 'workflow'}))
        status, _ = self.request('alice', 'individual', 'proposal-decide', {
            'id': proposals[0]['id'], 'accept': True, 'receipt': {'passed': True}})
        self.assertEqual(status, 400)

    def test_no_path_configuration_export_or_evaluator_routes(self):
        for action in ('export', 'backup', 'evaluate', 'config', 'dream-run', 'trace-import'):
            self.assertEqual(self.request('alice', 'individual', action, {'path': '/etc/passwd'})[0], 404)
        for payload in ({'namespace': '../alice'}, {'namespace': 'ALICE'}, {'scope': 'organization'}, {'path': '/etc/passwd'}):
            self.assertIn(self.request('alice', 'individual', 'status', payload)[0], (400, 403))
        status, data = self.request('alice', 'individual', 'remember', {
            'key': 'bad', 'body': 'password=very-private-value', 'evidence': ['fixture']})
        self.assertEqual(status, 400)
        self.assertNotIn('very-private-value', json.dumps(data))
        self.assertNotIn(str(self.directory), json.dumps(self.ok('alice', 'individual', 'status')))

    def test_http_body_response_and_rate_limits(self):
        self.server.service.requests_per_minute = 2
        self.assertEqual(self.request('alice', 'individual', 'status')[0], 200)
        self.assertEqual(self.request('alice', 'individual', 'status')[0], 200)
        self.assertEqual(self.request('alice', 'individual', 'status')[0], 429)
        self.assertEqual(self.request('bob', 'individual', 'status')[0], 200)
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        connection.request('POST', '/v1/individual/status', b'{}', {
            'Content-Length': str(service.MAX_BODY+1), 'Content-Type': 'application/json',
            'Authorization': 'Bearer '+self.tokens['alice']})
        response = connection.getresponse()
        self.assertEqual(response.status, 413)
        response.read(); connection.close()
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_absolute_connection_deadline_releases_stalled_header_client(self):
        with patch.object(service, 'CONNECTION_TIMEOUT', 0.2):
            client = socket.create_connection(self.server.server_address, timeout=2)
            try:
                client.sendall(b'POST /v1/individual/status HTTP/1.1\r\nX-Slow: ')
                client.settimeout(2)
                self.assertEqual(client.recv(1), b'')
            finally:
                client.close()
        self.assertEqual(self.request('alice', 'individual', 'status')[0], 200)

    def test_admin_validation_revision_guard_and_file_permissions(self):
        policy = service.read_policy(self.directory)
        with self.assertRaisesRegex(ValueError, 'revision'):
            service.administer(self.directory, 'revoke', 'alice', expected_revision=policy['revision']-1)
        self.assertEqual(service.read_policy(self.directory), policy)
        with self.assertRaises(ValueError):
            self.add_user('BadCase', 'alpha')
        with self.assertRaises(ValueError):
            self.add_user('eve', 'alpha', [], False, ['design'])
        self.assertEqual(service.read_policy(self.directory), policy)
        if os.name == 'posix':
            self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
            self.assertEqual((self.directory/'policy.json').stat().st_mode & 0o777, 0o600)
            (self.directory/'policy.json').chmod(0o644)
            try:
                self.assertEqual(self.request('alice', 'individual', 'status')[0], 400)
            finally:
                (self.directory/'policy.json').chmod(0o600)

    def test_cli_admin_token_is_issued_only_at_creation_or_rotation(self):
        command = [sys.executable, str(Path(service.__file__)), 'provision', '--directory', str(self.directory),
                   '--user', 'cli-user', '--org', 'alpha']
        result = subprocess.run(command, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        token = json.loads(result.stdout)['token']
        self.assertNotIn(token, (self.directory/'policy.json').read_text())
        repeated = subprocess.run(command, capture_output=True, text=True, timeout=5)
        self.assertEqual(repeated.returncode, 2)
        self.assertNotIn(token, repeated.stdout + repeated.stderr)

    def test_policy_capacity_rejection_preserves_authentication_and_administration(self):
        teams = [f't{i:02d}-' + 't'*60 for i in range(16)]
        rejected = False
        for number in range(service.MAX_PRINCIPALS):
            before = (self.directory/'policy.json').read_bytes()
            previous = service.read_policy(self.directory)
            try:
                service.administer(self.directory, 'provision', f'u{number:02d}-'+'u'*60,
                                   org='org-'+'o'*60, teams=teams, team_writers=teams)
            except ValueError as error:
                self.assertIn('size limit', str(error))
                self.assertEqual((self.directory/'policy.json').read_bytes(), before)
                self.assertEqual(service.read_policy(self.directory), previous)
                rejected = True
                break
        self.assertTrue(rejected, 'Fixture must exceed the serialized policy limit')
        self.assertEqual(self.request('alice', 'individual', 'status')[0], 200)
        service.administer(self.directory, 'revoke', 'bob')
        self.assertEqual(self.request('bob', 'individual', 'status')[0], 401)

    def test_service_workflow_rollback_and_expiration_cannot_bypass_evaluation(self):
        self.remember('alice', 'individual', 'workflow', 'Legacy procedure A.')
        self.event()
        packet = self.prepare()
        response = {'proposals': [{'key': 'workflow', 'kind': 'workflow', 'body': 'Evaluated procedure B.',
                    'reason': 'Verified fixture correction.', 'ttl_days': 30,
                    'evidence_event_ids': [packet['events'][0]['id']]}]}
        proposal = self.ok('alice', 'individual', 'review-submit', {'packet': packet, 'response': response})[0]
        evaluator = self.root/'trusted-evaluator.py'
        evaluator.write_text('import json,sys\np=json.load(sys.stdin)\n'
                             'print(json.dumps({"cases":[{"id":c["id"],"baseline_pass":False,'
                             '"candidate_pass":True,"fixed_constraints_pass":True} for c in p["cases"]]}))\n')
        cases = self.root/'cases.json'
        cases.write_text(json.dumps([{'id': f'heldout-{i}'} for i in range(3)]))
        project = self.directory/'state/individual/alpha/alice'
        with contextlib.closing(service.uw.Store(project)) as owner:
            owner.config['evaluator'] = [sys.executable, str(evaluator)]
            self.assertTrue(owner.evaluate(proposal['id'], cases)['passed'])
        accepted = self.ok('alice', 'individual', 'proposal-decide', {'id': proposal['id'], 'accept': True})
        for action, extra in (('rollback', {'revision': 1}), ('expire', {})):
            status, _ = self.request('alice', 'individual', action, {
                'key': 'workflow', 'expected_hash': accepted['hash'], **extra})
            self.assertEqual(status, 403)
            self.assertEqual(self.ok('alice', 'individual', 'current', {'key': 'workflow'})['hash'], accepted['hash'])
        # Local owner semantics remain available; HTTP cannot restore an old
        # workflow into a non-workflow target without a new evaluated proposal.
        with contextlib.closing(service.uw.Store(project)) as owner:
            restored = owner.rollback('workflow', 1, accepted['hash'])
        status, _ = self.request('alice', 'individual', 'rollback', {
            'key': 'workflow', 'expected_hash': restored['hash'], 'revision': 2})
        self.assertEqual(status, 403)
        self.assertEqual(self.ok('alice', 'individual', 'current', {'key': 'workflow'})['hash'], restored['hash'])

    def test_memory_quota_covers_remember_and_accepted_proposals(self):
        with patch.object(service, 'MAX_NAMESPACE_MEMORIES', 3):
            for i in range(3):
                self.remember('alice', 'individual', f'key-{i}', f'Existing value {i}.')
            status, _ = self.request('alice', 'individual', 'remember', {
                'key': 'excess', 'body': 'Not inserted.', 'evidence': ['fixture']})
            self.assertEqual(status, 413)
            packet, pid = self.proposal()
            self.assertEqual(self.request('alice', 'individual', 'proposal-decide', {'id': pid, 'accept': True})[0], 413)
            self.assertEqual(self.ok('alice', 'individual', 'proposal-get', {'id': pid})['status'], 'pending')
            self.assertIsNone(self.ok('alice', 'individual', 'current', {'key': 'lesson'}))
            self.assertEqual(self.ok('alice', 'individual', 'status')['memories'], 3)

    def test_external_oversized_store_fails_closed_before_recall_or_prepare(self):
        self.ok('alice', 'individual', 'status')
        project = self.directory/'state/individual/alpha/alice'
        with contextlib.closing(service.uw.Store(project)) as owner:
            for i in range(4):
                owner.put(f'key-{i}', 'Large external namespace fixture.', ['fixture'])
        with patch.object(service, 'MAX_NAMESPACE_MEMORIES', 3), patch.object(service.ServiceStore, 'recall', side_effect=AssertionError('Must reject before scanning')):
            self.assertEqual(self.request('alice', 'individual', 'recall', {'limit': 1})[0], 413)
            self.assertEqual(self.request('alice', 'individual', 'review-prepare', {'force': True})[0], 413)
        # An oversized inherited namespace must fail closed as well.
        self.ok('alice', 'organization', 'status')
        org_project = self.directory/'state/organization/alpha'
        with contextlib.closing(service.uw.Store(org_project)) as owner:
            for i in range(4):
                owner.put(f'key-{i}', 'Oversized inherited context.', ['fixture'])
        with patch.object(service, 'MAX_NAMESPACE_MEMORIES', 3):
            self.assertEqual(self.request('bob', 'individual', 'recall', {'limit': 1})[0], 413)
            self.event('bob')
            self.assertEqual(self.request('bob', 'individual', 'review-prepare', {'force': True})[0], 413)
        with contextlib.closing(service.uw.Store(self.directory/'state/individual/alpha/bob')) as owner:
            self.assertEqual(owner.db.execute('SELECT COUNT(*) FROM reviews').fetchone()[0], 0)

    def test_service_recall_retains_only_bounded_best_matches(self):
        self.remember('alice', 'individual', 'less', 'Build command reference.')
        self.remember('alice', 'individual', 'best', 'Build command verified project check.')
        self.remember('alice', 'individual', 'unrelated', 'Different knowledge.')
        with patch.object(service.uw.Store, 'recall', side_effect=AssertionError('Unbounded local recall must not be called')):
            results = self.ok('alice', 'individual', 'recall', {
                'query': 'verified project build', 'limit': 1, 'include_inherited': False})
            self.assertEqual([item['memory']['key'] for item in results], ['best'])
            self.event()
            packet = self.prepare()
            self.assertEqual(len(packet['memories']), 3)

    def test_recall_scan_budget_is_shared_across_inherited_namespaces(self):
        self.remember('alice', 'organization', 'org', 'Organization knowledge.')
        self.remember('alice', 'individual', 'own', 'Individual knowledge.')
        with patch.object(service, 'MAX_RECALL_SCAN', 1):
            status, _ = self.request('alice', 'individual', 'recall', {'query': 'absent', 'limit': 1})
            self.assertEqual(status, 413)
            # The budget is reset for the next request and disabling inherited
            # search can complete within the bound without partial results.
            self.assertEqual(self.ok('alice', 'individual', 'recall', {
                'query': 'absent', 'limit': 1, 'include_inherited': False}), [])

    def test_unicode_review_batches_fit_actual_json_transport_without_skipping_events(self):
        ids = []
        for number in range(30):
            record = self.ok('alice', 'individual', 'record', {'event': {
                'id': f'wide-{number}', 'session': f'session-{number}', 'status': 'failure',
                'summary': '\U0001f600'*3999, 'evidence': ['fixture:unicode']}})
            ids.append(record['id'])
        reviewed = []
        batches = 0
        while True:
            packet = self.prepare()
            if packet.get('due') is False:
                break
            request = {'packet': packet, 'response': {'proposals': []}}
            self.assertLessEqual(len(json.dumps(request, ensure_ascii=True).encode()), service.MAX_REVIEW_PACKET_BYTES)
            self.assertEqual(self.ok('alice', 'individual', 'review-submit', request), [])
            reviewed.extend(event['id'] for event in packet['events'])
            batches += 1
            self.assertLess(batches, 10)
        self.assertGreater(batches, 1)
        self.assertEqual(reviewed, ids)


if __name__ == '__main__':
    unittest.main(verbosity=2)
