# Add Universal Workflow skill, Claude plugin, and verified project-memory runtime

Agents repeatedly rebuild task context and can adopt lessons without traceable evidence. This adds a reusable workflow that preserves the original request, selects relevant context, verifies results, and records project-scoped lessons. It works as a portable skill, with a Claude Code plugin and local Codex project installer.

The optional standard-library Python runtime adds transactional revision history, rollback, expiry, exports, bounded review workers, and proposal approval. Workflow changes require a passing independently executed evaluator. Native memory remains the default for ordinary recall.

Validation: 53 local deterministic tests plus structural checks, two separate task trials, and targeted concurrency/packaging audits. Fixes include rollback-token reuse, late evaluation races, oversized Unicode batches, initialization races, Windows delete-pending lock contention, private backup creation, project-boundary leaks, hook event deduplication, and instruction-file preservation. See [validation](VALIDATION.md) and [gap analysis](GAP_ANALYSIS.md).

This is a 0.1.0 preview. Native Claude/Codex CLI checks remain pending. Six hosted CI jobs run on the public PR; require all to pass on the latest revision. No scheduler or model provider is activated by installation. The original third-party transcript and all private runtime data are excluded.

After opening the PR, post `@codex review` as a separate conversation comment. Check that the repository is enabled for Codex reviews, inspect returned findings, reproduce bugs, fix them on the PR branch, and rerun relevant checks before merge. Do not treat absence of a review response as approval.
