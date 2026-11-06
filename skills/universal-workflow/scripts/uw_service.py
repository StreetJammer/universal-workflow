#!/usr/bin/env python3
"""Optional authenticated loopback memory service. Python 3.10+, stdlib only."""
from __future__ import annotations

import argparse
from collections import defaultdict, deque
from contextlib import contextmanager
import hashlib
import heapq
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import stat
import sys
import threading
import time

import uw

MAX_BODY = 1_000_000
MAX_RESPONSE = 2_000_000
MAX_HISTORY_PAGE_BYTES = 1_000_000
MAX_HISTORY_PAGE_ITEMS = 200
MAX_HISTORY_CURSOR = (1 << 63) - 1
MAX_PRINCIPALS = 64
MAX_POLICY_BYTES = 128_000
# Admission must leave room to disable every principal (false adds one byte)
# and grow the revision by another 256 decimal digits. Security operations can
# use that reserve; they never relax the hard on-disk/read limit.
POLICY_SECURITY_RESERVE = MAX_PRINCIPALS + 256
MAX_NAMESPACE_MEMORIES = 1000
MAX_RECALL_SCAN = 1000
MAX_MEMORY_BYTES = 128_000
MAX_REVIEW_PACKET_BYTES = 600_000
CONNECTION_TIMEOUT = 5
SCOPES = ('organization', 'team', 'individual', 'scratch')
READ_ACTIONS = {'recall', 'current', 'history', 'status', 'review-get', 'proposal-get', 'proposals'}
WRITE_ACTIONS = {'remember', 'record', 'expire', 'rollback', 'review-prepare', 'review-submit', 'proposal-decide'}
ROUTE = re.compile(r'/v1/(organization|team|individual|scratch)/([a-z-]+)')
NAME = re.compile(r'[a-z0-9][a-z0-9_-]{0,63}')


class ServiceError(Exception):
    def __init__(self, status, message):
        self.status, self.message = status, message


def name(value):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ValueError('Invalid namespace identifier')
    return value


def fields(value, required=(), optional=()):
    if not isinstance(value, dict) or not set(required) <= value.keys() or value.keys() - set(required) - set(optional):
        raise ValueError('Unexpected or missing request fields')


def _private(path, directory=False):
    info = path.lstat()
    reparse = getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)
    if stat.S_ISLNK(info.st_mode) or reparse:
        raise ValueError('Service paths must not be links or reparse points')
    if directory != stat.S_ISDIR(info.st_mode) or (not directory and not stat.S_ISREG(info.st_mode)):
        raise ValueError('Invalid service path type')
    if os.name == 'posix' and (info.st_mode & 0o077 or info.st_uid != os.getuid()):
        raise ValueError('Service state must be owned by this account and private')


def _directory(path):
    path = Path(path).expanduser().absolute()
    _private(path, directory=True)
    _private(path / 'state', directory=True)
    return path


def _validate_policy(policy):
    fields(policy, ('schema', 'revision', 'principals'))
    if type(policy['schema']) is not int or policy['schema'] != 1 or type(policy['revision']) is not int or policy['revision'] < 0:
        raise ValueError('Invalid service policy version')
    principals = policy['principals']
    if not isinstance(principals, dict) or len(principals) > MAX_PRINCIPALS:
        raise ValueError('Invalid principals')
    credentials = set()
    for user, principal in principals.items():
        name(user)
        fields(principal, ('org', 'teams', 'org_writer', 'team_writers', 'token_hash', 'enabled'))
        name(principal['org'])
        for key in ('teams', 'team_writers'):
            values = principal[key]
            if not isinstance(values, list) or len(values) > 16 or len(set(map(str, values))) != len(values):
                raise ValueError('Invalid team membership')
            for value in values:
                name(value)
        if not set(principal['team_writers']) <= set(principal['teams']):
            raise ValueError('Team writers must be members')
        if type(principal['org_writer']) is not bool or type(principal['enabled']) is not bool:
            raise ValueError('Invalid membership flags')
        if not isinstance(principal['token_hash'], str) or not re.fullmatch(r'[a-f0-9]{64}', principal['token_hash']):
            raise ValueError('Invalid credential digest')
        if principal['token_hash'] in credentials:
            raise ValueError('Credential digests must identify exactly one principal')
        credentials.add(principal['token_hash'])
    return policy


