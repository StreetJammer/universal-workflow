#!/usr/bin/env python3
"""Policy capacity must never block supported token revocation or rotation."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import uw_service as service


class PolicyCapacityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uw-policy-capacity-')
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name) / 'service'
        service.initialize(self.directory)
        self.path = self.directory / 'policy.json'

    @staticmethod
    def serialized(policy):
        # Exact representation emitted by the supported legacy writer.
        return json.dumps(policy, indent=2) + '\n'

    def policy_at_size(self, size, revision=9):
        policy = {'schema': 1, 'revision': revision, 'principals': {}}
        tokens = {}
        for index in range(service.MAX_PRINCIPALS):
            user = f'u{index:02d}' + 'u' * 61
            token = hashlib.sha256(f'capacity-fixture-{index}'.encode()).hexdigest()
            tokens[user] = token
            teams = [f't{team:02d}' for team in range(16)]
            policy['principals'][user] = {
                'org': 'org', 'teams': teams, 'org_writer': True,
                'team_writers': list(teams), 'enabled': True,
                'token_hash': hashlib.sha256(token.encode()).hexdigest()}
        remaining = size - len(self.serialized(policy).encode())
        self.assertGreaterEqual(remaining, 0)
        for principal in policy['principals'].values():
            for index, team in enumerate(principal['teams']):
                amount = min(64 - len(team), remaining // 2)
                principal['teams'][index] += 't' * amount
                principal['team_writers'][index] += 't' * amount
                remaining -= amount * 2
        self.assertLessEqual(remaining, 1, 'Fixture exceeds maximum valid membership size')
        next(iter(policy['principals'].values()))['org'] += 'o' * remaining
        service._validate_policy(policy)
        self.assertEqual(len(self.serialized(policy).encode()), size)
        return policy, tokens

    def write_legacy(self, policy):
        # Simulate a file already accepted by the previous release, without
        # using the new admission guard to generate the regression fixture.
        service.uw.atomic(self.path, self.serialized(policy))
        self.assertEqual(service.read_policy(self.directory), policy)

    def assert_denied(self, client, token):
        with self.assertRaises(service.ServiceError) as denied:
            client.authenticate('Bearer ' + token)
        self.assertEqual(denied.exception.status, 401)

    def test_exact_legacy_cap_can_revoke_every_principal_without_changing_grants(self):
        original, tokens = self.policy_at_size(service.MAX_POLICY_BYTES)
        self.write_legacy(original)
        self.assertEqual(self.path.stat().st_size, 128_000)
        client = service.MemoryService(self.directory)
        users = list(tokens)
        self.assertEqual(client.authenticate('Bearer ' + tokens[users[0]])[1], users[0])
        for user in users:
            service.administer(self.directory, 'revoke', user)
            self.assertLessEqual(self.path.stat().st_size, service.MAX_POLICY_BYTES)
        current = service.read_policy(self.directory)
        expected = copy.deepcopy(original)
        expected['revision'] += len(users)
        for principal in expected['principals'].values():
            principal['enabled'] = False
        self.assertEqual(current, expected)
        self.assert_denied(client, tokens[users[0]])
        self.assert_denied(client, tokens[users[-1]])

    def test_exact_legacy_cap_rotation_crosses_revision_boundary_and_revokes_old_token(self):
        for revision in (9, 10**256 - 1):
            with self.subTest(revision_digits=len(str(revision))):
                original, tokens = self.policy_at_size(service.MAX_POLICY_BYTES, revision)
                self.write_legacy(original)
                user = next(iter(tokens))
                client = service.MemoryService(self.directory)
                rotated = service.administer(self.directory, 'rotate', user, expected_revision=revision)
                self.assertEqual(rotated['revision'], revision + 1)
                self.assertLessEqual(self.path.stat().st_size, service.MAX_POLICY_BYTES)
                self.assert_denied(client, tokens[user])
                self.assertEqual(client.authenticate('Bearer ' + rotated['token'])[1], user)
                current = service.read_policy(self.directory)
                expected = copy.deepcopy(original)
                expected['revision'] += 1
                expected['principals'][user]['token_hash'] = hashlib.sha256(rotated['token'].encode()).hexdigest()
                self.assertEqual(current, expected)
                service.administer(self.directory, 'revoke', user, expected_revision=revision + 1)
                self.assert_denied(client, rotated['token'])
                self.assertLessEqual(self.path.stat().st_size, service.MAX_POLICY_BYTES)

    def test_new_admission_reserves_all_revocations_and_revision_growth(self):
        admission_limit = service.MAX_POLICY_BYTES - service.POLICY_SECURITY_RESERVE
        policy, tokens = self.policy_at_size(admission_limit)
        service.write_policy(self.directory, policy)
        before = self.path.read_bytes()
        oversized, _ = self.policy_at_size(admission_limit + 1)
        with self.assertRaisesRegex(ValueError, 'size limit'):
            service.write_policy(self.directory, oversized)
        self.assertEqual(self.path.read_bytes(), before)
        client = service.MemoryService(self.directory)
        user = next(iter(tokens))
        self.assertEqual(client.authenticate('Bearer ' + tokens[user])[1], user)
        # Prove the reserved budget fits every false expansion plus 256 extra
        # decimal revision digits even before legacy whitespace reclamation.
        future = copy.deepcopy(policy)
        future['revision'] = 10**256
        for principal in future['principals'].values():
            principal['enabled'] = False
        self.assertEqual(len(self.serialized(future).encode()), service.MAX_POLICY_BYTES)
        service.write_policy(self.directory, future, security_update=True)
        self.assertEqual(service.read_policy(self.directory), future)
        self.assert_denied(client, tokens[user])

    def test_security_updates_and_reads_keep_the_hard_cap(self):
        policy, _ = self.policy_at_size(service.MAX_POLICY_BYTES)
        self.write_legacy(policy)
        before = self.path.read_bytes()
        for principal in policy['principals'].values():
            principal['org'] = 'o' * 64
            principal['teams'] = [f't{index:02d}' + 't' * 61 for index in range(16)]
            principal['team_writers'] = list(principal['teams'])
        self.assertGreater(len(json.dumps(policy, separators=(',', ':')).encode()), service.MAX_POLICY_BYTES)
        with self.assertRaisesRegex(ValueError, 'size limit'):
            service.write_policy(self.directory, policy, security_update=True)
        self.assertEqual(self.path.read_bytes(), before)
        service.uw.atomic(self.path, before.decode() + ' ')
        with self.assertRaisesRegex(ValueError, 'size limit'):
            service.read_policy(self.directory)


if __name__ == '__main__':
    unittest.main(verbosity=2)
