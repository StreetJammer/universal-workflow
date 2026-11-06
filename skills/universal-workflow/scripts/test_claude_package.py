"""Portable upload archive checks; these do not simulate Claude's uploader."""
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

import install
import package_claude


class ClaudeArchiveTests(unittest.TestCase):
    def test_skill_zip_has_portable_paths_and_working_extracted_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            output = root / 'package with spaces.zip'
            result = subprocess.run(
                [sys.executable, str(Path(package_claude.__file__)), '--target', 'skill',
                 '--output', str(output)], capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(json.loads(result.stdout)['claude_upload_verified'])
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                self.assertIn('universal-workflow/SKILL.md', archive.namelist())
                for info in archive.infolist():
                    self.assertTrue(info.filename.startswith('universal-workflow/'))
                    self.assertNotIn('\\', info.filename)
                    self.assertNotIn('__pycache__', info.filename)
                    self.assertNotIn('.universal-workflow/', info.filename)
                    self.assertTrue(stat.S_ISREG(info.external_attr >> 16))
                archive.extractall(root / 'extracted')
            runtime = root / 'extracted/universal-workflow/scripts/uw.py'
            result = subprocess.run([sys.executable, str(runtime), '--help'],
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_export_header_fits_claude_and_preserves_source_and_body(self):
        source = install.payload()['SKILL.md']
        before = (install.SOURCE / 'SKILL.md').read_bytes()
        for line_ending in (b'\n', b'\r\n'):
            content = source.replace(b'\r\n', b'\n').replace(b'\n', line_ending)
            with mock.patch.object(install, 'payload', return_value={'SKILL.md': content}):
                exported = package_claude.upload_payload('skill')['universal-workflow/SKILL.md']
            delimiter = line_ending + b'---' + line_ending
            self.assertEqual(content.split(delimiter, 1)[1], exported.split(delimiter, 1)[1])
            description = json.loads(next(line for line in exported.decode().splitlines()
                                          if line.startswith('description: '))[13:])
            self.assertLessEqual(len(description), 200)
            self.assertGreater(len(description), 0)
        self.assertEqual((install.SOURCE / 'SKILL.md').read_bytes(), before)

    def test_plugin_zip_has_manifest_and_existing_hook_targets(self):
        files = package_claude.upload_payload('plugin')
        with tempfile.TemporaryDirectory() as temp:
            output = package_claude.write_archive(Path(temp) / 'plugin.zip', files)
            with zipfile.ZipFile(output) as archive:
                self.assertIsNone(archive.testzip())
                manifest = json.loads(archive.read('.claude-plugin/plugin.json'))
                self.assertEqual(manifest['name'], 'universal-workflow')
                self.assertIn('skills/universal-workflow/SKILL.md', archive.namelist())
                hooks = json.loads(archive.read('hooks/hooks.json'))
                for groups in hooks['hooks'].values():
                    for group in groups:
                        for hook in group['hooks']:
                            target = hook['command'].split('${CLAUDE_PLUGIN_ROOT}/', 1)[1].split('"', 1)[0]
                            self.assertIn(target, archive.namelist())

    def test_invalid_paths_rejected_and_existing_archive_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('skill\\SKILL.md', '../SKILL.md', '/SKILL.md', 'C:/SKILL.md',
                         'skill//SKILL.md', 'skill/./SKILL.md', 'skill/bad\nname'):
                output = root / 'invalid.zip'
                with self.subTest(name=name), self.assertRaises(ValueError):
                    package_claude.write_archive(output, {name: b'fixture'})
                self.assertFalse(output.exists())
            existing = root / 'keep.zip'
            existing.write_bytes(b'existing package')
            with self.assertRaises(FileExistsError):
                package_claude.write_archive(existing, {'SKILL.md': b'fixture'})
            self.assertEqual(existing.read_bytes(), b'existing package')


if __name__ == '__main__':
    unittest.main()