def read_policy(directory):
    directory = _directory(directory)
    path = directory / 'policy.json'
    _private(path)
    if path.stat().st_size > MAX_POLICY_BYTES:
        raise ValueError('Policy exceeds its size limit')
    return _validate_policy(json.loads(path.read_text(encoding='utf-8')))


def write_policy(directory, policy, *, security_update=False):
    _validate_policy(policy)
    serialized = json.dumps(policy, indent=2) + '\n'
    limit = MAX_POLICY_BYTES if security_update else MAX_POLICY_BYTES - POLICY_SECURITY_RESERVE
    if security_update and len(serialized.encode('utf-8')) > limit:
        # The prior supported writer admitted pretty JSON right up to the hard
        # cap. Reclaim its whitespace during rotation/revocation so those valid
        # legacy policies remain administrable without dropping any grants.
        serialized = json.dumps(policy, separators=(',', ':')) + '\n'
    if len(serialized.encode('utf-8')) > limit:
        raise ValueError('Policy exceeds its size limit; existing policy was preserved')
    uw.atomic(directory / 'policy.json', serialized)


def initialize(directory):
    directory = Path(directory).expanduser().absolute()
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        (directory / 'state').mkdir(mode=0o700)
        write_policy(directory, {'schema': 1, 'revision': 0, 'principals': {}})
    except BaseException:
        # Keep failed state for owner inspection rather than deleting unknown files.
        raise
    return {'directory': str(directory), 'principals': 0, 'service_running': False}


@contextmanager
def policy_lock(directory):
    directory = _directory(directory)
    lock = directory / '.policy.lock'
    deadline = time.monotonic() + 5
    while True:
        try:
            lock.mkdir(mode=0o700)
            break
        except (FileExistsError, PermissionError) as error:
            if isinstance(error, PermissionError) and getattr(error, 'winerror', None) != 5:
                raise
            if time.monotonic() >= deadline:
                raise ValueError('Service policy lock is busy or stale')
            time.sleep(0.05)
    try:
        yield directory
    finally:
        lock.rmdir()


def administer(directory, action, user, *, org=None, teams=(), org_writer=False,
               team_writers=(), expected_revision=None):
    user = name(user)
    with policy_lock(directory) as directory:
        policy = read_policy(directory)
        if expected_revision is not None and expected_revision != policy['revision']:
            raise ValueError('Policy revision changed')
        principals = policy['principals']
        token = None
        if action == 'provision':
            if user in principals or len(principals) >= MAX_PRINCIPALS:
                raise ValueError('Principal already exists or capacity reached')
            token = secrets.token_urlsafe(32)
            principals[user] = {'org': name(org), 'teams': sorted(teams), 'org_writer': org_writer,
                                'team_writers': sorted(team_writers), 'enabled': True,
                                'token_hash': hashlib.sha256(token.encode()).hexdigest()}
        else:
            if user not in principals:
                raise ValueError('Unknown principal')
            principal = principals[user]
            if action == 'rotate':
                token = secrets.token_urlsafe(32)
                principal['token_hash'] = hashlib.sha256(token.encode()).hexdigest()
                principal['enabled'] = True
            elif action == 'revoke':
                principal['enabled'] = False
            elif action == 'membership':
                principal.update(org=name(org), teams=sorted(teams), org_writer=org_writer,
                                 team_writers=sorted(team_writers))
            else:
                raise ValueError('Unknown administrative action')
        policy['revision'] += 1
        write_policy(directory, policy, security_update=action in ('rotate', 'revoke'))
        output = {'user': user, 'revision': policy['revision'], 'action': action}
        if token is not None:
            output['token'] = token  # Returned only by explicit local administration.
        return output


