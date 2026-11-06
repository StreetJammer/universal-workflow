#!/usr/bin/env python3
"""Install Universal Workflow locally or export a Claude Code plugin. No network calls."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import sys

SOURCE = Path(__file__).resolve().parents[1]
NAME = 'universal-workflow'


def payload():
    paths = [SOURCE / 'SKILL.md', SOURCE / 'LICENSE']
    for directory in ('agents', 'scripts', 'references', 'assets'):
        paths += [p for p in (SOURCE / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix != '.pyc']
    for p in paths:
        if p.is_symlink():
            raise ValueError('Source contains a symlink')
    return {p.relative_to(SOURCE).as_posix(): p.read_bytes() for p in paths}


def plugin_payload(files):
    out={'skills/' + NAME + '/' + k:v for k,v in files.items()}
    manifest={'name':NAME,'version':'0.1.0','author':{'name':'Arun Christudhas'},'license':'MIT','repository':'https://github.com/StreetJammer/universal-workflow','description':'Portable task prompting, verified work, project memory, and cross-session review.'}
    out['.claude-plugin/plugin.json']=(json.dumps(manifest,indent=2)+'\n').encode()
    hook_command='python3 "${CLAUDE_PLUGIN_ROOT}/skills/universal-workflow/scripts/hook.py"'
    hooks={event:[{'hooks':[{'type':'command','command':hook_command,'timeout':10}]}]
           for event in ('SessionStart','UserPromptSubmit','Stop','PostToolUseFailure')}
    out['hooks/hooks.json']=(json.dumps({'hooks':hooks},indent=2)+'\n').encode()
    out['commands/workflow.md']=('---\ndescription: Complete a task using Universal Workflow\n---\n'
        'Read `${CLAUDE_PLUGIN_ROOT}/skills/universal-workflow/SKILL.md` and use it to complete the following request. '
        'Preserve the original request and applicable host/project instructions.\n\n$ARGUMENTS\n').encode()
    out['commands/dream.md']=('---\ndescription: Review project lessons and propose evidenced improvements\n---\n'
        'Read `${CLAUDE_PLUGIN_ROOT}/skills/universal-workflow/SKILL.md` and its references/review.md. '
        'Run a project-scoped memory review using the current host model. Present proposals with evidence; '
        'evaluate workflow changes before promotion. Additional user instructions:\n\n$ARGUMENTS\n').encode()
    return out


def check_tree(destination, files):
    dest=Path(destination).expanduser().absolute()
    resolved=dest.resolve()
    if resolved==SOURCE or SOURCE in resolved.parents:
        raise ValueError('Destination must be outside this skill source')
    if any(p.is_symlink() for p in [dest,*dest.parents]):
        raise ValueError('Destination path must not traverse symlinks')
    if dest.exists():
        for rel,content in files.items():
            existing=dest/rel
            if not existing.is_file() or existing.is_symlink() or existing.read_bytes()!=content:
                raise ValueError('Destination differs; choose a new path to preserve existing work: '+str(dest))
        return dest,False
    return dest,True


def install_tree(destination, files):
    dest,changed=check_tree(destination,files)
    if not changed:
        return {'path':str(dest),'changed':False}
    dest.parent.mkdir(parents=True,exist_ok=True)
    temp=Path(tempfile.mkdtemp(prefix='.uw-install-',dir=dest.parent))
    try:
        for rel,content in files.items():
            path=temp/rel
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(content)
        temp.rename(dest)
    finally:
        if temp.exists(): shutil.rmtree(temp)
    return {'path':str(dest),'changed':True}


def instruction_block(path, skill_path, check_only=False):
    start='<!-- universal-workflow:start -->'
    end='<!-- universal-workflow:end -->'
    block=(start+'\nFor substantial implementation tasks in this project, use the Universal Workflow skill at `'
           +skill_path+'`. Keep simple questions and small edits lightweight. Preserve explicit user requirements '
           'and host permissions. Verify outcomes using the relevant domain tools; save only evidenced project lessons.\n'+end)
    if path.is_symlink(): raise ValueError('Instruction file is a symlink')
    existing=path.read_bytes().decode('utf-8') if path.exists() else ''
    newline='\r\n' if '\r\n' in existing else '\n'
    block=block.replace('\n',newline)
    shared=path.parent/'AGENTS.md'
    if path.name=='CLAUDE.md' and shared.is_file():
        shared_text=shared.read_text(encoding='utf-8')
        if start in shared_text and '@AGENTS.md' in existing.splitlines():
            return False
        if not path.exists():
            # A new CLAUDE.md can stop direct AGENTS.md loading; keep it imported.
            existing='@AGENTS.md\n'
            if start in shared_text:
                from uw import atomic
                if not check_only: atomic(path,existing)
                return True
    if start in existing or end in existing:
        if block in existing: return False
        raise ValueError('Existing managed block differs; merge it explicitly before reinstalling')
    from uw import atomic
    if not check_only:
        separator=newline if existing.endswith(('\n','\r')) else newline+newline
        atomic(path,existing+separator+block+newline)
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',required=True,choices=('codex','claude','both','plugin','generic'))
    parser.add_argument('--project',help='Existing project directory for repo-scoped skill installation')
    parser.add_argument('--user',action='store_true',help='Install skills in the current user home instead of a project')
    parser.add_argument('--output',help='New output directory for plugin or generic exports')
    parser.add_argument('--wire',action='store_true',help='Append a managed project instruction block; preserve existing content')
    args=parser.parse_args()
    results=[]
    try:
        files=payload()
        if args.target in ('plugin','generic'):
            if not args.output or args.project or args.user or args.wire:
                raise ValueError('plugin/generic requires --output only')
            results.append(install_tree(args.output, plugin_payload(files) if args.target=='plugin' else files))
        else:
            if bool(args.project)==bool(args.user) or args.output:
                raise ValueError('Choose exactly one of --project or --user')
            if args.user and args.wire:
                raise ValueError('--wire is project-only; global instructions are not changed')
            base=Path.home() if args.user else Path(args.project).expanduser().resolve()
            if not base.is_dir(): raise ValueError('Project directory must exist')
            targets=('codex','claude') if args.target=='both' else (args.target,)
            # Detect conflicts in all destinations before installing the first host.
            for target in targets:
                rel=('.agents' if target=='codex' else '.claude')+'/skills/'+NAME
                check_tree(base/rel,files)
                if args.wire:
                    instruction_block(base/('AGENTS.md' if target=='codex' else 'CLAUDE.md'),rel+'/SKILL.md',check_only=True)
            for target in targets:
                rel=('.agents' if target=='codex' else '.claude')+'/skills/'+NAME
                result=install_tree(base/rel,files)
                results.append(result)
                if args.wire:
                    result['instructions_updated']=instruction_block(base/('AGENTS.md' if target=='codex' else 'CLAUDE.md'),rel+'/SKILL.md')
        print(json.dumps({'installed':results,'background_scheduler_active':False},indent=2))
        return 0
    except (ValueError,OSError) as error:
        print(json.dumps({'error':str(error),'completed':results,
                          'partial':any(r.get('changed') or r.get('instructions_updated') for r in results)}),file=sys.stderr)
        return 2

if __name__=='__main__':
    sys.exit(main())
