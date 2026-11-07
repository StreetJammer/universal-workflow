"""Readiness regression tests; fixtures are not live GitHub/model verification."""
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pr_watch', Path(__file__).resolve().parents[1] / 'scripts/pr_watch.py')
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


def fixture():
    review = dict(commit_sha='a' * 40, status='completed', has_any_findings=False,
                  has_unresolved_findings=False)
    return dict(number=12, url='https://github.com/example/repo/pull/12', state='OPEN',
                isDraft=False, headRefOid='a' * 40, baseRefOid='b' * 40,
                mergeable='MERGEABLE', mergeStateStatus='CLEAN', reviewDecision=None,
                comments=dict(pageInfo=dict(hasPreviousPage=False), nodes=[dict(
                    databaseId=1, updatedAt='2026-11-07T12:00:00Z',
                    author=dict(login='chatgpt-codex-connector', __typename='Bot'),
                    body='<!-- codex-review-state:v1 ' + json.dumps(dict(code_review=review,
                        security_review=review.copy())) + ' -->')]),
                reviewThreads=dict(pageInfo=dict(hasNextPage=False), nodes=[]),
                latestReviews=dict(pageInfo=dict(hasNextPage=False), nodes=[]),
                commits=dict(nodes=[dict(commit=dict(oid='a' * 40, statusCheckRollup=dict(
                    contexts=dict(pageInfo=dict(hasNextPage=False), nodes=[dict(
                        __typename='CheckRun', name='test (ubuntu, 3.12)', status='COMPLETED',
                        conclusion='SUCCESS', detailsUrl='https://github.com/example/repo/actions/runs/1')]))))]))


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.pr = fixture()
        self.checks = self.pr['commits']['nodes'][0]['commit']['statusCheckRollup']['contexts']

    def result(self):
        return w.evaluate(self.pr)['status']

    def test_ready_requires_current_evidence(self):
        self.assertEqual(self.result(), 'ready')
        self.pr['headRefOid'] = 'c' * 40
        self.assertEqual(self.result(), 'pending')

    def test_fake_bot_marker_is_not_trusted(self):
        self.pr['comments']['nodes'][0]['author']['__typename'] = 'User'
        self.assertEqual(self.result(), 'pending')

    def test_other_bot_is_not_trusted(self):
        self.pr['comments']['nodes'][0]['author']['login'] = 'someone-else[bot]'
        self.assertEqual(self.result(), 'pending')

    def test_newer_pending_or_malformed_summary_supersedes_clean(self):
        for body in ['<!-- codex-review-state:v1 broken -->',
                     '<!-- codex-review-state:v1 {"code_review":{"status":"in_progress"}} -->']:
            with self.subTest(body=body):
                self.pr['comments']['nodes'].append(dict(databaseId=2, updatedAt='2026-11-07T12:01:00Z',
                    author=dict(login='chatgpt-codex-connector', __typename='Bot'), body=body))
                self.assertEqual(self.result(), 'pending')
                self.pr['comments']['nodes'].pop()

    def test_unsupported_or_incomplete_summary_never_reuses_old_clean(self):
        clean = self.pr['comments']['nodes'][0]['body']
        for body in [clean.replace('codex-review-state:v1', 'codex-review-state:v2'),
                     clean.removesuffix(' -->'),
                     '<!-- codex-pull-request-review-summary --> Review running']:
            with self.subTest(body=body):
                self.pr['comments']['nodes'].append(dict(databaseId=2, updatedAt='2026-11-07T12:01:00Z',
                    author=dict(login='chatgpt-codex-connector', __typename='Bot'), body=body))
                self.assertEqual(self.result(), 'pending')
                self.pr['comments']['nodes'].pop()

    def test_findings_block(self):
        c = self.pr['comments']['nodes'][0]
        c['body'] = c['body'].replace('"has_any_findings": false', '"has_any_findings": true')
        self.assertEqual(self.result(), 'blocked')

    def test_unknown_security_is_not_clean(self):
        c = self.pr['comments']['nodes'][0]
        data = json.loads(w.MARKER.search(c['body']).group(1))
        data['security_review']['has_any_findings'] = None
        c['body'] = '<!-- codex-review-state:v1 ' + json.dumps(data) + ' -->'
        self.assertEqual(self.result(), 'ready')
        self.assertEqual(w.evaluate(self.pr, require_security=True)['status'], 'pending')

    def test_missing_and_skipped_only_ci_never_ready(self):
        self.checks['nodes'][0]['conclusion'] = 'SKIPPED'
        self.assertEqual(self.result(), 'pending')
        self.checks['nodes'] = []
        self.assertEqual(self.result(), 'pending')

    def test_check_failure_and_pending(self):
        for state, expected in [('FAILURE', 'blocked'), ('CANCELLED', 'blocked'),
                                ('TIMED_OUT', 'blocked'), (None, 'pending')]:
            with self.subTest(state=state):
                self.checks['nodes'][0]['conclusion'] = state
                self.assertEqual(self.result(), expected)

    def test_rerun_in_progress_does_not_use_old_success(self):
        self.checks['nodes'][0]['status'] = 'IN_PROGRESS'
        self.assertEqual(self.result(), 'pending')

    def test_status_context_failure(self):
        self.checks['nodes'].append(dict(__typename='StatusContext', context='external-ci', state='ERROR'))
        self.assertEqual(self.result(), 'blocked')

    def test_expected_check_must_exist_and_succeed(self):
        self.assertEqual(w.evaluate(self.pr, ['missing'])['status'], 'pending')
        self.checks['nodes'].append(dict(__typename='CheckRun', name='required', status='COMPLETED', conclusion='SKIPPED'))
        self.assertEqual(w.evaluate(self.pr, ['required'])['status'], 'pending')

    def test_draft_conflict_and_required_approval(self):
        self.pr['isDraft'] = True
        self.assertEqual(self.result(), 'pending')
        self.pr['isDraft'] = False
        self.pr['reviewDecision'] = 'REVIEW_REQUIRED'
        self.assertEqual(self.result(), 'pending')
        self.pr['mergeable'] = 'CONFLICTING'
        self.assertEqual(self.result(), 'blocked')

    def test_unresolved_or_changes_requested_blocks(self):
        self.pr['reviewThreads']['nodes'] = [dict(isResolved=False)]
        self.assertEqual(self.result(), 'blocked')
        self.pr['reviewThreads']['nodes'] = []
        self.pr['latestReviews']['nodes'] = [dict(state='CHANGES_REQUESTED')]
        self.assertEqual(self.result(), 'blocked')

    def test_truncated_evidence_never_ready(self):
        for collection in [self.checks, self.pr['reviewThreads'], self.pr['latestReviews']]:
            collection['pageInfo']['hasNextPage'] = True
            self.assertEqual(self.result(), 'pending')
            collection['pageInfo']['hasNextPage'] = False

    def test_truncated_comments_never_ready(self):
        self.pr["comments"]["pageInfo"]["hasPreviousPage"] = True
        self.assertEqual(self.result(), "pending")

    def test_merge_state_blocked_or_unknown(self):
        for state in ['BLOCKED', 'BEHIND', 'UNKNOWN', 'UNSTABLE']:
            self.pr['mergeStateStatus'] = state
            self.assertEqual(self.result(), 'pending')

    def test_merged_and_closed(self):
        for state in ['MERGED', 'CLOSED']:
            self.pr['state'] = state
            self.assertEqual(self.result(), state.lower())

    def test_head_and_base_race_are_pending(self):
        for key in ['headRefOid', 'baseRefOid']:
            changed = copy.deepcopy(self.pr)
            changed[key] = 'c' * 40
            with patch.object(w, 'fetch_pr', side_effect=[self.pr, changed]):
                self.assertEqual(w.snapshot('example/repo', 12)['status'], 'pending')


