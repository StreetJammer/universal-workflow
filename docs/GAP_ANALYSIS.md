# Release gap analysis

Scope: installation, context prompting, optional memory, review proposals, evaluation gates, process control, portability, privacy, and public packaging. The goal is a reviewable preview with explicit evidence, not an unverifiable claim that the repository is perfect.

## Reproduced defects repaired

| Gap | Repair | Regression evidence |
| --- | --- | --- |
| Rollback could make an old writer hash valid again | Bind revision identity into the hash | Stale writer rejected after A → B → rollback to A |
| Late evaluation could overwrite an accepted receipt | Recheck status, payload, baseline, and receipt transactionally | Paused failing evaluator cannot rewrite accepted passing evidence |
| Interrupting a worker left it running | Reap worker/process group on every exit path | SIGINT cleanup check on POSIX |
| Unicode batches could exceed submit's input limit | Byte-budget packets and keep remaining events eligible | All 50 large events round-trip in sequence |
| Read-only status recreated a missing database | Open existing stores only and validate identity/schema | Missing database remains missing with actionable error |
| Initializers could observe partially written configuration | Serialize initialization and atomically publish config | Controlled pause and 12 concurrent initializers |
| Windows reported access denied for a delete-pending initialization lock | Retry Windows error 5 within the existing five-second budget; propagate unrelated errors | Transient, persistent, and unrelated-error regressions plus Windows CI |
| Backup test retained a SQLite connection until garbage collection | Explicitly close the connection before fixture cleanup | Backup lifecycle test on Windows CI |
| Non-object config caused a traceback | Validate config shape before access | Structured JSON error for `[]` |
| Concurrent overlapping reviews reused consumed events | Reject already-consumed sequence ranges | Second overlapping packet rejected |
| Child projects inherited parent context | Stop at Git/worktree or explicit host project boundary | Independent Git and non-Git project probes |
| Identical completion text collapsed separate turns | Use prompt identity; UUID fallback for older hosts | Five distinct turns count five; same-turn retry deduplicates |
| Hooks waited longer than their host deadline | One-second database contention budget, diagnostic, best-effort recording | Locked store returns promptly without blocking completion |
| Missing working directory silently used process cwd | Require absolute existing cwd and session identity for records | Missing/relative/invalid cwd cannot write |
| Instruction wiring changed modes and newline bytes | Preserve existing bytes and POSIX mode | CRLF prefix, permissions, unrelated settings, idempotence |
| A later target conflict left an unexplained partial install | Preflight all destinations/blocks; report completed mutations on later filesystem errors | Second-target and managed-block conflicts leave first target untouched |
| Portable exports lacked licensing metadata | Include MIT terms and author/license/repository manifest fields | Generic and plugin exports contain matching license |

## Deliberate limits and next release gates

| Area | Current boundary | Evidence needed to expand it |
| --- | --- | --- |
| Native integrations | Claude/Codex transport mocks and documented formats | Authenticated native CLI installation and real host smoke checks |
| Operating systems | Local Linux/Python 3.12 run; six hosted jobs active on PR #1 | All six jobs must pass on the latest revision |
| Automatic improvement | Proposal-only review; trusted external evaluator controls workflow promotion | Project-owned tasks and meaningful held-out measures |
| Multi-user security | Local filesystem trust, project scopes, provenance labels | Authenticated service, tenant isolation, enforced policy, operational review |
| Retrieval scale | Lexical retrieval with bounded context | Benchmarks for larger stores and a justified search upgrade |
| Privacy | Minimal metadata, common credential-pattern filter | Project-specific data policy; filter is not comprehensive DLP |
| Background operation | One CLI invocation with time/lease bounds; scheduler external | Persistent scheduler registration, logs, credentials, and operational ownership |
| Hook reliability | Best effort; metadata can be skipped under contention | Durable event queue if lossless accounting is required |
| Non-Git discovery | Explicit host root respected; nearest store fallback without one | Initialize separate project stores or provide the host root |
| Windows process trees | Immediate worker termination only | Native process-tree supervision and integration checks |
| Store upgrades | First public schema 1; unpublished development stores rejected | Versioned migration when a released schema changes |
| Public review | PR #1 open; `@codex review` requested; code/security reviews running | Completed reviews and fixes for reproducible findings |

No open reproducible failure remains in the local release suite. Passing those tests does not establish complete coverage of the speech's architecture. The external checks above remain pending and are not represented as passing tests.

## Speech coverage follow-up — 6 November 2026

The [full speech audit](SPEECH_AUDIT.md) identified incomplete features that the original source map did not distinguish clearly enough:

- Rich sanitized session-trace ingestion, including tool input/output, skills used, and explicit user corrections.
- Memory-only maintenance without new events, eventual coverage of older memories, and versioned merge/retire proposals.
- Configurable priorities and exclusions for the standalone reviewer.
- Parallel reviewer orchestration and cross-batch/fleet aggregation.
- Authenticated organization/team/individual scopes and matching review-input permissions.

The first three are the highest-value local feature backlog; the last two remain scale-dependent architectural work. These are not implemented by the current 51-test suite. Active scheduling and measured real-task benefits remain separate deployment/evaluation gaps.
