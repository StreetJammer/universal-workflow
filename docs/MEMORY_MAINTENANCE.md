# Memory maintenance without new events

This change adds a memory-only review path to Universal Workflow. It can inspect older and expired memories even when no new outcome events exist, and can propose versioned merges or retirements.

Use the [maintenance reference](../skills/universal-workflow/references/maintenance.md) for the CLI commands, response format, full-state evaluator contract, retries, and restoration procedure.

## Implemented behavior

- Separate, persistent key-order sweeps with a fixed upper bound and transactional cursor advancement after successful submission.
- A single reusable pending packet, explicit cancellation without skipping, and independent outcome-event watermarks.
- Indexed duplicate candidates and possible conflicts from earlier batches; no full-store transcript dump into each review.
- Merge/retire proposals with all source/target revision hashes, atomic multi-memory acceptance, and preserved rollback history.
- Workflow evaluation for every affected workflow, including retirement and conversion to a non-workflow kind.
- Bounded external review worker, additive schema-1 tables, and export/backup coverage.

## Validation

Twenty-one deterministic regression tests cover 125-memory sweeps, late insertions, old/expired memories, concurrent prepares, retry/cancel behavior, cross-batch duplicates and possible conflicts, versioned retirement/restore, merge atomicity under injected database failure, stale sources/targets, invalid/tampered/foreign packets, evaluator gates, source changes during evaluation, event-watermark independence, exports, CLI round trips, and large Unicode packets. They also verify that accepted merge/retire payloads exactly equal what the evaluator received, that an expiry constraint can block a merge, and that legacy unfrozen proposals fail closed.

The complete local suite contains 81 tests on the initial-release base: 80 pass on Linux and the Windows junction test is explicitly skipped. All maintenance tests pass on that base. Root integration adds other feature suites separately. Fixture evaluators test the gate's behavior; they do not establish real model learning gains or domain-level accuracy. Native authenticated host execution and persistent scheduling remain deployment checks.

## Scope

Candidates are review aids. Normalized body equality suggests duplication; shared evidence references suggest related or potentially conflicting facts. Semantic correctness still requires the reviewer and authoritative source evidence. The implementation does not silently retire expired records, delete history, or automatically approve proposals. Permissions remain those of the enclosing store/access boundary.
