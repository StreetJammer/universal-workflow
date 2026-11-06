# Source coverage and implementation boundaries

Source: the user-provided approximately 31-minute AI DevCon transcript. The speaker identifies herself as Lamis and as a member of Anthropic's applied AI team. The original transcript is not redistributed. Speaker identity, event date, and historical product availability were not independently verified; this table summarizes the concepts without reproducing the transcript.

| Transcript range | Concept | Current status and evidence |
| --- | --- | --- |
| 00:00–03:15 | Useful agents need task and organizational context | Implemented as host instructions: inspect current artifacts, preserve goals, retrieve relevant lessons; organization-wide context is supplied externally |
| 03:20–04:35 | Short human-readable instructions; avoid growing always-loaded context | Implemented: compact SKILL.md, optional managed project blocks, on-demand references |
| 04:39–05:24 | Agents choose when to read, write, and update memories during work | Implemented as skill guidance plus `recall`/`remember`; depends on host execution, not an autonomous daemon |
| 05:27–07:05 | Skills and progressive disclosure | Implemented: frontmatter and separate task/domain/reference files; host controls discovery |
| 07:09–08:28 | Filesystem-shaped memory, indexing, ordinary search | Partial/design difference: SQLite is authoritative; Markdown/index are one-time exports, not a live writable memory tree; lexical recall scans the local store |
| 08:32–09:58 | Concurrent, stale, incorrect, or injected memories | Partial: transactional updates, expiry, and untrusted-data instructions implemented; semantic truth and malicious-content detection are not guaranteed |
| 10:19–10:51 | Versions, rollback, session/source provenance, authors | Implemented: history, revision hashes, actor labels, and evidence/event references; identities and source claims are not authenticated |
| 10:54–11:40 | Hash-check writes and reread on conflict | Implemented: comparison and mutation in one transaction; host must reconcile a rejected update |
| 11:46–12:41 | Organization/team/individual permission hierarchy | Not implemented as an access-control system: project/scratch scopes only; scratch is not isolated per agent; organization rules stay outside the writable store |
| 12:45–13:24 | Portable clean interface across products | Implemented locally: JSON CLI, Markdown/JSON snapshots, backup, host installers; no remote shared-memory service |
| 13:29–14:55 | Better repeat-task accuracy, latency, and cost | Measurement fields and evaluator gate exist; no real before/after benchmark establishes these benefits for this package |
| 15:04–17:43 | Separate task work from cross-session curation | Partial: a separate one-shot worker exists; no scheduler is installed, and old memories receive no exhaustive maintenance pass |
| 17:49–19:28 | Batch review of memory plus session transcripts | Partial: worker reviews bounded recorded outcome summaries and memory snapshots; no session-transcript importer or full trace reconstruction |
| 19:32–21:17 | Discover missing knowledge, tool-configuration failures, style problems | Partial: model guidance plus tool/error labels; richer tool input/output, skills-used, and correction records have no dedicated schema/importer |
| 21:22–22:04 | Orchestrator distributes transcripts to subagents | Not implemented: one reviewer CLI process; no sharding, reviewer-result aggregation, or fleet orchestration |
| 22:04–22:29 | Organization-specific steering for memory and dreaming | Partial: current host can follow user instructions; standalone worker prompt is fixed, with no project priorities/exclusions configuration |
| 22:35–23:24 | Aggregate prevalent patterns, examples, reasons, accept/reject | Partial: evidenced proposals and accept/reject implemented; session denominators cover the selected batch only, with no cross-batch/fleet aggregation |
| 23:29–24:43 | Fast foreground learning plus deeper asynchronous consolidation | Partial: both execution paths exist; activation is external and no measured net cost benefit is established |
| 24:51–26:04 | Start simple and support non-code work | Implemented as domain guidance for research, documents, and operations; destination tools still perform those tasks |
| 26:07–26:22 | Consolidate, cut obsolete material, add missing knowledge, organize | Partial: add/update, manual expiry, history/rollback; no merge/retire proposal actions, exhaustive sweep, or memory-only worker review without new events |
| 27:12–28:25 | Speaker recommends a managed enterprise API | Historical source claim, not a required dependency or implemented adapter; current API availability is not established by the transcript |
| 28:32–29:47 | Match review transcript selection to source permissions | Not implemented as multi-user authorization: project packets are isolated, but no user/group ACL filter or delegated permission matching exists |
| 29:53–30:57 | Put reliable primitives in code rather than prompting alone | Implemented: transactions, schema checks, hashes, versioning, bounded subprocesses, and executable evaluation gates |

The full talk is mapped above; mapping a concept does not mean it is implemented. Version 0.1.0 is a local single-project foundation, not the talk's complete production dreaming architecture. Test success measures implemented behavior, not completeness against the speech.

Automatic task prompting and evaluator-gated prompt improvement were additional user requests. They are distinct from the speech's memory-curation loop. The prompt assembler preserves the original request; host reasoning and real task-specific evaluation remain necessary. No model weights are trained.

The supplied transcript retains its original rights. The MIT license covers the original implementation and documentation, not the transcript.