class TransportAndLoopTests(unittest.TestCase):
    def test_list_paginates(self):
        with patch.object(w, 'gh_json', return_value=[[dict(number=1)], [dict(number=2)]]) as api:
            self.assertEqual(w.open_prs('example/repo'), [1, 2])
            self.assertIn('--paginate', api.call_args.args)
            self.assertIn('--slurp', api.call_args.args)

    def test_partial_graphql_response_is_error(self):
        result = subprocess.CompletedProcess([], 0, '{"data":{},"errors":[{"message":"denied"}]}', '')
        with patch.object(w.subprocess, 'run', return_value=result):
            with self.assertRaises(w.WatchError):
                w.gh_json('api', 'graphql')

    def test_cli_diagnostics_not_exposed(self):
        result = subprocess.CompletedProcess([], 1, '', 'secret token here')
        with patch.object(w.subprocess, 'run', return_value=result):
            with self.assertRaises(w.WatchError) as error:
                w.gh_json('api', 'graphql')
            self.assertNotIn('secret token', str(error.exception))

    def test_once_exit_codes(self):
        for status, code in [('ready', 0), ('blocked', 1), ('error', 2), ('pending', 3), ('merged', 0)]:
            with patch.object(w, 'snapshot', return_value=dict(pr=1, status=status, reasons=[])), patch('builtins.print'):
                self.assertEqual(w.main(['--repo', 'example/repo', '--pr', '1', '--once']), code)

    def test_all_rediscovers_new_prs_and_deduplicates(self):
        clock = [0]
        def sleep(seconds):
            clock[0] += seconds
        with patch.object(w.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(w.time, 'sleep', side_effect=sleep), \
             patch.object(w, 'open_prs', side_effect=[[1], [1, 2]]), \
             patch.object(w, 'snapshot', side_effect=lambda repo, n, *args: dict(pr=n, status='ready', reasons=[])), \
             patch('builtins.print') as output:
            self.assertEqual(w.main(['--repo', 'example/repo', '--all', '--timeout', '30', '--interval', '15']), 3)
            self.assertEqual(len([call for call in output.call_args_list if str(call.args[0]).startswith('#')]), 2)

    def test_invalid_api_schema_is_error_not_ready(self):
        with patch.object(w, 'fetch_pr', return_value={}), patch('builtins.print'):
            self.assertEqual(w.main(['--repo', 'example/repo', '--pr', '1', '--once']), 2)

    def test_repo_argument_rejects_flags_or_paths(self):
        with self.assertRaises(SystemExit), patch('sys.stderr'):
            w.main(['--repo', '../a/b', '--pr', '1', '--once'])


if __name__ == '__main__':
    unittest.main()
