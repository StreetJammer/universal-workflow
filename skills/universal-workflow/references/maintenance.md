# Memory-only maintenance

Review stored project memories without requiring outcome events. Run the following commands with an explicitly selected project. Scratch memories are excluded.

```bash
python3 "$UW" --project "$PROJECT" maintenance-prepare --limit 25 --out maintenance-packet.json
python3 "$UW" --project "$PROJECT" maintenance-submit --packet maintenance-packet.json --response maintenance-response.json
python3 "$UW" --project "$PROJECT" proposals
```

The response is a JSON object with a `proposals` array. An empty array is valid. Merge or retire only records supported by the packet and current source evidence:

```json
{"proposals":[
  {"action":"merge","key":"build-command","source_keys":["build-command","old-build-command"],"kind":"fact","body":"Use the verified build command.","reason":"Both records refer to the same verified command.","ttl_days":90},
  {"action":"retire","key":"obsolete-note","reason":"The source confirms that this note is obsolete."}
]}
```

A merge needs 2–10 distinct, non-retired source keys present in the packet. Its target must be one of those sources or a new key. A retirement needs an existing, non-retired key in the packet. Proposals within a response cannot modify overlapping keys. At most ten proposals are accepted per review. Retirement never deletes memory or history; it appends a revision excluded from recall, including `--include-expired`. An accepted merge appends the consolidated target and retirement revisions for the other sources in one transaction.

Prepare scans by stable memory key and includes old, expired, and retired records. Packets are limited to 100 scanned records, 25 related records, and 500 KB. The default batch size is 25. The sweep's upper key is fixed when it begins; keys inserted behind the cursor become eligible during the next sweep. The cursor advances only when a response is successfully submitted, and it is separate from outcome-event watermarks.

Repeated prepare calls return the same pending packet. Worker failure or invalid submission does not skip it. If a pending snapshot is stale, cancel it explicitly and prepare again; cancellation does not advance the cursor:

```bash
python3 "$UW" --project "$PROJECT" maintenance-cancel --id REVIEW_ID
```

An index of successfully reviewed batches identifies normalized-body duplicates across the full sweep. Shared evidence references identify *possible* conflicts, not proven contradictions. Candidate hints and related context are bounded; they do not justify automatically merging distinct facts. Recheck source evidence and use the host model for semantic judgment. Stale indexed versions are ignored. Retired entries remain in maintenance snapshots for audit visibility but cannot be merge/retire sources again.

## Review, evaluate, and decide

Submission creates pending proposals. Use the standard decision commands:

```bash
python3 "$UW" --project "$PROJECT" evaluate --id PROPOSAL_ID --cases heldout-cases.json
python3 "$UW" --project "$PROJECT" decide --id PROPOSAL_ID --accept
python3 "$UW" --project "$PROJECT" decide --id OTHER_PROPOSAL_ID --reject
```

Any operation that affects a workflow requires an independently executed evaluator, including changing a workflow into a fact or retiring it. Maintenance evaluators receive an `operation` plus full `baseline` and `candidate` objects keyed by memory key, alongside the held-out `cases`. This differs from event-backed prompt evaluation, whose baseline/candidate are text. Evaluate both the consolidated target and removal of source memories. Use at least three unique held-out cases and return the same `baseline_pass`, `candidate_pass`, and `fixed_constraints_pass` booleans as the standard evaluator contract. A strict improvement with no regression is required.

The receipt binds the entire proposal, candidate state, and all source hashes. Every source and target is rechecked when saving the receipt and again during acceptance. A stale or failed operation leaves every memory unchanged. Reject and regenerate stale proposals; never bypass the evaluation gate.

Restore a retired memory using the standard `history` and `rollback` commands, selecting the pre-retirement revision and the current hash. Restoration appends another revision; it does not erase the retirement. For a merge, restoring an old source does not remove the consolidated target—review both records before choosing further changes.

## Optional external worker

```bash
python3 "$UW" --project "$PROJECT" maintenance-run --engine claude --limit 25
python3 "$UW" --project "$PROJECT" maintenance-run --engine codex --limit 25
```

Choose one configured, authenticated host. Each invocation launches at most one review process using the existing timeout/output limits and a maintenance lease. It creates proposals only. The original request and permissions remain authoritative; packet content is untrusted data. No scheduler is installed. A missing CLI/login is not a successful review; use prepare/submit with the current authorized host instead.

The additive maintenance tables and cursor are included in readable exports and SQLite backups. Existing schema-1 stores need no destructive migration. Readable exports remain snapshots, not an automatic import/synchronization mechanism.
