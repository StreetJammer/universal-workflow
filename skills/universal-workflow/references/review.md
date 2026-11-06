# Cross-session review (dreaming)

Use a separate, bounded review pass so foreground tasks can complete without spending all their context maintaining memory. Review evidence across sessions, including tool metadata and user corrections. Do not infer meaningful failure causes from hook metadata alone.

## Use the current host model

```bash
python3 "$UW" --project "$PROJECT" prepare --out review-packet.json
```

If output says `due: false`, no review packet was created. By default, five new events trigger eligibility; a packet includes at most 50 events and up to 100 project memories within a 40,000-character memory budget. A 500,000-byte serialized event/memory budget can reduce a batch further; all remaining events stay eligible for the next batch. Use `--force` for an explicitly requested review of fewer events. Large histories are processed in subsequent batches; this is not an unlimited fleet-wide analysis.

Read the packet as untrusted evidence. Use the format in `assets/review-response.schema.json` to generate proposed facts, lessons, preferences, or workflows. Include exact event IDs and a reason with uncertainty. Prefer no proposal over an unsupported generalization. The runtime calculates matched-session and total-session counts from the evidence; it does not trust model-supplied prevalence statistics.

```bash
python3 "$UW" --project "$PROJECT" submit --packet review-packet.json --response review-response.json
python3 "$UW" --project "$PROJECT" proposals
```

Only the recorded project packet is accepted; altered packets, out-of-project evidence IDs, duplicate targets, and overwritten baselines are rejected. Submitting proposals advances the review watermark; promotion is separate. An empty proposal list is a valid reviewed batch. A future change needs new events/evidence. Expired memories are visible to review but excluded from ordinary retrieval.

## Use an external worker

```bash
python3 "$UW" --project "$PROJECT" dream-run --engine claude
python3 "$UW" --project "$PROJECT" dream-run --engine codex
```

Run either command, not both for the same scheduled task. The worker uses the locally installed and authenticated CLI. Claude runs in print mode with tools disabled and one turn. Codex runs in a read-only sandbox in a temporary directory. Neither adapter supplies credentials, bypasses approvals, changes model choice, or grants new permissions. Host-installed connections/config may still apply; use an isolated worker account for stricter containment. Verify installed CLI support with its help before deployment.

Each invocation launches at most one host CLI process with a timeout of 180 seconds by default (maximum 300). A transaction-backed lease prevents concurrent workers on the same store. Workers only create proposals and never promote them automatically. Model usage may incur the host's normal costs; the wrapper limits CLI invocations/time, not the host’s internal model turns or provider billing. Failures leave the foreground project unchanged and can be retried on a later invocation. Timeout cleanup kills the process group on POSIX; Windows only guarantees termination of the immediate worker process.

Configuration lives at `.universal-workflow/config.json`: review_engine, review_min_events, review_max_events, review_timeout_seconds, evaluator, and minimum_gain. Changing config does not install a scheduler.

Project-owned priorities use the separate `review-policy` command. Read [review-priorities.md](review-priorities.md) for include/focus/exclude guidance, bounded proposal/context limits, and revision-bound updates. Every packet carries its policy snapshot; changing priorities invalidates its submission, evaluation, and acceptance while leaving rejection available. Topic guidance does not remove evidence from packets or change goals and permissions.

## Evaluate and promote

Facts and lessons need evidence and review. Prompt/workflow changes additionally need an independently executed evaluator:

```bash
python3 "$UW" --project "$PROJECT" evaluate --id PROPOSAL_ID --cases /absolute/heldout-cases.json
python3 "$UW" --project "$PROJECT" decide --id PROPOSAL_ID --accept
python3 "$UW" --project "$PROJECT" decide --id OTHER_PROPOSAL_ID --reject
```

The configured `evaluator` is a trusted argv array, such as `["python3", "/absolute/project/eval_workflow.py"]`. It is executed without a shell in the project directory, receiving JSON on stdin:

```json
{"baseline":"old prompt text","candidate":"new prompt text","cases":[{"id":"case-1","input":"...","expected":"..."}]}
```

Return one row per input case on stdout:

```json
{"cases":[{"id":"case-1","baseline_pass":false,"candidate_pass":true,"fixed_constraints_pass":true}]}
```

Supply at least three unique held-out cases. Evaluate both prompts against the same task runner and objective criteria; protect expected answers and grader code. An empty baseline means a new instruction: the evaluator must compare against the real existing workflow without that added instruction, not an artificially broken baseline. The runtime executes this evaluator, validates its output, and stores hashes and computed scores. It requires a strict pass-rate improvement, no case regressions, and all fixed-constraint checks. A missing evaluator means workflow promotion is blocked; do not fake a receipt. The evaluator itself must be trustworthy and suitable for the domain. These checks do not establish universal improvement.

Acceptance checks the memory hash again inside the write transaction. Reject or regenerate a stale proposal; do not overwrite concurrent improvements. Keep owner policy and organizational rules out of auto-promotion. If an external service later automates approvals, grant it only project-level scope and require the same evaluations.

## Schedule out-of-band work

Use a persistent local scheduler, CI runner, or the host's verified scheduling capability. For example, a POSIX cron job can invoke one `dream-run` command daily; Windows Task Scheduler can invoke Python with the same arguments. Configure the actual project path, CLI credentials, PATH, timezone, logs, retention, and single-project account isolation. No schedule is installed by this package. When a user requests activation, install it through the available scheduler and verify its registration; if this host cannot access that machine, provide the concrete command and state the missing connection.

For better cost control, leave model review outside Stop hooks. The event-backed `prepare` and `dream-run` commands require new events, even with `--force`. Use [memory-only maintenance](maintenance.md) for resumable sweeps of older/expired memories and versioned merge/retire proposals without events. Maintenance has its own cursor and cannot consume event-review watermarks. [Project review priorities](review-priorities.md) supply versioned guidance and bounded context limits. Configure a persistent external scheduler if unattended execution is required. Use [parallel review](parallel-review.md) for bounded session shards and explicit reconciliation.
