#!/usr/bin/env python3
"""Explicit, bounded session-trace import. No native history discovery or network IO."""
from __future__ import annotations

import json
from pathlib import Path
import re

from uw import SECRET, canon, digest, require, text_field

MAX_INPUT_BYTES = 1_000_000
MAX_TURNS = 100
MAX_EVENT_BYTES = 50_000
MAX_DEPTH = 8
MAX_ITEMS = 10_000
ROLES = ('user', 'assistant', 'system', 'tool')
STATUSES = ('success', 'failure', 'blocked', 'unverified', 'observed')
# Whole sensitive fields are replaced, rather than retaining a key/value prefix
# that could be mistaken for a real credential by the store's final validator.
PEM = re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)')
AUTH = re.compile(r'(?i)\b(?:bearer|basic)\s+[a-z0-9+/_.=~-]+')
HEADERS = re.compile(r'(?im)^\s*(?:authorization|cookie|set-cookie|x-api-key)\s*:[^\r\n]*')
ASSIGNMENT = re.compile(
    r'''(?ix)["']?\b(?:password|passwd|secret|api[_-]?key|client[_-]?secret|access[_-]?token|refresh[_-]?token|token|auth|authorization|cookie|set-cookie|session[_-]?(?:id|key|token)|private[_-]?key)\b["']?\s*[:=]\s*(?:\[REDACTED\]|"(?:\\.|[^"\\\r\n])*"|'(?:\\.|[^'\\\r\n])*'|[^\r\n,;&}\]]+)''')
URL_AUTH = re.compile(r'(?i)(https?://)[^\s/@:]+:[^\s/@]+@')
URL_QUERY = re.compile(r'(?i)(https?://[^\s?#]+)[?#][^\s]*')
EMAIL = re.compile(r'(?i)\b[a-z0-9.!#$%&*+/=?^_`{|}~-]+@[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}\b')
HOME = re.compile(r'(?i)(?:[a-z]:\\Users\\[^\\\s]+|/(?:home|Users)/[^/\s]+)')


def bounded_document(document):
    """Reject excessive depth/counts before JSON serialization; never log input."""
    stack = [(document, 0)]
    count = 0
    while stack:
        value, depth = stack.pop()
        count += 1
        require(depth <= MAX_DEPTH and count <= MAX_ITEMS, 'Trace input exceeds structural limits')
        if type(value) is dict:
            require(all(type(key) is str for key in value), 'Trace object keys must be strings')
            stack.extend((child, depth + 1) for child in value.values())
        elif type(value) is list:
            stack.extend((child, depth + 1) for child in value)
        else:
            require(type(value) in (str, int, bool, type(None)), 'Trace values must use the documented JSON types')
    try:
        encoded = canon(document).encode('utf-8')
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('Trace input is not valid bounded UTF-8 JSON') from error
    require(len(encoded) <= MAX_INPUT_BYTES, 'Trace input exceeds 1 MB')


def load_session(path):
    """Read only the explicitly selected UTF-8 JSON file, at most 1 MB."""
    try:
        with Path(path).open('rb') as source:
            data = source.read(MAX_INPUT_BYTES + 1)
    except OSError as error:
        raise ValueError('Unable to read selected trace file') from error
    require(len(data) <= MAX_INPUT_BYTES, 'Trace input exceeds 1 MB')
    try:
        document = json.loads(data.decode('utf-8'))
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ValueError('Trace input must be bounded UTF-8 JSON') from error
    bounded_document(document)
    return document


def shape(value, required, optional=()):
    require(type(value) is dict, 'Trace record must be an object')
    keys = set(value)
    require(set(required) <= keys and keys <= set(required) | set(optional), 'Trace record has missing or unsupported fields')


def sanitized(value, name, limit):
    require(type(value) is str and value.strip() and len(value) <= limit,
            f'{name} must be nonempty text, maximum {limit} characters')
    value = PEM.sub('[REDACTED]', value)
    value = URL_AUTH.sub(r'\1[REDACTED]@', value)
    value = URL_QUERY.sub(r'\1?[REDACTED]', value)
    value = HEADERS.sub('[REDACTED]', value)
    value = AUTH.sub('[REDACTED]', value)
    value = ASSIGNMENT.sub('[REDACTED]', value)
    value = SECRET.sub('[REDACTED]', value)
    value = EMAIL.sub('[REDACTED_EMAIL]', value)
    value = HOME.sub('[REDACTED_HOME]', value)
    # Avoid ambiguous control bytes in exported text and prompts.
    value = ''.join(c if c in '\n\r\t' or ord(c) >= 32 else '\ufffd' for c in value)
    return text_field(value, name, max(limit, len(value)))


