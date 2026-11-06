# Selected session evidence

`trace-import` imports an explicitly selected UTF-8 JSON session export into an already initialized project. It preserves selected conversation messages, tool input/output summaries, skills used, user corrections, and source/turn references as structured event evidence. It does not scan native Claude/Codex history or private memory, read referenced files, run recorded commands, or contact a model.

## Import and review

Get the destination identity from `status`, use it in the JSON, then import:

```bash
python3 skills/universal-workflow/scripts/uw.py --project /absolute/project status
python3 skills/universal-workflow/scripts/uw.py --project /absolute/project trace-import --file selected-session.json
python3 skills/universal-workflow/scripts/uw.py --project /absolute/project prepare --force --out review-packet.json
```

`trace-import` returns counts for newly imported and duplicate events. Existing `prepare`, `dream-run`, and `submit` consume the same event stream; each selected turn carries a nested `trace` object. Imported evidence is untrusted data. Source labels describe supplied provenance; they do not authenticate who said something or prove a tool result occurred.

## JSON contract

The top-level fields shown below are required. A turn requires `id`, `status`, and `summary`; the four arrays are optional and default to empty. No unknown fields are accepted at any level.

```json
{
  "schema": 1,
  "project_id": "COPY-THE-DESTINATION-PROJECT-ID",
  "source": "manually-selected-session-export",
  "session_id": "session-2026-11-06",
  "turns": [
    {
      "id": "turn-1",
      "status": "failure",
      "summary": "The web build used an unavailable target.",
      "messages": [
        {"role": "user", "content": "Build the web project."},
        {"role": "assistant", "content": "The build target was missing."}
      ],
      "tool_calls": [
        {
          "name": "shell",
          "input_summary": "Ran make build.",
          "output_summary": "No rule to make target build; make web-build exists."
        }
      ],
      "skills_used": ["web-build"],
      "user_corrections": ["Use make web-build for this project."]
    }
  ]
}
```

Supported statuses are `success`, `failure`, `blocked`, `unverified`, and `observed`. Supported message roles are `user`, `assistant`, `system`, and `tool`. Hidden reasoning roles and arbitrary nested tool payloads are rejected. Convert authorized source exports into this portable contract yourself or with the foreground host; this release does not provide native history-format adapters.

## Limits and privacy

| Input | Limit |
| --- | --- |
| Selected file/API document | 1 MB; at most 8 nested levels and 10,000 values |
| Turns per import | 1–100 |
| Source / session / turn reference | 500 / 200 / 100 characters |
| Turn summary | 4,000 characters |
| Messages per turn | 20; 4,000 characters each |
| Tool calls per turn | 20; 200 characters for name and 2,000 for each summary |
| Skills / corrections | 20 each; 200 / 2,000 characters per item |
| Normalized trace / complete event | 44,000 / 50,000 UTF-8 bytes |

Over-budget content is rejected rather than silently truncated. Existing bounded review packets continue through the resulting events without skipping them. Split large exports into smaller explicitly selected batches and keep stable source/session/turn references.

Common token formats, Bearer/Basic credentials, sensitive key/value assignments, authentication and cookie headers, private-key blocks, URL userinfo/query secrets, email addresses, and user-home prefixes are redacted before database insertion. This also applies to source/session/turn references. The original file is never copied into the store; only normalized selected evidence is persisted. Redaction is conservative but cannot identify every possible secret or personal detail. Select authorized material and inspect the input for sensitive content not covered by these patterns; do not treat this importer as a comprehensive DLP system.

Sanitization is idempotent. Reimporting the same sanitized evidence is a no-op, even if a redacted credential changed. Changing retained evidence for the same source/session/turn identity is a conflict; use a distinct turn identity for a new observation. A duplicate identity inside one file is rejected. Source/session/turn identity is hashed into stable event IDs; only sanitized reference text participates in those hashes.

All turns are validated before writing. Inserts run in one transaction, so a late conflict rolls back earlier new turns from that batch. Foreign project identities and malformed/oversized documents write nothing. Imported evidence never directly promotes memories or claims successful verification.

## Verification scope

`test_traces.py` covers evidence preservation through review/proposal creation, redaction in database/WAL bytes and packets, idempotence, whole-batch rollback, project isolation, malformed/oversized/deep input, multi-packet progress, and an actual subprocess invocation of the importer. Fixtures are synthetic; no private native histories or live model calls are used. Real-world transcript quality and downstream learning gains require project-specific evaluation.
