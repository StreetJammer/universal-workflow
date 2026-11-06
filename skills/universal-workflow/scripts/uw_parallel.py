"""Bounded, proposal-only orchestration over independent session shards."""
from __future__ import annotations

import concurrent.futures
import copy
import threading
import time

import uw


def shard_packet(packet, workers):
    """Keep each session in one shard, preserving original event order."""
    uw.require(type(workers) is int and 1 <= workers <= 4, 'workers must be 1..4')
    events = packet.get('events')
    uw.require(isinstance(events, list) and 1 <= len(events) <= 100, 'Need 1..100 events to shard')
    uw.require(len(uw.canon(packet).encode('utf-8')) <= 2_000_000, 'Review packet exceeds 2 MB')
    sessions = {}
    event_ids = set()
    for event in events:
        uw.require(isinstance(event, dict) and isinstance(event.get('session'), str)
                   and event['session'] and isinstance(event.get('id'), str), 'Invalid shard event')
        uw.require(event['id'] not in event_ids, 'Duplicate event in review packet')
        event_ids.add(event['id'])
        sessions.setdefault(event['session'], []).append(event)
    groups = [[] for _ in range(min(workers, len(sessions)))]
    assigned = {}
    for session in sorted(sessions):
        index = min(range(len(groups)), key=lambda i: (len(groups[i]), i))
        groups[index].extend(sessions[session])
        assigned[session] = index
    shards = []
    for index in range(len(groups)):
        shard = copy.deepcopy(packet)
        shard['events'] = [copy.deepcopy(event) for event in events if assigned[event['session']] == index]
        shard['parallel_shard'] = {'index': index, 'count': len(groups),
                                   'evidence_scope': 'Only events in this shard may be cited.'}
        shards.append(shard)
    return shards


def normalize_response(shard, response):
    """Reject invalid evidence before results from separate workers are combined."""
    uw.require(isinstance(response, dict) and isinstance(response.get('proposals'), list), 'Expected proposals array')
    uw.require(len(response['proposals']) <= 10, 'Maximum ten proposals per reviewer')
    allowed = {event['id'] for event in shard['events']}
    proposals = []
    for item in response['proposals']:
        uw.require(isinstance(item, dict), 'Proposal must be an object')
        key = uw.slug(item.get('key'))
        kind = item.get('kind')
        uw.require(kind in uw.KINDS, 'Invalid proposal kind')
        body = uw.text_field(item.get('body'), 'body')
        reason = uw.text_field(item.get('reason'), 'reason', 2000)
        ids = item.get('evidence_event_ids')
        uw.require(isinstance(ids, list) and 0 < len(ids) <= 20
                   and all(isinstance(i, str) and i in allowed for i in ids),
                   'Reviewer evidence must reference its own shard')
        ttl = item.get('ttl_days', 90)
        uw.require(type(ttl) is int and 1 <= ttl <= 3650, 'Proposal TTL must be 1..3650 days')
        proposals.append({'key': key, 'kind': kind, 'body': body, 'reason': reason,
                          'ttl_days': ttl, 'evidence_event_ids': sorted(set(ids))})
    return proposals


def aggregate_responses(packet, shards, responses):
    """Merge exact agreements and expose every incompatible choice for reconciliation."""
    uw.require(len(shards) == len(responses) and 1 <= len(shards) <= 4, 'Missing reviewer responses')
    all_events = {event['id']: event for event in packet['events']}
    covered = [event['id'] for shard in shards for event in shard['events']]
    uw.require(len(covered) == len(set(covered)) and set(covered) == set(all_events),
               'Shards must cover every event exactly once')
    groups = {}
    for index, (shard, response) in enumerate(zip(shards, responses)):
        for item in normalize_response(shard, response):
            identity = (item['key'], item['kind'], item['body'], item['ttl_days'])
            group = groups.setdefault(identity, {'item': item, 'evidence': set(), 'reasons': set(), 'shards': set()})
            group['evidence'].update(item['evidence_event_ids'])
            group['reasons'].add(item['reason'])
            group['shards'].add(index)
    candidates = []
    coverage = []
    conflicts = []
    by_key = {}
    by_body = {}
    for identity in sorted(groups):
        group = groups[identity]
        evidence = sorted(group['evidence'])
        # Reasons are not used as votes. Keep a deterministic supplied reason;
        # evidence and shard provenance retain the union of actual observations.
        item = {**group['item'], 'reason': min(group['reasons']), 'evidence_event_ids': evidence}
        by_key.setdefault(item['key'], []).append(item)
        by_body.setdefault((item['kind'], item['body']), set()).add(item['key'])
        candidates.append(item)
        coverage.append({'key': item['key'], 'candidate_hash': uw.digest(item),
                         'reviewer_shards': sorted(group['shards']),
                         'matched_sessions': len({all_events[i]['session'] for i in evidence}),
                         'total_sessions': len({e['session'] for e in packet['events']})})
        if len(evidence) > 20:
            conflicts.append({'kind': 'evidence_limit', 'key': item['key'],
                              'message': 'Combined evidence exceeds 20 references; narrow the candidate explicitly.'})
    for key in sorted(by_key):
        if len(by_key[key]) > 1:
            conflicts.append({'kind': 'incompatible_candidates', 'key': key, 'alternatives': by_key[key]})
    for identity, keys in sorted(by_body.items()):
        if len(keys) > 1:
            conflicts.append({'kind': 'duplicate_body_keys', 'keys': sorted(keys),
                              'message': 'Identical content has multiple target keys; choose the intended target explicitly.'})
    maximum = packet.get('review_policy', {}).get('policy', {}).get('limits', {}).get('max_proposals', 10)
    uw.require(type(maximum) is int and 0 <= maximum <= 10, 'Invalid review policy max_proposals')
    if len(candidates) > maximum:
        conflicts.append({'kind': 'proposal_limit', 'count': len(candidates), 'maximum': maximum,
                          'message': 'Too many distinct candidates; select a bounded response explicitly.'})
    return {'proposals': candidates, 'conflicts': conflicts, 'coverage': coverage,
            'total_events': len(all_events), 'total_sessions': len({e['session'] for e in packet['events']})}


