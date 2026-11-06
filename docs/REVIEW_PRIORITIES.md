# Project review priorities

`review-policy` configures what a project's reviewer should pay attention to. It applies to manual `prepare`/`submit` reviews the external `dream-run` worker, and memory-only maintenance. It does not rewrite the user's task, grant tool permissions, or change the workflow evaluator requirement.

## Show and update

Read the current policy and its revision-bound hash:

```bash
python3 skills/universal-workflow/scripts/uw.py --project /absolute/project review-policy
```

Create an explicitly selected JSON file, for example:

```json
{
  "schema": 1,
  "include": ["build reliability", "game behavior"],
  "focus": ["repeated test failures", "restart state"],
  "exclude": ["cosmetic preferences"],
  "limits": {
    "max_proposals": 5,
    "max_memories": 50,
    "memory_char_budget": 20000
  }
}
```

Then copy the current hash into the update command:

```bash
python3 skills/universal-workflow/scripts/uw.py --project /absolute/project review-policy \
  --file priorities.json --expected-hash CURRENT_HASH
```

The policy is stored as authoritative metadata in the existing project database. No schema migration or private native-memory access is needed. Do not add a competing `review_policy` block to `config.json`; use this command. A local project owner controls the policy through existing filesystem access. This command is not an authenticated multi-user service.

## Meaning and bounds

| Field | Meaning | Limit |
| --- | --- | --- |
| `include` | Desired proposal subject areas; empty means all areas | Up to 12 unique, single-line topics, 160 characters each |
| `focus` | Areas to prioritize within the review | Same topic bounds |
| `exclude` | Avoid proposing lessons solely about these topics | Same topic bounds; cannot overlap include/focus |
| `limits.max_proposals` | Maximum proposals accepted from one review; zero allows a review with no proposals | 0–10; default 10 |
| `limits.max_memories` | Maximum project memories selected for a review | 1–100; default 100 |
| `limits.memory_char_budget` | Memory context character budget | 1,000–40,000; default 40,000 |

The entire normalized policy is limited to 6,000 UTF-8 bytes. Unknown fields, credential-like text, duplicate or contradictory topics, control characters, malformed values, and out-of-range limits are rejected before any write. Omitted fields use their defaults.

Topic lists are **reviewer guidance**, not a deterministic content classifier. They do not remove evidence from a packet, authorize any action, or suppress security and fixed-constraint violations. Every selected event still contributes to the review's denominators and watermark. Proposal-count and memory-context limits are enforced by code; semantic adherence to topical preferences depends on the reviewer and must be assessed in actual host use.

## Concurrency and pending work

Policy updates compare the supplied hash and write the next revision in one transaction. A stale writer must reread and reconcile. Reapplying the same normalized policy with the current hash is a no-op. Changing a policy and later restoring its contents still produces a new revision.

Every new review packet records its policy snapshot. The runtime checks that snapshot when submitting proposals, before launching an evaluator, when saving the evaluator receipt, and when accepting a proposal. A changed policy invalidates those operations; prepare a fresh review instead. Rejecting an old proposal is still allowed. Legacy packets without a snapshot are usable only before the first policy update.

Maintenance includes both scanned and related memories in these context limits. It stops before a record that would exceed the budget, leaving it for the next batch. If the first record exceeds the configured budget, increase the limit or explicitly reconcile that memory; it is never silently skipped. After a policy update, cancel a stale prepared maintenance packet with `maintenance-cancel` before preparing again.

Reviewers and writable memory cannot edit this metadata through proposal fields. These checks do not defend against another process with direct access to the database; use OS/service isolation for untrusted identities.

## Verification

The deterministic tests exercise CLI updates, concurrent compare-and-swap, default behavior, bounded context, unchanged evidence/watermarks, worker prompt construction, malformed/secret-bearing policies, snapshot tampering, update-and-revert conflicts, and policy changes before/during evaluation and acceptance. Worker tests use a controlled transport substitute and do not claim live model compliance or native CLI validation.

```bash
python3 -m unittest discover -s skills/universal-workflow/scripts -p test_policy.py -v
```
