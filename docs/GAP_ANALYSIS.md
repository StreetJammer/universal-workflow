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
| Database backups widened POSIX permissions and could overwrite a racing destination | Reserve a new file with `O_EXCL` and mode `0600` before SQLite opens it | Initial permission check and competing-writer preservation |
| Non-object config caused a traceback | Validate config shape before access | Structured JSON error for `[]` |
| Concurrent overlapping reviews reused consumed events | Reject already-consumed sequence ranges | Second overlapping packet rejected |
| Child projects inherited parent context | Stop at Git/worktree or explicit host project boundary | Independent Git and non-Git project probes |
| Identical completion text collapsed separate turns | Use prompt identity; UUID fallback for older hosts | Five distinct turns count five; same-turn retry deduplicates |
| Hooks waited longer than their host deadline | One-second database contention budget, diagnostic, best-effort recording | Locked store returns promptly without blocking completion |
| Missing working directory silently used process cwd | Require absolute existing cwd and session identity for records | Missing/relative/invalid cwd cannot write |
| Instruction wiring changed modes and newline bytes | Preserve existing bytes and POSIX mode | CRLF prefix, permissions, unrelated settings, idempotence |
| A later target conflict left an unexplained partial install | Preflight all destinations/blocks; report completed mutations on later filesystem errors | Second-target and managed-block conflicts leave first target untouched |
| Existing destinations accepted injected import modules or linked directories alongside identical expected files | Validate the full entry set and types; reject extras and links without following them | Harmless injected-module reproduction, both-host preflight, plugin export, symlink/junction, bytecode/cache, FIFO, and missing/swapped-entry regressions |
| Portable exports lacked licensing metadata | Include MIT terms and author/license/repository manifest fields | Generic and plugin exports contain matching license |

## Deliberate limits and next release gates

| Area | Current boundary | Evidence needed to expand it |
| --- | --- | --- |
| Native integrations | Claude/Codex transport mocks and documented formats | Authenticated native CLI installation and real host smoke checks |
| Operating systems | Local Linux/Python 3.12 run; six hosted jobs passed for merged PR #1 | All six jobs must pass on the latest revision |
| Automatic improvement | Proposal-only review; trusted external evaluator controls workflow promotion | Project-owned tasks and meaningful held-out measures |
| Multi-user security | Optional authenticated loopback service with enforced namespaces and review ACLs; direct CLI still trusts its owner | Dedicated OS account/ACL deployment, enterprise identity/TLS integration and operational review |
| Retrieval scale | Lexical retrieval with bounded context | Benchmarks for larger stores and a justified search upgrade |
| Privacy | Minimal metadata, common credential-pattern filter | Project-specific data policy; filter is not comprehensive DLP |
| Background operation | One CLI invocation with time/lease bounds; scheduler external | Persistent scheduler registration, logs, credentials, and operational ownership |
| Hook reliability | Best effort; metadata can be skipped under contention | Durable event queue if lossless accounting is required |
| Non-Git discovery | Explicit host root respected; nearest store fallback without one | Initialize separate project stores or provide the host root |
| Windows process trees | Immediate worker termination only | Native process-tree supervision and integration checks |
| Store upgrades | First public schema 1; unpublished development stores rejected | Versioned migration when a released schema changes |
| Public review | PR #1 merged after completed code/security reviews and passing CI; feature PRs reviewed separately | Completed reviews on each latest feature revision and fixes for reproducible findings |

No open reproducible failure remains in the local release suite. Passing those tests does not establish complete coverage of the speech's architecture. The external checks above remain pending and are not represented as passing tests.

## Speech coverage follow-up — 6 November 2026

The [full speech audit](SPEECH_AUDIT.md) identified incomplete features that the original source map did not distinguish clearly enough:

- Completed locally: rich sanitized session-trace ingestion, including tool summaries, skills used, explicit user corrections and source/turn references. See [session traces](SESSION_TRACES.md).
- Completed locally: memory-only maintenance without new events, eventual key-order sweeps, cross-batch candidates, and versioned merge/retire proposals. See [maintenance](MEMORY_MAINTENANCE.md).
- Completed locally: configurable include/focus/exclude priorities, deterministic limits, and stale-policy checks for event and maintenance reviews. See [review priorities](REVIEW_PRIORITIES.md).
- Completed locally: bounded parallel session reviewers, evidence aggregation, global session denominators, and explicit conflict reconciliation. See [parallel review](PARALLEL_REVIEW.md).
- Completed locally: authenticated organization/team/individual scopes, private scratch, and matching review-input permissions through an optional protected service. See [access control](ACCESS_CONTROL.md).

All five changes have dedicated executable tests and separate feature PRs. See [validation](VALIDATION.md) for current test evidence. Active scheduling and measured real-task benefits remain separate deployment/evaluation gaps.
