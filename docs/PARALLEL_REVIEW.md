# Parallel session review

`dream-parallel` runs independent reviewers concurrently over one bounded event packet. It groups events by session, so a session is never split between reviewers. Each reviewer receives only its assigned events, the packet's already-selected project memories, and the same review-policy snapshot. The original prepared packet remains the authority for final submission.

```bash
python3 skills/universal-workflow/scripts/uw.py --project /absolute/project dream-parallel \
  --engine claude --workers 2 --retries 1 --timeout 180 --out parallel-result.json
```

`--engine codex` uses the Codex transport. An authenticated native CLI is required. No worker starts merely because the skill is installed. This command reviews events; the separate maintenance workflow handles memory-only sweeps.

## Bounds and failure behavior

- One to four workers; zero to two retries per shard. Actual shard count is bounded by the number of sessions, and total calls cannot exceed twelve.
- One wall-clock deadline of 1–300 seconds covers all worker calls and retries. SQLite setup and cleanup retain their existing bounded database wait behavior.
- Workers use isolated temporary working directories and never receive a `Store` object or share its SQLite connection. Only the coordinating thread prepares and submits the packet.
- A shared dream lease prevents overlap with the single-reviewer worker. Failure, timeout, or interruption cancels other subprocesses, waits for cleanup, and releases the lease. POSIX process groups are stopped; Windows guarantees immediate-child termination only, as in the original runtime.
- Invalid JSON, invalid proposals, and evidence from another shard are rejected. An event that exists elsewhere in the complete packet is still invalid evidence for a reviewer that did not receive it.
- No proposal is submitted and no event watermark is consumed until every shard succeeds and all results pass validation. A prepared audit record can remain after failure; the same events remain eligible for a later review.

Native tool availability, actual model quality, and CLI authentication are environment-specific. Offline tests use controlled subprocesses; they do not establish improved model accuracy or savings.

## Deterministic reconciliation

Exact agreements on target key, kind, body, and TTL are merged. Evidence references and reviewer provenance are deduplicated; the denominator is the number of unique sessions in the complete original packet, not the sum of per-shard estimates or a model-supplied score. Results are ordered deterministically regardless of worker completion order.

Conflicting content for one target, identical content assigned to different keys, more than twenty combined evidence references, or more candidates than the allowed proposal cap return `status: needs_reconciliation`. No candidate is silently chosen or discarded, and the event batch remains unconsumed. The output contains the authentic `packet`, candidate `response`, explicit `conflicts`, and calculated `statistics`.

To reconcile, inspect the evidence, choose or rewrite a bounded response, preserve valid evidence references, and save the returned `packet` and reconciled `response` as separate JSON files. Then use the existing submission command:

```bash
python3 skills/universal-workflow/scripts/uw.py --project /absolute/project submit \
  --packet reviewed-packet.json --response reconciled-response.json
```

Submission only creates pending proposals. Normal hash checks, explicit acceptance, and the independent workflow-evaluation gate remain in force. The orchestrator does not claim to detect every semantic contradiction between differently worded candidates and does not automatically promote any memory.

## Python interface

`uw_parallel.run_parallel(store, engine=None, force=False, workers=2, retries=0, timeout=180)` returns the same structured result as the CLI. `shard_packet()` and `aggregate_responses()` provide the deterministic preparation and reconciliation stages. `uw.review_packet()` is the one-reviewer transport shared by single and parallel review; `uw.review_prompt()` assembles its instructions from packet data.
