#!/usr/bin/env python3
"""Offline installation and hook regression checks with disposable projects."""
import json
import os
from pathlib import Path
import subprocess
import shutil
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

    def test_existing_tree_rejects_every_unexpected_entry(self):
        files=install.payload()
        cases=(('scripts/json.py',False),('scripts/json.pyc',False),('.hidden',False),
               ('scripts/__pycache__/json.pyc',False),('scripts/extra',True))
        for number,(rel,directory) in enumerate(cases):
            with self.subTest(entry=rel):
                dest=self.root/f'extra-{number}'
                install.install_tree(dest,files)
                entry=dest/rel
                entry.parent.mkdir(parents=True,exist_ok=True)
                if directory: entry.mkdir()
                else: entry.write_bytes(b'unexpected content')
                with self.assertRaisesRegex(ValueError,'Unexpected entry'):
                    install.install_tree(dest,files)
                self.assertTrue(entry.exists(),'Rejected content must not be removed automatically')

    def test_forged_module_rejected_before_installing_either_host(self):
        dest=self.project/'.claude/skills/universal-workflow'
        install.install_tree(dest,install.payload())
        marker=self.root/'injected-module-marker'
        module=dest/'scripts/json.py'
        module.write_text('from pathlib import Path\nPath('+repr(str(marker))+').write_text("harmless regression marker")\n'
                          'raise RuntimeError("injected module executed")\n',encoding='utf-8')
        response=self.installer('--target','both','--project',str(self.project),'--wire')
        if response.returncode==0:
            subprocess.run([sys.executable,str(dest/'scripts/uw.py'),'--help'],capture_output=True,timeout=5)
        self.assertFalse(marker.exists(),'Installer accepted a tree that executes the injected module')
        self.assertEqual(response.returncode,2,response.stdout)
        self.assertFalse(json.loads(response.stderr)['partial'])
        self.assertFalse((self.project/'.agents').exists())
        self.assertFalse((self.project/'AGENTS.md').exists())
        self.assertFalse((self.project/'CLAUDE.md').exists())
        self.assertTrue(module.is_file())

    def test_plugin_export_rejects_extra_module(self):
        dest=self.root/'forged-plugin'
        files=install.plugin_payload(install.payload())
        install.install_tree(dest,files)
        (dest/'skills/universal-workflow/scripts/json.py').write_text('unexpected module')
        response=self.installer('--target','plugin','--output',str(dest))
        self.assertEqual(response.returncode,2,response.stdout)
        self.assertFalse(json.loads(response.stderr)['partial'])

    @unittest.skipUnless(os.name=='posix','Symlink creation needs separate Windows privileges')
    def test_internal_symlinks_rejected_even_with_identical_content(self):
        files=install.payload()
        for number,kind in enumerate(('file','directory','dangling','extra')):
            with self.subTest(kind=kind):
                dest=self.root/f'linked-{number}'
                install.install_tree(dest,files)
                if kind=='file':
                    outside=self.root/'original-skill.md'
                    (dest/'SKILL.md').rename(outside)
                    (dest/'SKILL.md').symlink_to(outside)
                elif kind=='directory':
                    outside=self.root/'original-scripts'
                    (dest/'scripts').rename(outside)
                    (dest/'scripts').symlink_to(outside,target_is_directory=True)
                else:
                    target=self.root/'absent' if kind=='dangling' else dest/'SKILL.md'
                    (dest/'extra-link').symlink_to(target)
                with self.assertRaisesRegex(ValueError,'symlinks or reparse points'):
                    install.install_tree(dest,files)

    @unittest.skipUnless(os.name=='nt','Windows junction behavior')
    def test_internal_windows_junction_is_rejected(self):
        dest=self.root/'junction-skill'
        files=install.payload()
        install.install_tree(dest,files)
        outside=self.root/'junction-target'
        (dest/'scripts').rename(outside)
        result=subprocess.run(['cmd','/c','mklink','/J',str(dest/'scripts'),str(outside)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        try:
            with self.assertRaisesRegex(ValueError,'reparse points'):
                install.install_tree(dest,files)
        finally:
            (dest/'scripts').rmdir()

    @unittest.skipUnless(os.name=='posix','POSIX special file behavior')
    def test_expected_file_replaced_with_fifo_is_rejected_without_reading(self):
        dest=self.root/'special-file'
        files=install.payload()
        install.install_tree(dest,files)
        (dest/'SKILL.md').unlink()
        os.mkfifo(dest/'SKILL.md')
        response=self.installer('--target','generic','--output',str(dest))
        self.assertEqual(response.returncode,2,response.stdout)

    def test_missing_file_and_file_directory_swaps_are_rejected(self):
        files=install.payload()
        for number,kind in enumerate(('missing','file-as-directory','directory-as-file')):
            with self.subTest(kind=kind):
                dest=self.root/f'malformed-{number}'
                install.install_tree(dest,files)
                if kind=='directory-as-file':
                    shutil.rmtree(dest/'scripts')
                    (dest/'scripts').write_bytes(b'not a directory')
                else:
                    (dest/'SKILL.md').unlink()
                    if kind=='file-as-directory': (dest/'SKILL.md').mkdir()
                with self.assertRaises(ValueError):
                    install.install_tree(dest,files)


if __name__=='__main__':
    unittest.main(verbosity=2)