class ServiceStore(uw.Store):
    """Service-only quotas and workflow gates; the trusted local CLI is unchanged."""

    def memory_count(self):
        return self.db.execute('SELECT COUNT(*) FROM (SELECT 1 FROM memories LIMIT ?)',
                               (MAX_NAMESPACE_MEMORIES + 1,)).fetchone()[0]

    def check_bounds(self):
        if self.memory_count() > MAX_NAMESPACE_MEMORIES:
            raise ServiceError(413, 'Namespace exceeds the service memory limit')
        oversized = self.db.execute('SELECT 1 FROM memories WHERE length(CAST(payload AS BLOB))>? LIMIT 1',
                                    (MAX_MEMORY_BYTES,)).fetchone()
        if oversized is not None:
            raise ServiceError(413, 'Namespace contains an oversized memory')

    def _put(self, key, payload, expected, actor, scope='project'):
        if len(uw.canon(payload).encode('utf-8')) > MAX_MEMORY_BYTES:
            raise ServiceError(413, 'Memory exceeds the service limit')
        if self.current(key, scope) is None and self.memory_count() >= MAX_NAMESPACE_MEMORIES:
            raise ServiceError(413, 'Namespace memory capacity reached')
        return super()._put(key, payload, expected, actor, scope)

    def recall(self, query='', limit=5, scope='project', include_expired=False):
        # Bound both the scan and retained winners, rather than materializing the
        # entire namespace before slicing the result. Core prepare uses this too.
        uw.require(scope == 'project', 'Service recall only reads its selected namespace')
        uw.require(type(limit) is int and 1 <= limit <= 100, 'Invalid recall limit')
        terms = set(re.findall(r'\w+', query.lower()))
        winners = []
        rows = self.db.execute('SELECT * FROM memories WHERE scope=? ORDER BY updated DESC,key LIMIT ?',
                               (scope, MAX_NAMESPACE_MEMORIES + 1))
        for ordinal, row in enumerate(rows):
            if ordinal >= MAX_NAMESPACE_MEMORIES:
                raise ServiceError(413, 'Namespace exceeds the service memory limit')
            if len(row['payload'].encode('utf-8')) > MAX_MEMORY_BYTES:
                raise ServiceError(413, 'Namespace contains an oversized memory')
            budget = getattr(self, 'scan_budget', None)
            if budget is not None:
                if budget['remaining'] <= 0:
                    raise ServiceError(413, 'Combined namespace scan limit reached')
                budget['remaining'] -= 1
            payload = json.loads(row['payload'])
            if payload.get('retired', False):
                continue
            expiry = payload.get('expires_at')
            if expiry and not include_expired and uw.dt.datetime.fromisoformat(expiry) <= uw.dt.datetime.now(uw.dt.timezone.utc):
                continue
            words = set(re.findall(r'\w+', (row['key'] + ' ' + payload['body']).lower()))
            score = len(terms & words)
            if terms and not score:
                continue
            item = {**dict(row), 'payload': payload}
            entry = (score, -ordinal, item)
            if len(winners) < limit:
                heapq.heappush(winners, entry)
            elif entry[:2] > winners[0][:2]:
                heapq.heapreplace(winners, entry)
            if not terms and len(winners) == limit:
                break
        return [entry[2] for entry in sorted(winners, reverse=True)]

    def expire(self, key, expected, actor='owner', scope='project'):
        def work():
            current = self.current(key, scope)
            uw.require(current is not None, 'Memory not found')
            if current['payload']['kind'] == 'workflow':
                raise ServiceError(403, 'Workflow expiration requires trusted local administration')
            payload = current['payload']
            payload['expires_at'] = uw.now()
            return self._put(key, payload, expected, actor, scope)
        return self.transaction(work)

    def rollback(self, key, revision, expected, actor='owner', scope='project'):
        def work():
            current = self.current(key, scope)
            old = self.db.execute('SELECT payload FROM history WHERE scope=? AND key=? AND revision=?',
                                  (scope, key, revision)).fetchone()
            uw.require(old is not None, 'Revision not found')
            payload = json.loads(old['payload'])
            if payload['kind'] == 'workflow' or (current and current['payload']['kind'] == 'workflow'):
                raise ServiceError(403, 'Workflow rollback requires trusted local administration')
            return self._put(key, payload, expected, actor, scope)
        return self.transaction(work)