def run_parallel(store, engine=None, force=False, workers=2, retries=0, timeout=180):
    """Run at most four reviewers, with one deadline and no worker Store access."""
    uw.require(type(workers) is int and 1 <= workers <= 4, 'workers must be 1..4')
    uw.require(type(retries) is int and 0 <= retries <= 2, 'retries must be 0..2')
    uw.require(type(timeout) is int and 1 <= timeout <= 300, 'timeout must be 1..300 seconds')
    engine = engine or store.config.get('review_engine')
    uw.require(engine in ('claude', 'codex'), 'Select --engine claude|codex or set review_engine')
    uw.require(uw.shutil.which(engine) is not None, f'{engine} CLI is not installed')
    token = store.lease('dream', seconds=timeout + 30)
    try:
        packet = store.prepare(force)
        if packet.get('due') is False:
            return packet
        shards = shard_packet(packet, workers)
        cancelled = threading.Event()
        deadline = time.monotonic() + timeout

        def review(shard):
            for attempt in range(1, retries + 2):
                uw.require(not cancelled.is_set() and time.monotonic() < deadline,
                           'Parallel review cancelled or timed out')
                try:
                    response = uw.review_packet(shard, engine, timeout,
                                                cancel_event=cancelled, deadline=deadline)
                    normalized = normalize_response(shard, response)
                    return {'proposals': normalized}, attempt
                except (ValueError, OSError, UnicodeError) as error:
                    if cancelled.is_set() or time.monotonic() >= deadline or attempt > retries:
                        raise ValueError(f"Reviewer shard {shard['parallel_shard']['index']} failed after {attempt} attempt(s): {uw.redact(error)}") from error
            raise AssertionError('Unreachable retry state')

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(shards), thread_name_prefix='uw-review')
        futures = []
        try:
            futures = [pool.submit(review, shard) for shard in shards]
            done, pending = concurrent.futures.wait(futures, timeout=max(0, deadline-time.monotonic()),
                                                     return_when=concurrent.futures.FIRST_EXCEPTION)
            for future in futures:
                if future in done:
                    future.result()  # Raise worker failure before considering any proposals.
            uw.require(not pending and time.monotonic() <= deadline, 'Parallel review timed out; no proposals submitted')
            results = [future.result() for future in futures]
        finally:
            cancelled.set()
            for future in futures:
                future.cancel()
            pool.shutdown(wait=True, cancel_futures=True)

        aggregate = aggregate_responses(packet, shards, [response for response, _ in results])
        stats = {'workers': len(shards), 'attempts': [attempts for _, attempts in results],
                 'total_calls': sum(attempts for _, attempts in results),
                 'total_events': aggregate['total_events'], 'total_sessions': aggregate['total_sessions'],
                 'coverage': aggregate['coverage']}
        if aggregate['conflicts']:
            return {'review_id': packet['review_id'], 'status': 'needs_reconciliation', 'promoted': False,
                    'submitted': False, 'packet': packet, 'response': {'proposals': aggregate['proposals']},
                    'conflicts': aggregate['conflicts'], 'statistics': stats}
        proposals = store.submit(packet, {'proposals': aggregate['proposals']})
        return {'review_id': packet['review_id'], 'status': 'submitted', 'promoted': False,
                'submitted': True, 'proposals': proposals, 'statistics': stats}
    finally:
        store.release('dream', token)
