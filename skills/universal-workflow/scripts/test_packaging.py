#!/usr/bin/env python3
"""Offline installation and hook regression checks with disposable projects."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import install
import uw


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='uw-packaging-')
        self.root = Path(self.temp.name).resolve()
        self.project = self.root / 'project with spaces'
        self.project.mkdir()
        self.store = uw.Store(self.project, create=True)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def hook(self, event, project_root=None):
        env={k:v for k,v in os.environ.items() if k!='CLAUDE_PROJECT_DIR'}
        if project_root is not None: env['CLAUDE_PROJECT_DIR']=str(project_root)
        return subprocess.run([sys.executable, str(Path(__file__).with_name('hook.py'))],
            input=json.dumps(event), capture_output=True, text=True, cwd=self.project, timeout=5, env=env)

    def installer(self, *args):
        return subprocess.run([sys.executable, str(Path(__file__).with_name('install.py')), *args],
            capture_output=True, text=True, timeout=15)

    def test_missing_or_relative_cwd_and_missing_session_do_not_write(self):
        for cwd in (None, '', '.', 'missing-folder', str(self.root/'absent')):
            event={'hook_event_name':'Stop','session_id':'s'}
            if cwd is not None: event['cwd']=cwd
            self.assertEqual(self.hook(event).returncode, 0)
        self.assertEqual(self.hook({'hook_event_name':'Stop','cwd':str(self.project)}).returncode, 0)
        self.assertEqual(self.store.status()['events'], 0)

    def test_distinct_turns_count_and_same_turn_retry_deduplicates(self):
        event={'hook_event_name':'Stop','cwd':str(self.project),'session_id':'s','last_assistant_message':'Done.'}
        for i in range(5):
            self.assertEqual(self.hook({**event,'prompt_id':str(i)}).returncode, 0)
        self.hook({**event,'prompt_id':'4'})
        self.assertEqual(self.store.status()['events'], 5)
        self.assertNotIn('Done.', str(self.store.prepare()['events']))

    def test_older_host_missing_prompt_id_uses_fresh_events(self):
        event={'hook_event_name':'Stop','cwd':str(self.project),'session_id':'s'}
        self.hook(event); self.hook(event)
        self.assertEqual(self.store.status()['events'], 2)

    def test_nested_repository_and_worktree_do_not_inherit_memory(self):
        self.store.put('outer','Outer project private context.',['fixture:scope'])
        for marker in ('directory', 'file'):
            child=self.project/marker
            child.mkdir()
            if marker=='directory': (child/'.git').mkdir()
            else: (child/'.git').write_text('gitdir: ../elsewhere')
            response=self.hook({'hook_event_name':'SessionStart','cwd':str(child)})
            self.assertEqual(response.stdout, '')
            self.hook({'hook_event_name':'Stop','cwd':str(child),'session_id':'s'})
        self.assertEqual(self.store.status()['events'], 0)

    def test_hook_lock_contention_is_bounded_and_diagnosed(self):
        self.store.db.execute('BEGIN IMMEDIATE')
        try:
            start=time.monotonic()
            response=self.hook({'hook_event_name':'Stop','cwd':str(self.project),'session_id':'s'})
            elapsed=time.monotonic()-start
        finally:
            self.store.db.rollback()
        self.assertEqual(response.returncode, 0)
        self.assertLess(elapsed, 4)
        self.assertIn('hook skipped', response.stderr)
        self.assertEqual(self.store.status()['events'], 0)

    def test_explicit_non_git_project_boundary_prevents_parent_inheritance(self):
        self.store.put('outer','Outer private context.',['fixture:scope'])
        child=self.project/'independent-document-project'
        child.mkdir()
        deep=child/'drafts'; deep.mkdir()
        for cwd in (child,deep):
            event={'hook_event_name':'SessionStart','cwd':str(cwd)}
            self.assertEqual(self.hook(event,project_root=child).stdout, '')
            self.hook({**event,'hook_event_name':'Stop','session_id':'s'},project_root=child)
        self.assertEqual(self.store.status()['events'], 0)
        separate=uw.Store(child,create=True)
        try:
            separate.put('child','Child project context.',['fixture:child'])
            output=self.hook({'hook_event_name':'SessionStart','cwd':str(deep)},project_root=child).stdout
            self.assertIn('Child project context.',output)
            self.assertNotIn('Outer private context.',output)
        finally: separate.close()

    def test_cwd_outside_explicit_root_requires_its_own_store(self):
        external=self.root/'external'; external.mkdir()
        event={'hook_event_name':'SessionStart','cwd':str(external)}
        self.assertEqual(self.hook(event,project_root=self.project).stdout,'')
        self.assertEqual(self.hook(event,project_root='.').stdout,'')
        own=uw.Store(external,create=True)
        try:
            own.put('own','External initialized context.',['fixture:external'])
            self.assertIn('External initialized context.',self.hook(event,project_root=self.project).stdout)
        finally: own.close()

    def test_both_targets_conflict_preflight_preserves_first_target(self):
        conflicting=self.project/'.claude/skills/universal-workflow'
        conflicting.mkdir(parents=True)
        (conflicting/'SKILL.md').write_text('Existing custom skill')
        response=self.installer('--target','both','--project',str(self.project),'--wire')
        self.assertEqual(response.returncode, 2)
        self.assertFalse(json.loads(response.stderr)['partial'])
        self.assertFalse((self.project/'.agents').exists())
        self.assertFalse((self.project/'AGENTS.md').exists())

    def test_existing_instruction_bytes_modes_and_settings_survive(self):
        instructions=self.project/'AGENTS.md'
        original=b'# Project rules\r\nKeep this exact text.  \r\n'
        instructions.write_bytes(original)
        instructions.chmod(0o644)
        settings=self.project/'.claude/settings.json'
        settings.parent.mkdir()
        settings.write_bytes(b'{"existing": true}\n')
        response=self.installer('--target','both','--project',str(self.project),'--wire')
        self.assertEqual(response.returncode, 0, response.stderr)
        self.assertTrue(instructions.read_bytes().startswith(original))
        self.assertNotIn(b'\r\r\n',instructions.read_bytes())
        if os.name=='posix': self.assertEqual(instructions.stat().st_mode & 0o777, 0o644)
        self.assertEqual(settings.read_bytes(), b'{"existing": true}\n')
        self.assertEqual((self.project/'CLAUDE.md').read_text(), '@AGENTS.md\n')
        before=instructions.read_bytes()
        again=self.installer('--target','both','--project',str(self.project),'--wire')
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(instructions.read_bytes(), before)

    def test_plugin_and_generic_exports_include_license(self):
        for target in ('plugin','generic'):
            dest=self.root/target
            response=self.installer('--target',target,'--output',str(dest))
            self.assertEqual(response.returncode, 0, response.stderr)
            skill=dest/'skills/universal-workflow' if target=='plugin' else dest
            self.assertIn('MIT License', (skill/'LICENSE').read_text())
            if target=='plugin':
                manifest=json.loads((dest/'.claude-plugin/plugin.json').read_text())
                self.assertEqual(manifest['license'], 'MIT')
                self.assertEqual(manifest['author']['name'], 'Arun Christudhas')

    def test_conflicting_managed_instructions_fail_before_install(self):
        (self.project/'AGENTS.md').write_text('<!-- universal-workflow:start -->\nExisting custom block\n<!-- universal-workflow:end -->\n')
        response=self.installer('--target','codex','--project',str(self.project),'--wire')
        self.assertEqual(response.returncode, 2)
        self.assertFalse((self.project/'.agents').exists())


if __name__=='__main__':
    unittest.main(verbosity=2)
