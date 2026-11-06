#!/usr/bin/env python3
"""Regression checks for concurrency, interruption, isolation, and review integrity.

Uses disposable fixtures only; never calls live models.
"""
import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "uw.py"
spec = importlib.util.spec_from_file_location('uw_audited', SOURCE)
uw = importlib.util.module_from_spec(spec)
spec.loader.exec_module(uw)


def wait_file(path, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if path.exists() and path.stat().st_size:
            return
        time.sleep(0.01)
    raise AssertionError(f'Timed out waiting for fixture {path}')


class RuntimeAudit(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uw-regression-')
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.store = uw.Store(self.project, create=True)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def event(self, n=1):
        return self.store.record({'id': f'e{n}', 'session': f's{n}', 'status': 'failure',
            'summary': 'Reproduced controlled fixture failure.', 'evidence': [f'fixture:{n}']})

    def candidate(self, key='workflow', kind='workflow'):
        return {'key': key, 'kind': kind, 'body': 'Run the verified project check.',
            'reason': 'Supported by the controlled event.', 'ttl_days': 30, 'evidence_event_ids': ['e1']}

    def proposal(self):
        self.event()
        packet = self.store.prepare(force=True)
        return self.store.submit(packet, {'proposals': [self.candidate()]})[0]['id']

    def evaluator(self, passed, block=False):
        script = self.root / ('blocking.py' if block else 'passing.py')
        ready, release = self.root / 'ready', self.root / 'release'
        script.write_text(
            'import json,sys,time\nfrom pathlib import Path\np=json.load(sys.stdin)\n'
            + (f'Path({str(ready)!r}).write_text("ready")\n'
               f'end=time.monotonic()+10\nwhile not Path({str(release)!r}).exists() and time.monotonic()<end: time.sleep(0.01)\n' if block else '')
            + f'print(json.dumps({{"cases":[{{"id":c["id"],"baseline_pass":False,"candidate_pass":{passed!r},"fixed_constraints_pass":True}} for c in p["cases"]]}}))\n')
        cases = self.root / 'cases.json'
        cases.write_text(json.dumps([{'id': f'heldout-{n}', 'input': 'fixture'} for n in range(3)]))
        return [sys.executable, str(script)], cases, ready, release

    def test_rollback_does_not_revalidate_stale_hash(self):
        first = self.store.put('fact', 'Original verified value.', ['fixture:1'], ttl_days=0)
        second = self.store.put('fact', 'Corrected verified value.', ['fixture:2'], first['hash'], ttl_days=0)
        self.store.rollback('fact', 1, second['hash'])
        with self.assertRaisesRegex(ValueError, 'Conflict'):
            self.store.put('fact', 'Stale writer overwrites newer history.', ['fixture:stale'], first['hash'], ttl_days=0)

    def test_review_cannot_reuse_consumed_events(self):
        self.event()
        first = self.store.prepare(force=True)
        second = self.store.prepare(force=True)
        self.store.submit(first, {'proposals': [self.candidate('first', 'lesson')]})
        self.assertFalse(self.store.prepare(force=True)['due'])
        with self.assertRaises(ValueError):
            self.store.submit(second, {'proposals': [self.candidate('second', 'lesson')]})

    def test_accepted_evaluator_receipt_is_not_overwritten(self):
        pid = self.proposal()
        command, cases, _, _ = self.evaluator(True)
        self.store.config['evaluator'] = command
        original_receipt = self.store.evaluate(pid, cases)
        self.assertTrue(original_receipt['passed'])
        command, cases, ready, release = self.evaluator(False, block=True)
        def delayed_evaluation():
            other = uw.Store(self.project)
            try:
                other.config['evaluator'] = command
                return other.evaluate(pid, cases, timeout=15)
            finally:
                other.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            job = pool.submit(delayed_evaluation)
            try:
                wait_file(ready)
                self.store.decide(pid, accept=True)
            finally:
                release.write_text('release')
            try:
                job.result(timeout=15)
            except ValueError as error:
                self.assertIn('changed', str(error))
        accepted = self.store.proposal(pid)
        self.assertEqual(accepted['status'], 'accepted')
        self.assertTrue(accepted['receipt']['passed'], 'A post-accept evaluation replaced the passing receipt')
        self.assertEqual(accepted['receipt'], original_receipt)

    @unittest.skipUnless(os.name == 'posix', 'POSIX cancellation behavior')
    def test_interrupt_cleans_up_worker(self):
        worker = self.root / 'worker.py'
        pidfile = self.root / 'worker.pid'
        worker.write_text(f'import os,time\nfrom pathlib import Path\nPath({str(pidfile)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n')
        wrapper = self.root / 'wrapper.py'
        wrapper.write_text(
            'import importlib.util,sys,os,signal,json\nfrom pathlib import Path\n'
            f'spec=importlib.util.spec_from_file_location("uw",{str(SOURCE)!r})\nu=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(u)\n'
            'try:\n'
            f' u.run_process([sys.executable,{str(worker)!r}],"",{str(self.project)!r},20)\n'
            'except KeyboardInterrupt:\n'
            f' pid=int(Path({str(pidfile)!r}).read_text())\n'
            ' try: os.kill(pid,0); alive=True\n'
            ' except ProcessLookupError: alive=False\n'
            ' print(json.dumps({"alive_after_interrupt":alive}),flush=True)\n'
            ' if alive:\n'
            '  os.killpg(pid,signal.SIGKILL)\n'
            '  try: os.waitpid(pid,0)\n'
            '  except ChildProcessError: pass\n')
        parent = subprocess.Popen([sys.executable, str(wrapper)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            wait_file(pidfile)
            parent.send_signal(signal.SIGINT)
            out, err = parent.communicate(timeout=5)
            self.assertEqual(parent.returncode, 0, err)
            self.assertFalse(json.loads(out)['alive_after_interrupt'], 'run_process left the worker alive after cancellation')
        finally:
            if parent.poll() is None:
                parent.kill()
                parent.wait()
            if pidfile.exists():
                try: os.killpg(int(pidfile.read_text()), signal.SIGKILL)
                except ProcessLookupError: pass

    def test_transaction_rolls_back_head_if_history_insert_fails(self):
        first = self.store.put('fact', 'Initial value.', ['fixture:1'])
        self.store.db.execute("CREATE TRIGGER fail_history BEFORE INSERT ON history BEGIN SELECT RAISE(ABORT,'fixture abort'); END")
        self.store.db.commit()
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'fixture abort'):
            self.store.put('fact', 'Must be rolled back.', ['fixture:2'], first['hash'])
        self.assertEqual(self.store.current('fact')['hash'], first['hash'])
        self.assertEqual(len(self.store.history('fact')), 1)
        self.assertFalse(self.store.db.in_transaction)

    def test_proposal_gate_blocks_downgrade_from_existing_workflow(self):
        pid = self.proposal()
        command, cases, _, _ = self.evaluator(True)
        self.store.config['evaluator'] = command
        self.store.evaluate(pid, cases)
        self.store.decide(pid, accept=True)
        self.event(2)
        packet = self.store.prepare(force=True)
        candidate = self.candidate(kind='fact')
        candidate['evidence_event_ids'] = ['e2']
        new_pid = self.store.submit(packet, {'proposals': [candidate]})[0]['id']
        with self.assertRaisesRegex(ValueError, 'evaluator'):
            self.store.decide(new_pid, accept=True)

    @unittest.skipUnless(os.name == "posix", "Symlink creation needs separate Windows privileges")
    def test_alias_project_path_shares_identity_and_foreign_project_is_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.project, target_is_directory=True)
        other = uw.Store(alias)
        try:
            self.assertEqual(other.config['project_id'], self.store.config['project_id'])
        finally:
            other.close()
        self.event()
        packet = self.store.prepare(force=True)
        foreign_path = self.root / 'foreign'
        foreign_path.mkdir()
        foreign = uw.Store(foreign_path, create=True)
        try:
            with self.assertRaisesRegex(ValueError, 'Project mismatch'):
                foreign.submit(packet, {'proposals': []})
        finally:
            foreign.close()

    @unittest.skipUnless(os.name == "posix", "Symlink creation needs separate Windows privileges")
    def test_memory_directory_symlink_is_rejected(self):
        foreign_path = self.root / 'foreign'
        foreign_path.mkdir()
        (foreign_path / '.universal-workflow').symlink_to(self.store.root, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            uw.Store(foreign_path)

    def test_invalid_numerics_and_partial_submit_leave_no_rows(self):
        for invalid in [True, -1, float('nan'), float('inf'), '2']:
            with self.assertRaises(ValueError):
                self.store.record({'session': 's', 'status': 'failure', 'summary': 'Fixture.', 'tokens': invalid})
        self.assertEqual(self.store.status()['events'], 0)
        self.event()
        packet = self.store.prepare(force=True)
        with self.assertRaises(ValueError):
            self.store.submit(packet, {'proposals': [self.candidate(), None]})
        self.assertEqual(self.store.status()['pending_proposals'], 0)
        self.assertEqual(self.store.db.execute('SELECT status FROM reviews').fetchone()[0], 'prepared')

    def test_default_packet_can_be_roundtripped_through_cli_input_limit(self):
        # Even default batching exceeds the 2 MB byte reader with valid Unicode.
        for i in range(50):
            self.store.record({'id': f'e{i}', 'session': f's{i}', 'status': 'observed',
                'summary': '\u00e9' * 4000, 'evidence': [str(j) + '\u00e9' * 998 for j in range(20)]})
        path = self.root / 'packet.json'
        processed = []
        while True:
            packet = self.store.prepare(force=True)
            if packet.get('due') is False:
                break
            uw.emit(packet, path)
            self.assertLessEqual(path.stat().st_size, 2_000_000, 'Emitted packet exceeds submit input limit')
            self.assertEqual(uw.read_json(path), packet)
            processed.extend(e['seq'] for e in packet['events'])
            self.store.submit(packet, {'proposals': []})
        self.assertEqual(processed, list(range(1, 51)), 'Byte batching lost, duplicated, or skipped events')

    def test_read_only_status_does_not_reinitialize_missing_database(self):
        self.store.put('fact', 'Persistent history fixture.', ['fixture:1'])
        self.store.close()
        dbpath = self.project / '.universal-workflow' / 'memory.sqlite3'
        dbpath.unlink()
        result = subprocess.run([sys.executable, str(SOURCE), '--project', str(self.project), 'status'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0, 'status silently recreated an empty store with the same project identity')
        self.assertFalse(dbpath.exists())

    def test_malformed_config_has_structured_cli_error(self):
        self.store.close()
        (self.project / '.universal-workflow' / 'config.json').write_text('[]')
        result = subprocess.run([sys.executable, str(SOURCE), '--project', str(self.project), 'status'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('error', json.loads(result.stderr))

    def test_concurrent_init_never_observes_partial_config(self):
        # Pause at the original dump or the fixed atomic publisher. A second
        # initializer must wait for completion rather than read partial state.
        project = self.root / 'initializing'
        project.mkdir()
        ready, release = self.root / 'init-ready', self.root / 'init-release'
        initializer = self.root / 'initializer.py'
        initializer.write_text(
            'import importlib.util,time\nfrom pathlib import Path\n'
            f'spec=importlib.util.spec_from_file_location("uw",{str(SOURCE)!r})\nu=importlib.util.module_from_spec(spec)\nspec.loader.exec_module(u)\n'
            'original_dump=u.json.dump\noriginal_atomic=u.atomic\n'
            'def pause():\n'
            f' Path({str(ready)!r}).write_text("ready")\n'
            ' end=time.monotonic()+10\n'
            f' while not Path({str(release)!r}).exists() and time.monotonic()<end: time.sleep(0.01)\n'
            'def delayed_dump(*args,**kwargs):\n'
            ' pause()\n return original_dump(*args,**kwargs)\n'
            'def delayed_atomic(*args,**kwargs):\n'
            ' if Path(args[0]).name=="config.json": pause()\n'
            ' return original_atomic(*args,**kwargs)\n'
            'u.json.dump=delayed_dump\nu.atomic=delayed_atomic\n'
            f'store=u.Store({str(project)!r},create=True)\nstore.close()\n')
        first = subprocess.Popen([sys.executable, str(initializer)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = None
        try:
            wait_file(ready)
            second = subprocess.Popen([sys.executable, str(SOURCE), '--project', str(project), 'init'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                second.communicate(timeout=0.5)
            except subprocess.TimeoutExpired:
                pass
        finally:
            release.write_text('release')
            _, first_error = first.communicate(timeout=15)
        _, second_error = second.communicate(timeout=15)
        self.assertEqual(first.returncode, 0, first_error)
        self.assertEqual(second.returncode, 0, second_error)

    def test_concurrent_initializers_keep_one_project_identity(self):
        project = self.root / 'many-initializers'
        project.mkdir()
        def initialize(_):
            store = uw.Store(project, create=True)
            try:
                return store.status()['project_id']
            finally:
                store.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            identities = list(pool.map(initialize, range(12)))
        self.assertEqual(len(set(identities)), 1)

    def test_windows_delete_pending_init_lock_retries(self):
        project = self.root / 'delete-pending-lock'
        project.mkdir()
        original_mkdir = Path.mkdir
        attempts = []
        def mkdir(path, *args, **kwargs):
            if path.name == '.init.lock':
                attempts.append(path)
                if len(attempts) == 1:
                    error = PermissionError('Windows delete-pending directory')
                    error.winerror = 5
                    raise error
            return original_mkdir(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', mkdir):
            store = uw.Store(project, create=True)
            try:
                identity = store.status()['project_id']
                self.assertTrue(identity)
            finally:
                store.close()
        self.assertEqual(len(attempts), 2)
        reopened = uw.Store(project)
        try:
            self.assertEqual(reopened.status()['project_id'], identity)
        finally:
            reopened.close()

    def test_init_lock_access_denied_is_bounded(self):
        original_mkdir = Path.mkdir
        def mkdir(path, *args, **kwargs):
            if path.name == '.init.lock':
                error = PermissionError('Persistent Windows access denial')
                error.winerror = 5
                raise error
            return original_mkdir(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', mkdir), patch.object(uw.time, 'monotonic', side_effect=[0, 6]):
            with self.assertRaisesRegex(ValueError, 'inaccessible'):
                uw.Store(self.project, create=True)
        self.assertFalse((self.store.root / '.init.lock').exists())

    def test_init_lock_unrelated_permission_errors_propagate(self):
        original_mkdir = Path.mkdir
        def mkdir(path, *args, **kwargs):
            if path.name == '.init.lock':
                raise PermissionError('Unrelated permission failure')
            return original_mkdir(path, *args, **kwargs)
        with patch.object(Path, 'mkdir', mkdir):
            with self.assertRaisesRegex(PermissionError, 'Unrelated'):
                uw.Store(self.project, create=True)


if __name__ == '__main__':
    unittest.main(verbosity=2)
