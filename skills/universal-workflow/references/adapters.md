# Host adapters and installation

This package is a portable skill plus a local runtime and Claude Code plugin exporter. It is not a universal execution engine. Python 3.10+ is required for scripts. No third-party Python packages, database server, model API key, or network access are needed for local memory, prompting, export, or installation. Model-backed review requires a separately installed, authenticated Claude Code or Codex CLI.

Set `SKILL_DIR` to the absolute directory containing this package's `SKILL.md`, and `PROJECT` to an existing project directory. Do not use a temporary memory store for important production history.

## Codex and Claude Code project skills

```bash
python3 "$SKILL_DIR/scripts/install.py" --target both --project "$PROJECT" --wire
```

This installs separate copies under `.agents/skills/universal-workflow` and `.claude/skills/universal-workflow`, and appends a small managed block to `AGENTS.md` and `CLAUDE.md`. It preserves their existing text. Use `--target codex` or `--target claude` for one host. Omit `--wire` to install only a discoverable skill without automatic project instructions.

Use `$universal-workflow` in Codex or `/universal-workflow` in Claude Code after the host discovers it. Verify availability in the host before claiming it is active. A skill install in ChatGPT does not install files on a user's separate computer.

For user-wide local discovery:

```bash
python3 "$SKILL_DIR/scripts/install.py" --target codex --user
python3 "$SKILL_DIR/scripts/install.py" --target claude --user
```

These target `~/.agents/skills` and `~/.claude/skills` respectively. Global instructions are not changed. Prefer project installation for controlled adoption. Do not install both a Claude skill and its plugin version in the same project unless intentional duplication is acceptable.

The installer is idempotent for identical content and refuses to overwrite differing files. For upgrades, install to a new directory or explicitly reconcile the old version. It does not silently erase customized copies. `--wire` only appends project instruction blocks, not hooks/settings or permission changes. Removal means removing the installed skill directory and its clearly marked instruction block; preserve project memory if it is still needed.

## Claude Code plugin

```bash
python3 "$SKILL_DIR/scripts/install.py" --target plugin --output /absolute/new/universal-workflow-plugin
claude --plugin-dir /absolute/new/universal-workflow-plugin
```

The output has `.claude-plugin/plugin.json`, `skills/universal-workflow/`, `commands/`, and `hooks/hooks.json`. `--plugin-dir` loads the local plugin for that Claude invocation; it is not a marketplace publication or permanent global registration. Use the host's plugin validation/install UI for your deployment.

The plugin exposes `/universal-workflow:workflow` and `/universal-workflow:dream`. Its hooks:

- SessionStart: add a small project-memory context summary.
- UserPromptSubmit: retrieve relevant memories without replacing the user's prompt.
- PostToolUseFailure: record tool name and an event fingerprint, not raw command/error content.
- Stop: record that the turn ended, not that the task succeeded; never veto completion or start another model.

Hooks operate only in a project that has initialized `.universal-workflow/config.json`. They do not initialize memory in arbitrary folders. The foreground skill must record meaningful verified lessons and outcomes. Hooks alone cannot infer them. With no project store, the hooks are a no-op. Hooks require an explicit absolute working directory and stop at the nearest Git repository boundary or the explicit `CLAUDE_PROJECT_DIR` session root. If the working directory moves outside that session root, only a store initialized at that exact working directory is used; external worktree ancestors and the original checkout are not inherited. Without a host project-root variable, non-Git folders use nearest-ancestor discovery, so initialize a store in each independent project. Recording hooks also require a session ID. Stop deduplication uses the host prompt ID; older hosts without it receive a fresh event ID per call. Hooks use a one-second database lock budget and are best effort: contention can skip metadata with a diagnostic in the host log. They may briefly delay a synchronous hook but never veto completion. Record important verified outcomes explicitly.

## Other agents, including Hermes

```bash
python3 "$SKILL_DIR/scripts/install.py" --target generic --output /absolute/new/universal-workflow
```

Point the host's documented skill discovery or project instructions at the exported `SKILL.md`. Invoke the JSON CLI for state. Hermes-specific plugin registration and hooks are not implemented or verified in this release; the generic workflow is portable only where the host supports these instructions and tools. Do not promise compatibility with unknown plugin manifest formats.

## Automation boundaries

Inside a live agent session, the skill coordinates prompt assembly, specialist routing, implementation, verification, recovery, and lesson capture. Outside the session, `dream-run` is a one-shot worker that can be scheduled. The package does not autonomously publish sites, send messages, buy assets, change account settings, or enable unbounded retries. The user can authorize such work through the host's normal task scope and tools.

The local store exposes a stable JSON command interface and portable snapshots. This version does not implement a remote MCP/HTTP memory service, authenticated fleet-wide tenancy, a model provider gateway, or a background daemon. Add those only when actual scale requires them.

## Official format references checked 2026-11-06

- Codex skill locations and progressive disclosure: https://learn.chatgpt.com/docs/build-skills
- Codex non-interactive execution: https://learn.chatgpt.com/docs/non-interactive-mode
- Claude plugin structure: https://code.claude.com/docs/en/plugins-reference
- Claude hook inputs/outputs: https://code.claude.com/docs/en/hooks
- Claude programmatic execution: https://code.claude.com/docs/en/headless

CLI adapters are implemented against these documented interfaces. Run native host validation on the deployment machine; neither Claude Code nor Codex CLI was installed in the authoring environment.
