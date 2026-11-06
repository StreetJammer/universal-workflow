#!/usr/bin/env python3
"""Build portable Claude skill/plugin upload ZIPs without installing anything."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import stat
import sys
import zipfile

import install


CLAUDE_DESCRIPTION = (
    'Use for substantial project tasks or requested automation: plan work, retrieve '
    'lessons, verify results, record outcomes, and review accumulated evidence. '
    'Keep trivial tasks lightweight.'
)


def upload_payload(target):
    if target not in ('skill', 'plugin'):
        raise ValueError('Choose skill or plugin')
    files = install.payload()
    # Claude's upload guide documents a 200-character description limit.
    # Adapt only the exported header, preserving the source and instruction body.
    lines = files['SKILL.md'].decode('utf-8').splitlines(keepends=True)
    if not lines or lines[0].strip() != '---':
        raise ValueError('Missing skill frontmatter')
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == '---'), None)
    if end is None:
        raise ValueError('Unterminated skill frontmatter')
    descriptions = [i for i in range(1, end) if lines[i].startswith('description: ')]
    if len(descriptions) != 1 or len(CLAUDE_DESCRIPTION) > 200:
        raise ValueError('Expected one plain description field of at most 200 characters')
    index = descriptions[0]
    newline = '\r\n' if lines[index].endswith('\r\n') else '\n'
    # JSON string quoting is also valid YAML, including embedded colons.
    lines[index] = 'description: ' + json.dumps(CLAUDE_DESCRIPTION) + newline
    files['SKILL.md'] = ''.join(lines).encode('utf-8')
    if target == 'plugin':
        return install.plugin_payload(files)
    return {'universal-workflow/' + name: data for name, data in files.items()}


def write_archive(destination, files):
    destination = Path(destination).expanduser().absolute()
    if destination.resolve() == install.SOURCE or install.SOURCE in destination.resolve().parents:
        raise ValueError('ZIP output must be outside the skill source directory')
    for name in files:
        # ZIP entry names are POSIX paths, even when built on Windows.
        if (not re.fullmatch(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*', name)
                or any(part in ('.', '..') for part in name.split('/'))):
            raise ValueError('Invalid archive path: ' + repr(name))
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation preserves an existing package, including symlinks.
    with destination.open('xb') as stream:
        try:
            with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name, data in sorted(files.items()):
                    info = zipfile.ZipInfo(name)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFREG | 0o644) << 16
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, data)
        except BaseException:
            stream.close()
            destination.unlink(missing_ok=True)
            raise
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, choices=('skill', 'plugin'))
    parser.add_argument('--output', required=True, help='New ZIP path; existing files are preserved')
    args = parser.parse_args()
    try:
        files = upload_payload(args.target)
        destination = write_archive(args.output, files)
    except (ValueError, OSError) as error:
        print(json.dumps({'error': str(error)}), file=sys.stderr)
        return 2
    print(json.dumps({'archive': str(destination), 'target': args.target, 'files': len(files),
                      'claude_upload_verified': False}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
