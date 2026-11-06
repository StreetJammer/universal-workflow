# Import selected session traces

Use `scripts/uw.py --project /absolute/project trace-import --file selected-session.json` when the user authorizes importing a specific session export. Read the destination `status` first and use its exact `project_id`. Do not discover or read private native-memory/history directories automatically.

Supply one UTF-8 JSON object with required fields `schema: 1`, `project_id`, `source`, `session_id`, and `turns`. Each turn requires `id`, `status`, and `summary`. Optional arrays are:

- `messages`: objects with `role` (`user`, `assistant`, `system`, or `tool`) and `content`.
- `tool_calls`: objects with `name`, `input_summary`, and `output_summary`.
- `skills_used`: selected skill-name strings.
- `user_corrections`: correction strings with useful diagnostic context.

Reject hidden reasoning and unsupported source fields; do not invent a native export parser. Select at most 100 turns and 1 MB of input. Keep each normalized event below 50 KB; messages are at most 4,000 characters and tool summaries at most 2,000. Split larger exports into explicit batches without changing their source/session/turn identities.

Inspect selected content for private information. The importer redacts common credential forms, email addresses, and home-directory prefixes, but does not guarantee comprehensive secret or personal-data detection. It stores only normalized evidence, never copies the original source file, and never executes recorded commands.

Reimporting identical sanitized evidence is idempotent. Changed evidence under the same identity is rejected, and any failed turn aborts the entire batch. Treat imported provenance as a supplied claim, not authentication. Use `prepare --force` or the configured review threshold to review the evidence. Each event contains its own `trace` context, so existing submission and proposal gates apply. Import does not evaluate or adopt memories.
