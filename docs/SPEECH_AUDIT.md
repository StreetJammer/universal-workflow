# Speech-to-implementation audit

Original audit: 6 November 2026 against the complete supplied transcript (00:00–30:57), using baseline `79c0037`. The completion table is updated as executable features are implemented; the original probes below are historical evidence, not claims about the current branch.

## Feature completion

| Speech gap | Current implementation |
| --- | --- |
| Detailed session analysis | Implemented: [selected trace importer](SESSION_TRACES.md), sanitized rich evidence in review packets, atomic/idempotent imports and project checks. Native history-format adapters and measured model gains remain separate. |
| Memory cleanup | Implemented: [resumable memory-only sweeps](MEMORY_MAINTENANCE.md), cross-batch candidates, versioned merge/retire proposals, independent workflow evaluation and conflict checks. |
| Custom review priorities | Implemented: [versioned project guidance](REVIEW_PRIORITIES.md), bounded proposal/context limits and stale-policy checks across event and maintenance reviews. |
| Parallel dreaming | Pending the dedicated orchestration change. |
| Permission hierarchy | Pending the dedicated access-control change. |

**Finding: the core local memory workflow is implemented, but the speech's complete production dreaming system is not.** The earlier source map covered all concepts without clearly distinguishing code, host instructions, and deferred architecture. That distinction is now explicit in the [revised source map](../skills/universal-workflow/references/source-map.md).

## Working foundation

The package implements progressive-disclosure instructions, task prompting, project memory, source/actor labels, versions, rollback, transactional hash checks, expiry, a portable CLI/export, bounded cross-session review, evidence-linked proposals, and accept/reject decisions. Workflow promotion also has an independent evaluator gate, an additional feature requested alongside the speech rather than a claim from it.

The original baseline contained 48 automated checks; the expanded current suite is recorded in [validation](VALIDATION.md). These tests verify implemented behavior. They do not establish complete feature parity with the talk, real model learning gains, native host compatibility, or an active background service.

## Gaps that matter

| Priority | Speech reference | Missing or partial capability | What actually exists | Concrete completion criterion |
| --- | --- | --- | --- | --- |
| Completed locally | 18:32–18:55; 20:42–20:57; 21:42–21:50 | Rich, sanitized session evidence | `trace-import` preserves selected messages, tool summaries, skills, corrections and provenance in the event stream | Ten regressions cover sanitization, whole-batch rollback, limits, project isolation and review evidence retention |
| Completed locally | 17:37–17:43; 26:07–26:22 | Complete memory maintenance | Independent persistent sweep cursor, cross-batch candidate index, merge/retire proposals and evaluator gates | Seventeen regressions cover older memories, event-free review, concurrency, rollback, source/target conflicts and batch progress |
| Completed locally | 22:04–22:29 | Configurable review priorities | Versioned include/focus/exclude guidance and deterministic context/proposal limits | Policy snapshots, concurrent updates and stale submit/evaluation/acceptance regressions; guidance cannot grant permissions |
| Later / scale | 21:56–22:04; 22:35–23:12 | Parallel reviewers and aggregate patterns | One bounded reviewer; evidence counts use only its selected batch | Bounded transcript sharding, independent reviewer results, deterministic evidence deduplication, aggregated denominators, orchestrator reconciliation, and retry limits |
| Later / multi-user | 11:46–12:41; 28:32–29:47 | Organization/team/agent permissions inherited by reviews | Project/scratch labels and OS trust; no authenticated hierarchy or per-agent scratch isolation | Authoritative identity and ACL checks on every read, write, and review-input selection, with tests that reject cross-user leakage |

Parallel orchestration and enterprise authorization are explicitly deferred architecture, not hidden completed features. The first three gaps now have executable implementations with dedicated regressions.

## Operational and design differences

- **Background operation (18:11–18:18; 23:29–24:19):** the worker is executable, but a persistent scheduler and authenticated host must be connected. A cron example is not an active service. Automatic overnight improvement has not been activated.
- **Filesystem memory (07:09–08:14):** the package uses SQLite plus readable Markdown snapshots. The talk describes a filesystem-shaped live memory surface. Direct Markdown editing and automatic synchronization are not supported; the CLI protects version/concurrency semantics.
- **Search and complete visibility:** lexical recall scans the local store. Event reviews see bounded recent context. The separate maintenance cursor eventually sweeps stable project-memory keys, with indexed candidate comparisons across batches; it does not add cross-project search.
- **Claimed benefits (13:56–14:29; 24:25–24:43):** latency/token/cost fields exist, but no before/after task benchmark proves accuracy gains or net savings. Those benefits must be measured with actual host runs.
- **Managed API Q&A (27:12–28:25):** this was a provider-specific recommendation. Reimplementing or depending on that API is not necessary for a provider-neutral tool. Its existence or current behavior cannot be inferred from a historical transcript.
- **Other work (25:45–26:04):** research, document, and operations profiles are included. They guide the host; they do not supply every renderer, engine, browser, or service integration.

## Original baseline probes

These used a disposable project and the current runtime; no live model, account, or user project was used.

1. Create a memory, expire it, then call `prepare(force=True)` with no outcome events. Observed `due: false`, `new_events: 0`, `required: 5`. This confirms the missing memory-only worker path.
2. Record a fixture with tool/error labels and extra `skills_used`, `tool_input_summary`, `tool_output_summary`, and `user_correction` fields. The stored event retains the documented summary/evidence/tool/error fields; the extra structured fields are discarded. This confirms that rich trace ingestion is not implemented. It does not show that all such information is impossible to put in a manual summary.
3. Prepare and submit that event batch, then force another review. It again reports no new events despite the expired memory remaining in the store. In the event-backed packet the expired memory is visible, so the limitation is review eligibility and coverage rather than total exclusion of expired memory.

## Corrections made by this audit

- Revised the full timestamp map to label partial and absent capabilities explicitly.
- Removed the misleading suggestion that the bundled worker can perform a memory-only review.
- Added the missing feature backlog to the release gap analysis and linked this audit from the README.
- Kept implementation and test claims unchanged. No new capability is claimed merely because it appears in this report.

PR #1's **0.1.0 preview** foundation is merged. The feature changes above extend it through separate review PRs. Native host behavior, active scheduling, and real-task learning gains require independent deployment/evaluation evidence.
