# Run reviews outside foreground work

The package includes a one-shot worker, not a daemon. Choose a persistent machine with Python and an authenticated Claude Code or Codex CLI. Run the worker manually once before adding a scheduler.

```bash
python3 /absolute/plugin/skills/universal-workflow/scripts/uw.py \
  --project /absolute/project dream-run --engine claude
```

## POSIX cron example

This is a recipe, not an installed job. Replace every path and verify the scheduler's timezone. The Python/CLI executables must be available in the scheduler's PATH.

```cron
PATH=/usr/local/bin:/usr/bin:/bin
0 3 * * * /usr/bin/python3 /absolute/plugin/skills/universal-workflow/scripts/uw.py --project /absolute/project dream-run --engine claude >> /absolute/private-logs/workflow-review.log 2>&1
```

Choose your intended timezone in the scheduler's supported configuration; do not assume the host uses your local time. Use Windows Task Scheduler with Python as the executable and the same arguments on Windows. Keep logs private and rotate them.

## Operational expectations

- With fewer than the configured new-event threshold, the worker does no model work.
- Each run launches at most one host CLI process, with a finite timeout and per-project lease. The host may make multiple internal model calls; the wrapper does not cap provider billing.
- A failed review can be retried later; no memory changes are adopted merely because the worker ran.
- Configure independent evaluation before adopting workflow changes.
- Normal CLI usage limits and charges apply. A wall-clock timeout is not a monetary cap.
- An unavailable executable, missing login, or unsupported CLI flag is a deployment blocker, not a successful review.
- Keep one worker per project identity. For multi-user deployment, enforce account/storage isolation outside this package.

For input schemas, proposal decisions, and evaluation, see [the review reference](../skills/universal-workflow/references/review.md).
