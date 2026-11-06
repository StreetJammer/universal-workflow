#!/usr/bin/env python3
"""Offline structural validation of the distributable repository."""
from pathlib import Path
import ast
import json
import re
import sys

ROOT=Path(__file__).resolve().parents[1]
SKILL=ROOT/'skills/universal-workflow'


def main():
    errors=[]
    required=['README.md','LICENSE','AGENTS.md','CLAUDE.md','CONTRIBUTING.md','SECURITY.md',
              '.claude-plugin/plugin.json','hooks/hooks.json','commands/workflow.md','commands/dream.md',
              '.github/workflows/ci.yml','skills/universal-workflow/SKILL.md']
    for relative in required:
        if not (ROOT/relative).is_file(): errors.append('Missing '+relative)
    for path in ROOT.rglob('*'):
        if not path.is_file() or '.git' in path.parts or '__pycache__' in path.parts:
            continue
        if path.suffix=='.json':
            try: json.loads(path.read_text(encoding='utf-8'))
            except (ValueError,UnicodeError) as e: errors.append(f'{path.relative_to(ROOT)}: {e}')
        elif path.suffix=='.py':
            try: ast.parse(path.read_text(encoding='utf-8'),filename=str(path),feature_version=(3,10))
            except (SyntaxError,UnicodeError) as e: errors.append(f'{path.relative_to(ROOT)}: {e}')
        elif path.suffix=='.md':
            text=path.read_text(encoding='utf-8')
            # Local Markdown references only; URLs and in-page anchors do not need network access.
            for link in re.findall(r'(?<!!)\[[^\]]*\]\(([^)]+)\)',text):
                if '://' in link or link.startswith('#') or '$' in link: continue
                target=link.split('#',1)[0]
                if target and not (path.parent/target).exists(): errors.append(f'Broken link in {path.relative_to(ROOT)}: {link}')
    front=(SKILL/'SKILL.md').read_text(encoding='utf-8').split('---',2)
    if len(front)!=3 or not re.search(r'^name: universal-workflow$',front[1],re.M) or not re.search(r'^description: .+',front[1],re.M):
        errors.append('Invalid skill frontmatter')
    manifest=json.loads((ROOT/'.claude-plugin/plugin.json').read_text())
    if manifest.get('name')!='universal-workflow': errors.append('Unexpected plugin name')
    hooks=json.loads((ROOT/'hooks/hooks.json').read_text())
    for event in ('SessionStart','UserPromptSubmit','Stop','PostToolUseFailure'):
        try:
            command=hooks['hooks'][event][0]['hooks'][0]['command']
            relative=command.split('${CLAUDE_PLUGIN_ROOT}/',1)[1].split('"',1)[0]
            if not (ROOT/relative).is_file(): errors.append(f'Hook target missing: {event}')
        except (KeyError,IndexError,TypeError): errors.append('Invalid hook '+event)
    if (SKILL/'references/source-transcript.txt').exists(): errors.append('Raw third-party transcript must not be redistributed')
    if any(ROOT.rglob('memory.sqlite3')): errors.append('Generated project database in distributable')
    if errors:
        print('\n'.join(errors),file=sys.stderr)
        return 1
    print('Package structure, JSON, Python 3.10 syntax, local links, and hook targets are valid.')
    return 0

if __name__=='__main__': sys.exit(main())
