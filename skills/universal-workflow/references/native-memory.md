# Reuse native memory

Prefer the host's existing memory for everyday recall. The project store is optional and serves portable project lessons, explicit evidence, version history, and evaluated workflow changes. It is not a replacement for native memory or a private-memory synchronization service.

## Select storage

| Information | Preferred home |
| --- | --- |
| Personal preferences and recurring corrections | The host's native memory controls |
| Required project rules | Existing AGENTS.md/CLAUDE.md or authoritative project documentation |
| Current architecture, code, versions, and commands readily visible in files | The actual project; inspect rather than duplicate |
| Verified lessons needed by multiple agents/hosts | Optional shared project store, with evidence |
| Evaluation results and proposed workflow improvements | Shared project review records |
| Temporary task state | Task-local scratch/context |

If only one host is used and no explicit history/evaluation requirement exists, use the native-memory path without initializing SQLite. For a project used by both Claude Code and Codex, keep the portable store project-scoped and avoid copying personal profiles into it.

## Claude Code

Claude Code provides auto memory in addition to persistent instruction files. Use `/memory` to inspect its supported controls. Auto memory is normally on in local sessions, with exceptions for some background and hosted contexts. Do not assume it is enabled in a worker started by another agent. Use the current host's supported memory operation instead of guessing its private directory name.

Official reference checked 2026-11-06: https://code.claude.com/docs/en/memory

## Codex

Local Codex clients have memory generation and recall controls, including `/memories`. Local memories are off by default and can be enabled in settings; generation runs in the background for eligible sessions. ChatGPT Work uses account/workspace memory controls rather than that local store. Inspect the installed client's capabilities before suggesting configuration changes. Treat generated memory files as managed state and avoid editing them directly.

Official reference checked 2026-11-06: https://learn.chatgpt.com/docs/customization/memories

## Coexistence and conflicts

Do not toggle native memory, rewrite its private files, bulk-export conversations, or enable cross-machine synchronization as a side effect of installing this skill. If a user requests such a change, use the documented host control and keep its scope explicit.

Use the current user request and current source artifacts to resolve stale data. Attribute a portable lesson to its original evidence. When the same fact is already authoritative in a project document, store a pointer instead of a second mutable copy. Expire or correct an obsolete shared lesson through the runtime. Native-memory cleanup should use the host's own supported mechanism.

The foreground agent can draw on native memory already available in context. The optional review worker sees only the explicit project packet; it does not crawl Claude or Codex private histories. Automatic private-store import/sync is intentionally not implemented.
