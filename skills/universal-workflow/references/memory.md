# Memory runtime

Use Python 3.10+ and the standard library. In these examples, `UW` is the absolute path to this skill's `scripts/uw.py`, and `PROJECT` is an explicitly selected existing project root. Set task-specific shell variables; never repurpose system variables.

## Initialize, read, and write

```bash
python3 "$UW" --project "$PROJECT" init
python3 "$UW" --project "$PROJECT" status
python3 "$UW" --project "$PROJECT" recall --query "build command"
python3 "$UW" --project "$PROJECT" remember --key build-command --body-file verified-lesson.txt --evidence 'build-log:2026-11-06' --kind fact --ttl-days 30
```

`remember` accepts fact, lesson, and preference. Workflow/prompt changes require the proposal/evaluation route. Use `--scope scratch` for working notes; project retrieval and review exclude scratch by default. The runtime is intentionally project-local: there is no global/org write endpoint.

Evidence references must identify real artifacts or user statements. A source string is provenance, not automatic verification of the underlying claim. Record an actor label for attribution; it is not an authentication credential. Avoid secrets and unnecessary personal data. A basic credential detector rejects common patterns; it is not comprehensive DLP. Minimize stored text instead of relying on redaction alone.

## Concurrency and history

Each memory contains kind, body, evidence, expiry, revision, timestamp, and SHA-256 hash. New keys require `NEW`; updates require the exact current hash:

```bash
python3 "$UW" --project "$PROJECT" remember --key build-command --body-file revised-lesson.txt --evidence 'build-log:verified' --expected-hash CURRENT_HASH --kind fact
python3 "$UW" --project "$PROJECT" history --key build-command
python3 "$UW" --project "$PROJECT" rollback --key build-command --revision 1 --expected-hash CURRENT_HASH
python3 "$UW" --project "$PROJECT" expire --key build-command --expected-hash CURRENT_HASH
```

SQLite `BEGIN IMMEDIATE` makes comparison, revision insertion, and head update one transaction. Concurrent writers cannot silently overwrite a newer value through this API. On conflict, reread, reconcile, and retry; do not substitute a new hash without examining changes. Rollback creates a new revision and preserves the audit trail. Hashes bind both payload and revision, so restoring an old value does not revalidate an old writer token. Expiry removes a memory from normal retrieval, not history. Revalidation requires fresh evidence.

## Outcome records

Write a small JSON file and call `record --file event.json`:

```json
{
  "session": "task-042",
  "status": "failure",
  "summary": "Mobile tap continued after pointer cancellation; reproduced in the engine.",
  "evidence": ["test-output/mobile-input-cancel.txt"],
  "tool": "engine-playtest",
  "error_tag": "pointer-cancel",
  "domain": "game"
}
```

Optional duration_seconds, tokens, and cost must be measured nonnegative values. Status is success, failure, blocked, unverified, or observed. An observed event is not proof of success. Explicit stable event IDs provide idempotency; reusing an ID with different content is rejected. Do not store hidden reasoning or raw transcript archives. Attach sanitized excerpts only when authorized and useful.

## Portability and backup

```bash
python3 "$UW" --project "$PROJECT" export --out /absolute/new-export-directory
python3 "$UW" --project "$PROJECT" backup --out /absolute/new-backup.sqlite3
```

Export creates `index.md`, individual memory Markdown files, and a JSON snapshot with history, events, reviews, and proposals. The database is authoritative; Markdown is a readable projection for other agents and `rg` searches. The current recall implementation uses lexical token overlap and is suitable for modest project stores. It is not a vector search or enterprise-scale search engine.

Backup uses SQLite's consistent backup API. Copy `config.json` with the backup because it identifies the project and configures the worker. Restore only into a stopped project store, preserving project identity and permissions. Import/merge across unrelated projects is deliberately not automatic. Exports include private project data: share only intentionally. The store's own `.gitignore` excludes runtime data from ordinary source control.

## Permission and deployment boundary

The local CLI restricts writable scopes and preserves transaction history. It does not authenticate users or prevent an agent with filesystem access from editing the database/config directly. Use separate OS accounts, containers, service credentials, storage partitions, and an authenticated memory service for adversarial or multi-tenant environments. Enforce organization policy outside the writable project store. SQLite WAL is intended for a local filesystem, not arbitrary network-share concurrency across a fleet. Distributed production deployments need a transactional service/database and enforced access controls.

## Storage format

Version 0.1.0 initializes schema 1 and verifies the database project identity against its configuration. Initialization is serialized; readers never recreate a missing database. Incomplete or incompatible development stores are rejected with an error. This first public version does not migrate unpublished development snapshots. Back up important history before upgrades.