class MemoryService:
    def __init__(self, directory, requests_per_minute=120):
        self.directory = _directory(directory)
        read_policy(self.directory)
        if type(requests_per_minute) is not int or not 1 <= requests_per_minute <= 600:
            raise ValueError('Invalid request rate limit')
        self.requests_per_minute = requests_per_minute
        self.rate = defaultdict(deque)
        self.lock = threading.RLock()
        self.scan_budget = None

    def _rate_limit(self, key, limit):
        current = time.monotonic()
        queue = self.rate[key]
        while queue and queue[0] <= current - 60:
            queue.popleft()
        if len(queue) >= limit:
            raise ServiceError(429, 'Request limit reached')
        queue.append(current)

    def authenticate(self, authorization):
        policy = read_policy(self.directory)  # Fresh policy on every request, including revocations.
        if not isinstance(authorization, str) or not authorization.startswith('Bearer '):
            self._rate_limit(('unauthenticated',), 30)
            raise ServiceError(401, 'Authentication required')
        token = authorization[7:]
        if not re.fullmatch(r'[A-Za-z0-9_-]{40,128}', token):
            self._rate_limit(('unauthenticated',), 30)
            raise ServiceError(401, 'Authentication required')
        digest = hashlib.sha256(token.encode()).hexdigest()
        match = None
        for user, principal in policy['principals'].items():
            valid = hmac.compare_digest(digest, principal['token_hash'])
            if valid and principal['enabled']:
                match = (user, principal)
        if match is None:
            self._rate_limit(('unauthenticated',), 30)
            raise ServiceError(401, 'Authentication required')
        self._rate_limit(('principal', match[0]), self.requests_per_minute)
        return policy, *match

    def _target(self, user, principal, scope, namespace, write=False):
        org = principal['org']
        if scope == 'organization':
            if namespace is not None and namespace != org:
                raise ServiceError(403, 'Access denied')
            if write and not principal['org_writer']:
                raise ServiceError(403, 'Access denied')
            return ('organization', org)
        if scope == 'team':
            name(namespace)
            if namespace not in principal['teams'] or (write and namespace not in principal['team_writers']):
                raise ServiceError(403, 'Access denied')
            return ('team', org, namespace)
        if scope not in ('individual', 'scratch') or namespace is not None:
            raise ServiceError(403, 'Access denied')
        return (scope, org, user)

    def _ancestors(self, user, principal, target):
        scope = target[0]
        if scope == 'organization':
            return []
        result = [('organization', principal['org'])]
        if scope in ('individual', 'scratch'):
            result += [('team', principal['org'], team) for team in sorted(principal['teams'])]
        if scope == 'scratch':
            result.append(('individual', principal['org'], user))
        return result

    @contextmanager
    def _store(self, target):
        path = self.directory / 'state'
        for component in target:
            name(component)
            path = path / component
            path.mkdir(mode=0o700, exist_ok=True)
            _private(path, directory=True)
        store = ServiceStore(path, create=True, busy_timeout_seconds=2)
        try:
            store.scan_budget = self.scan_budget
            store.check_bounds()
            yield store
        finally:
            store.close()

    def _binding(self, policy, user, target):
        return {'principal': user, 'policy_revision': policy['revision'], 'target': list(target)}

    def _verify_packet(self, packet, policy, user, target):
        if not isinstance(packet, dict) or packet.get('service_acl') != self._binding(policy, user, target):
            raise ServiceError(403, 'Access denied')

    def _review(self, store, review_id, policy, user, target):
        if not isinstance(review_id, str) or len(review_id) > 100:
            raise ValueError('Invalid review ID')
        row = store.db.execute('SELECT packet FROM reviews WHERE id=?', (review_id,)).fetchone()
        if row is None:
            raise ServiceError(404, 'Not found')
        packet = json.loads(row['packet'])
        self._verify_packet(packet, policy, user, target)
        return packet

    def _proposal(self, store, proposal_id, policy, user, target):
        if not isinstance(proposal_id, str) or len(proposal_id) > 100:
            raise ValueError('Invalid proposal ID')
        row = store.db.execute('SELECT review_id FROM proposals WHERE id=?', (proposal_id,)).fetchone()
        if row is None:
            raise ServiceError(404, 'Not found')
        self._review(store, row['review_id'], policy, user, target)
        return store.proposal(proposal_id)

    def request(self, authorization, route, body):
        # The HTTP server is single-threaded; this lock also serializes direct API callers.
        # Serialize administrative revocation with the full authorized operation,
        # so a grant cannot change between lookup and submit/decision writes.
        with self.lock, policy_lock(self.directory):
            policy, user, principal = self.authenticate(authorization)
            match = ROUTE.fullmatch(route)
            if not match or match[2] not in READ_ACTIONS | WRITE_ACTIONS:
                raise ServiceError(404, 'Not found')
            if not isinstance(body, dict):
                raise ValueError('Expected request object')
            scope, action = match.groups()
            target = self._target(user, principal, scope, body.get('namespace'), action in WRITE_ACTIONS)
            data = dict(body)
            data.pop('namespace', None)
            # All commands use project scope inside a physically separate namespace store.
            self.scan_budget = {'remaining': MAX_RECALL_SCAN}
            try:
                with self._store(target) as store:
                    return self._dispatch(store, policy, user, principal, target, action, data)
            finally:
                self.scan_budget = None

    def _dispatch(self, store, policy, user, principal, target, action, data):
        if action == 'status':
            fields(data)
            status = store.status()
            return {key: status[key] for key in ('version', 'project_id', 'memories', 'events', 'pending_proposals')}
        if action == 'recall':
            fields(data, optional=('query', 'limit', 'include_inherited'))
            query, limit = data.get('query', ''), data.get('limit', 5)
            if not isinstance(query, str) or len(query) > 4000 or type(limit) is not int or not 1 <= limit <= 20:
                raise ValueError('Invalid recall bounds')
            inherited = data.get('include_inherited', True)
            if type(inherited) is not bool:
                raise ValueError('Invalid inheritance option')
            results = [{'namespace': list(target), 'memory': item} for item in store.recall(query, limit)]
            if inherited:
                for ancestor in self._ancestors(user, principal, target):
                    remaining = limit - len(results)
                    if not remaining:
                        break
                    with self._store(ancestor) as parent:
                        results += [{'namespace': list(ancestor), 'memory': item} for item in parent.recall(query, remaining)]
            return results
        if action == 'current':
            fields(data, ('key',))
            return store.current(uw.slug(data['key']))
        if action == 'history':
            fields(data, ('key',), ('limit', 'cursor'))
            key, limit = uw.slug(data['key']), data.get('limit', MAX_HISTORY_PAGE_ITEMS)
            if type(limit) is not int or not 1 <= limit <= MAX_HISTORY_PAGE_ITEMS:
                raise ValueError('Invalid history limit')
            cursor = data.get('cursor')
            if 'cursor' in data and (type(cursor) is not int or not 1 <= cursor <= MAX_HISTORY_CURSOR):
                raise ValueError('Invalid history cursor')
            query = 'SELECT * FROM history WHERE scope=? AND key=?'
            parameters = ['project', key]
            if cursor is not None:
                query += ' AND revision<?'
                parameters.append(cursor)
            rows = store.db.execute(query + ' ORDER BY revision DESC LIMIT ?', (*parameters, limit))
            result, size = [], len(b'{"result":[]}')
            budget = min(MAX_HISTORY_PAGE_BYTES, MAX_RESPONSE)
            for row in rows:
                item = {**dict(row), 'payload': json.loads(row['payload'])}
                # Match Handler._send's escaped JSON, including envelope and commas.
                item_size = len(json.dumps(item, ensure_ascii=True, separators=(',', ':')).encode('ascii'))
                next_size = size + item_size + bool(result)
                if next_size > budget:
                    if not result:
                        raise ServiceError(413, 'History revision exceeds the service response limit')
                    break
                result.append(item)
                size = next_size
            # Keep the original list response. The last revision is the exclusive
            # cursor for the next page; an empty page marks the end of history.
            return result
        if action == 'remember':
            fields(data, ('key', 'body', 'evidence'), ('expected_hash', 'kind', 'ttl_days'))
            return store.put(data['key'], data['body'], data['evidence'], data.get('expected_hash', 'NEW'),
                             data.get('kind', 'lesson'), data.get('ttl_days', 90), actor='principal:' + user)
        if action == 'record':
            fields(data, ('event',))
            event = dict(data['event']) if isinstance(data['event'], dict) else None
            fields(event, ('session', 'status', 'summary'), ('id', 'evidence', 'tool', 'error_tag', 'domain', 'duration_seconds', 'tokens', 'cost'))
            session = uw.text_field(event['session'], 'session', 120)
            event['session'] = user + ':' + session
            if 'id' in event:
                raw_id = uw.text_field(event['id'], 'event id', 100)
                event['id'] = hashlib.sha256((user + ':' + raw_id).encode()).hexdigest()
            return store.record(event)
        if action in ('expire', 'rollback'):
            required = ('key', 'expected_hash', 'revision') if action == 'rollback' else ('key', 'expected_hash')
            fields(data, required)
            if action == 'expire':
                return store.expire(data['key'], data['expected_hash'], actor='principal:' + user)
            if type(data['revision']) is not int or data['revision'] < 1:
                raise ValueError('Invalid revision')
            return store.rollback(data['key'], data['revision'], data['expected_hash'], actor='principal:' + user)
        if action == 'review-prepare':
            fields(data, optional=('force',))
            if type(data.get('force', False)) is not bool:
                raise ValueError('Invalid force flag')
            packet = store.prepare(data.get('force', False))
            if packet.get('due') is False:
                return packet
            try:
                inherited, budget = [], 65_536
                for ancestor in self._ancestors(user, principal, target):
                    with self._store(ancestor) as parent:
                        for item in parent.recall(limit=5):
                            value = {'namespace': list(ancestor), 'memory': item}
                            size = len(uw.canon(value).encode())
                            if size <= budget:
                                inherited.append(value)
                                budget -= size
                # Ancestors are context only, never writable targets/evidence in this packet.
                packet['inherited_context'] = inherited
                packet['service_acl'] = self._binding(policy, user, target)
                packet['inheritance_rule'] = 'Read-only ancestor context; proposals may modify only this target namespace.'
                # JSON escaping can expand astral Unicode to twelve bytes per
                # character. Fit the actual request representation, preserving
                # remaining event eligibility by updating BOTH watermarks.
                while len(json.dumps({'packet': packet, 'response': {'proposals': []}}, ensure_ascii=True).encode()) > MAX_REVIEW_PACKET_BYTES:
                    if packet['inherited_context']:
                        packet['inherited_context'].pop()
                    elif packet['memories']:
                        packet['memories'].pop()
                    elif len(packet['events']) > 1:
                        packet['events'].pop()
                        packet['watermark'] = packet['events'][-1]['seq']
                    else:
                        raise ServiceError(413, 'Review event exceeds the service transport limit')
                def save():
                    row = store.db.execute('SELECT status FROM reviews WHERE id=?', (packet['review_id'],)).fetchone()
                    if row is None or row['status'] != 'prepared':
                        raise ValueError('Review changed during preparation')
                    store.db.execute('UPDATE reviews SET packet=?,watermark=? WHERE id=?',
                                     (uw.canon(packet), packet['watermark'], packet['review_id']))
                store.transaction(save)
            except BaseException:
                store.db.execute("DELETE FROM reviews WHERE id=? AND status='prepared'", (packet['review_id'],))
                store.db.commit()
                raise
            return packet
        if action == 'review-get':
            fields(data, ('review_id',))
            return self._review(store, data['review_id'], policy, user, target)
        if action == 'review-submit':
            fields(data, ('packet', 'response'))
            self._verify_packet(data['packet'], policy, user, target)
            self._review(store, data['packet'].get('review_id'), policy, user, target)
            return store.submit(data['packet'], data['response'])
        if action == 'proposal-get':
            fields(data, ('id',))
            return self._proposal(store, data['id'], policy, user, target)
        if action == 'proposals':
            fields(data)
            results = []
            rows = store.db.execute("SELECT id FROM proposals WHERE status='pending' ORDER BY rowid DESC LIMIT 1000").fetchall()
            for row in rows:
                try:
                    proposal = self._proposal(store, row['id'], policy, user, target)
                except ServiceError:
                    continue
                results.append(proposal)
                if len(results) >= 100:
                    break
            return results
        if action == 'proposal-decide':
            fields(data, ('id', 'accept'))
            if type(data['accept']) is not bool:
                raise ValueError('Invalid decision')
            self._proposal(store, data['id'], policy, user, target)
            return store.decide(data['id'], accept=data['accept'], actor='principal:' + user)
        raise ServiceError(404, 'Not found')


