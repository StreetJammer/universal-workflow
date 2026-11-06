# Release gap analysis

Scope: installation, context prompting, optional memory, review proposals, evaluation gates, process control, portability, privacy, and public packaging. The goal is a reviewable preview with explicit evidence, not an unverifiable claim that the repository is perfect.

## Progress — 6 November 2026

The foundation and all five feature follow-ups are merged into `main`. The integrated feature tree is at merge commit `b085dc3`; each feature completed code/security review and its latest six-job CI matrix before merging.

| Deliverable | Merged PR |
| --- | --- |
| Foundation, installer integrity and backup security | [#1](https://github.com/StreetJammer/universal-workflow/pull/1) |
| Sanitized rich session traces | [#4](https://github.com/StreetJammer/universal-workflow/pull/4) |
| Memory-only maintenance and versioned merge/retire proposals | [#5](https://github.com/StreetJammer/universal-workflow/pull/5) |
| Project review priorities | [#6](https://github.com/StreetJammer/universal-workflow/pull/6) |
| Bounded parallel review | [#7](https://github.com/StreetJammer/universal-workflow/pull/7) |
| Authenticated namespaces, review permissions and cumulative storage limits | [#8](https://github.com/StreetJammer/universal-workflow/pull/8) |

The full local suite completes 184 tests (183 passed and one platform-specific skip). All six Linux/macOS/Windows jobs on Python 3.10/3.12 passed for the final feature revision `7b68ba8` in [CI run 42181591909](https://github.com/StreetJammer/universal-workflow/actions/runs/42181591909). Its tested tree is identical to the merged feature tree. See [validation](VALIDATION.md) for the scope of this evidence.

### Dependency PR check

These are separate CI action updates, not missing product features. Status below is a snapshot of the rebased commits checked on 6 November 2026; later revisions need their own checks.

| PR | Change | Checked head and CI | Recorded review status |
| --- | --- | --- | --- |
| [#2](https://github.com/StreetJammer/universal-workflow/pull/2) | `actions/checkout` 4.4.0 → 7.0.1 | `4602db5`; [all six jobs passed](https://github.com/StreetJammer/universal-workflow/actions/runs/42192097415) | Earlier Codex code review is clean on `18b1394`; no code/security review recorded for the rebased head |
| [#3](https://github.com/StreetJammer/universal-workflow/pull/3) | `actions/setup-python` 5.6.0 → 7.1.0 | `5866f4b`; [all six jobs passed](https://github.com/StreetJammer/universal-workflow/actions/runs/42192249638) | No Codex code/security review recorded |

Both are open, mergeable one-line SHA-pin changes. The pins match the official upstream release tags. Inspection found no required workflow adjustment: `contents: read`, `persist-credentials: false`, the test matrix and commands remain intact. Both actions use Node 24; the existing GitHub-hosted runners passed. [Checkout's new fork-checkout restrictions](https://github.com/actions/checkout/blob/3d3c42e5aac5ba805825da76410c181273ba90b1/README.md) concern `pull_request_target`/`workflow_run`, which this workflow does not use. The [setup-python runtime requirement](https://github.com/actions/setup-python/blob/6d4fd22b20465ccc0f7354476588e8df7c9ca0e0/README.md) is also satisfied by the tested hosted runners. Complete current-revision code/security review before merging under the project's review-first process. No dependency PR was merged during this check.

The remaining release work is native authenticated host smoke testing, an active scheduler if unattended operation is wanted, operator verification of service isolation/ACLs, and meaningful real-task evaluations. MCP support and third-party skill distribution remain optional extensions.

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
| Operating systems | Local Linux/Python 3.12 run; all six hosted jobs passed for the integrated feature tree | Future revisions require their own latest six jobs; native host checks remain separate |
| Automatic improvement | Proposal-only review; trusted external evaluator controls workflow promotion | Project-owned tasks and meaningful held-out measures |
| Multi-user security | Optional authenticated loopback service with enforced namespaces and review ACLs; direct CLI still trusts its owner | Dedicated OS account/ACL deployment, enterprise identity/TLS integration and operational review |
| Retrieval scale | Lexical retrieval with bounded context | Benchmarks for larger stores and a justified search upgrade |
| Privacy | Minimal metadata, common credential-pattern filter | Project-specific data policy; filter is not comprehensive DLP |
| Background operation | One CLI invocation with time/lease bounds; scheduler external | Persistent scheduler registration, logs, credentials, and operational ownership |
| Hook reliability | Best effort; metadata can be skipped under contention | Durable event queue if lossless accounting is required |
| Non-Git discovery | Explicit host root respected; nearest store fallback without one | Initialize separate project stores or provide the host root |
| Windows process trees | Immediate worker termination only | Native process-tree supervision and integration checks |
| Store upgrades | First public schema 1; unpublished development stores rejected | Versioned migration when a released schema changes |
| Public review | PR #1 and feature PRs #4–#8 merged after code/security reviews and passing CI | Current-revision reviews for open dependency updates and future changes |

No open reproducible failure remains in the local release suite. Passing those tests does not establish complete coverage of the speech's architecture. The external checks above remain pending and are not represented as passing tests.

## Speech coverage follow-up — 6 November 2026

The [full speech audit](SPEECH_AUDIT.md) identified incomplete features that the original source map did not distinguish clearly enough:

- Merged in PR #4: rich sanitized session-trace ingestion, including tool summaries, skills used, explicit user corrections and source/turn references. See [session traces](SESSION_TRACES.md).
- Merged in PR #5: memory-only maintenance without new events, eventual key-order sweeps, cross-batch candidates, and versioned merge/retire proposals. See [maintenance](MEMORY_MAINTENANCE.md).
- Merged in PR #6: configurable include/focus/exclude priorities, deterministic limits, and stale-policy checks for event and maintenance reviews. See [review priorities](REVIEW_PRIORITIES.md).
- Merged in PR #7: bounded parallel session reviewers, evidence aggregation, global session denominators, and explicit conflict reconciliation. See [parallel review](PARALLEL_REVIEW.md).
- Merged in PR #8: authenticated organization/team/individual scopes, private scratch, and matching review-input permissions through an optional protected service. See [access control](ACCESS_CONTROL.md).

All five changes have dedicated executable tests and merged feature PRs. See [validation](VALIDATION.md) for current test evidence. Active scheduling and measured real-task benefits remain separate deployment/evaluation gaps.