def text_list(value, name, maximum, text_limit):
    require(type(value) is list and len(value) <= maximum, f'Invalid {name} count')
    return [sanitized(item, name, text_limit) for item in value]


def validate_trace(trace, project_id):
    """Validate/redact nested event evidence, including direct record() calls."""
    bounded_document(trace)
    shape(trace, ('schema', 'project_id', 'source', 'session_id', 'turn_id',
                  'messages', 'tool_calls', 'skills_used', 'user_corrections'))
    require(type(trace['schema']) is int and trace['schema'] == 1, 'Unsupported trace schema')
    require(trace['project_id'] == project_id, 'Trace project mismatch')
    normalized = {'schema': 1, 'project_id': project_id,
                  'source': sanitized(trace['source'], 'source', 500),
                  'session_id': sanitized(trace['session_id'], 'session_id', 200),
                  'turn_id': sanitized(trace['turn_id'], 'turn_id', 100)}
    messages = trace['messages']
    require(type(messages) is list and len(messages) <= 20, 'Maximum 20 selected messages per turn')
    normalized['messages'] = []
    for message in messages:
        shape(message, ('role', 'content'))
        require(message['role'] in ROLES, 'Unsupported message role; do not import hidden reasoning')
        normalized['messages'].append({'role': message['role'], 'content': sanitized(message['content'], 'message', 4000)})
    calls = trace['tool_calls']
    require(type(calls) is list and len(calls) <= 20, 'Maximum 20 selected tool calls per turn')
    normalized['tool_calls'] = []
    for call in calls:
        shape(call, ('name', 'input_summary', 'output_summary'))
        normalized['tool_calls'].append({'name': sanitized(call['name'], 'tool name', 200),
                                        'input_summary': sanitized(call['input_summary'], 'tool input summary', 2000),
                                        'output_summary': sanitized(call['output_summary'], 'tool output summary', 2000)})
    normalized['skills_used'] = text_list(trace['skills_used'], 'skills_used', 20, 200)
    normalized['user_corrections'] = text_list(trace['user_corrections'], 'user_corrections', 20, 2000)
    require(len(canon(normalized).encode('utf-8')) <= MAX_EVENT_BYTES - 6000, 'Selected turn exceeds evidence byte budget')
    return normalized


def import_session(store, document):
    """Validate/sanitize all turns before inserting one atomic, idempotent batch."""
    bounded_document(document)
    shape(document, ('schema', 'project_id', 'source', 'session_id', 'turns'))
    require(type(document['schema']) is int and document['schema'] == 1, 'Unsupported trace schema')
    require(document['project_id'] == store.config['project_id'], 'Trace project mismatch')
    source = sanitized(document['source'], 'source', 500)
    session_id = sanitized(document['session_id'], 'session_id', 200)
    turns = document['turns']
    require(type(turns) is list and 1 <= len(turns) <= MAX_TURNS, 'Trace needs 1..100 selected turns')
    normalized = []
    identities = set()
    session = 'trace-session-' + digest([store.config['project_id'], source, session_id])[:40]
    for turn in turns:
        shape(turn, ('id', 'status', 'summary'), ('messages', 'tool_calls', 'skills_used', 'user_corrections'))
        require(turn['status'] in STATUSES, 'Invalid trace status')
        trace = validate_trace({'schema': 1, 'project_id': store.config['project_id'], 'source': source,
                                'session_id': session_id, 'turn_id': turn['id'],
                                **{field: turn.get(field, []) for field in ('messages', 'tool_calls', 'skills_used', 'user_corrections')}},
                               store.config['project_id'])
        eid = 'trace-' + digest([store.config['project_id'], source, session_id, trace['turn_id']])
        require(eid not in identities, 'Duplicate turn identity in trace input')
        identities.add(eid)
        event = {'id': eid, 'session': session, 'status': turn['status'],
                 'summary': sanitized(turn['summary'], 'summary', 4000),
                 'evidence': ['selected-trace:' + eid], 'trace': trace}
        validated = store._event_payload(event)
        require(len(canon(validated[1]).encode('utf-8')) <= MAX_EVENT_BYTES, 'Selected turn exceeds event byte budget')
        normalized.append(validated)
    # Neither raw document nor unsanitized hashes/content are persisted.
    def write_batch():
        records = [store._record_event(eid, payload) for eid, payload in normalized]
        return {'project_id': store.config['project_id'], 'source': source, 'session_id': session_id,
                'imported': sum(not row.get('duplicate', False) for row in records),
                'duplicates': sum(row.get('duplicate', False) for row in records),
                'events': records, 'sanitized': True}
    return store.transaction(write_batch)