class ServiceHTTPServer(HTTPServer):
    request_queue_size = 8
    allow_reuse_address = False

    def __init__(self, directory, port=0, requests_per_minute=120):
        self.service = MemoryService(directory, requests_per_minute)
        self.connection_deadlines = {}
        super().__init__(('127.0.0.1', port), Handler)

    def get_request(self):
        request, address = super().get_request()
        request.settimeout(CONNECTION_TIMEOUT)
        def expire():
            try:
                request.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        # An absolute deadline also bounds clients trickling bytes frequently
        # enough to avoid a per-read inactivity timeout. At most one timer runs.
        timer = threading.Timer(CONNECTION_TIMEOUT, expire)
        timer.daemon = True
        self.connection_deadlines[request] = timer
        timer.start()
        return request, address

    def shutdown_request(self, request):
        timer = self.connection_deadlines.pop(request, None)
        if timer is not None:
            timer.cancel()
            timer.join(timeout=1)
        super().shutdown_request(request)

    def handle_error(self, request, client_address):
        # Never dump a request, credential, or project path in traceback logs.
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = 'UniversalWorkflowService/0.1'
    sys_version = ''
    protocol_version = 'HTTP/1.0'

    def log_message(self, format, *args):
        pass

    def _send(self, status, body):
        output = json.dumps(body, ensure_ascii=True, separators=(',', ':')).encode()
        if len(output) > MAX_RESPONSE:
            status, output = 413, b'{"error":"Response limit reached"}'
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(output)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        self.end_headers()
        self.wfile.write(output)
        self.close_connection = True

    def do_POST(self):
        try:
            if self.headers.get('Transfer-Encoding') is not None or len(self.headers.get_all('Content-Length', [])) != 1:
                raise ServiceError(400, 'Invalid request framing')
            length = self.headers['Content-Length']
            if not re.fullmatch(r'[0-9]{1,9}', length):
                raise ServiceError(400, 'Invalid request framing')
            length = int(length)
            if not 0 < length <= MAX_BODY:
                raise ServiceError(413, 'Request limit reached')
            if len(self.headers.get_all('Authorization', [])) != 1:
                raise ServiceError(401, 'Authentication required')
            if self.headers.get('Content-Type', '').split(';', 1)[0].strip() != 'application/json':
                raise ServiceError(415, 'JSON required')
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ServiceError(400, 'Incomplete request')
            data = json.loads(raw.decode('utf-8'))
            output = self.server.service.request(self.headers['Authorization'], self.path, data)
            self._send(200, {'result': output})
        except ServiceError as error:
            self._send(error.status, {'error': error.message})
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
            self._send(400, {'error': 'Invalid request'})
        except (OSError, sqlite3.Error):
            self._send(503, {'error': 'Service unavailable'})

    def do_GET(self):
        self._send(405, {'error': 'POST required'})

    do_PUT = do_DELETE = do_PATCH = do_GET


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for action in ('init', 'provision', 'membership', 'rotate', 'revoke', 'serve'):
        command = sub.add_parser(action)
        command.add_argument('--directory', required=True)
        if action in ('provision', 'membership', 'rotate', 'revoke'):
            command.add_argument('--user', required=True)
            command.add_argument('--expected-revision', type=int)
        if action in ('provision', 'membership'):
            command.add_argument('--org', required=True)
            command.add_argument('--team', action='append', default=[])
            command.add_argument('--org-writer', action='store_true')
            command.add_argument('--team-writer', action='append', default=[])
        if action == 'serve':
            command.add_argument('--port', type=int, default=8765)
            command.add_argument('--requests-per-minute', type=int, default=120)
    args = parser.parse_args(argv)
    try:
        if args.action == 'init':
            result = initialize(args.directory)
        elif args.action == 'serve':
            if not 0 <= args.port <= 65535:
                raise ValueError('Invalid port')
            server = ServiceHTTPServer(args.directory, args.port, args.requests_per_minute)
            try:
                print(json.dumps({'listening': '127.0.0.1', 'port': server.server_port}), flush=True)
                server.serve_forever()
            finally:
                server.server_close()
            return 0
        else:
            options = {'expected_revision': args.expected_revision}
            if args.action in ('provision', 'membership'):
                options.update(org=args.org, teams=args.team, org_writer=args.org_writer, team_writers=args.team_writer)
            result = administer(args.directory, args.action, args.user, **options)
        print(json.dumps(result, indent=2))
        return 0
    except (ValueError, OSError, sqlite3.Error) as error:
        print(json.dumps({'error': uw.redact(error)}), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0


if __name__ == '__main__':
    sys.exit(main())
