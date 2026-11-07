# Pull request watcher

`scripts/pr_watch.py` watches any GitHub.com repository you can read. It reports
current CI and Codex code review evidence for one PR, or continuously discovers
all open PRs including ones created after it starts. It never posts comments,
requests reviews, changes draft status, or merges. It runs only while its process
is alive; adding this file does not install a background service.

## Setup and commands

Use Python 3.10+ and the [GitHub CLI](https://cli.github.com/), authenticated with
`gh auth login`. The account/token needs repository, pull request, check and commit
status read access. Authentication stays in `gh`; the watcher does not save tokens.
Use `python3` instead of `py -3` on Linux/macOS/WSL.

From your Windows checkout:

```powershell
cd F:\Projects\Tools\dream
gh auth login
# All open PRs, including newly created ones, for eight hours:
py -3 scripts/pr_watch.py --repo StreetJammer/universal-workflow --all --timeout 28800
# One PR: exit when ready, blocked, merged, closed, or timed out:
py -3 scripts/pr_watch.py --repo StreetJammer/universal-workflow --pr 12
# One snapshot for an agent or CI integration:
py -3 scripts/pr_watch.py --repo StreetJammer/universal-workflow --all --once --json
```

For another project, change `--repo OWNER/REPO`. An agent can run the one-PR command
after opening/updating a PR and consume the exit status. The all-PR mode keeps
running even when every currently open PR is ready, so it can discover future PRs.
It prints changes only; it does not send desktop notifications. Stop with Ctrl+C.

The default interval is 60 seconds (minimum 15), and default duration is one hour.
API calls have a 60-second timeout each; a sweep may overrun the watch deadline by
its in-flight requests. All-PR sweeps are sequential and can be slow for large repos.
If you want ongoing unattended operation, run this command under your own persistent
scheduler and use its output/log handling. No scheduler is enabled by this change.

## What READY means

- PR is open, not a draft, and GitHub reports mergeable with a clean/has-hooks merge state.
- Required approval is satisfied if GitHub reports one; no changes-requested review
  or unresolved review thread is visible.
- Current-head check rollup has at least one success, no failures or pending checks.
  Neutral/skipped optional checks are permitted, but a skipped-only suite is not success.
- The trusted Codex bot's latest structured summary explicitly reports completed code
  review, zero findings, and zero unresolved findings for the exact current head SHA.
- A second snapshot confirms the head and base did not change and readiness still holds.

Use repeated `--require-check "exact check name"` arguments to require known jobs,
including jobs that have not appeared yet. An expected check must actually succeed;
skipped/neutral is insufficient. Without these arguments the watcher evaluates the
visible rollup, and cannot infer jobs that were never configured or triggered.

`--require-security` additionally requires explicit clean security evidence. A
security summary with null finding fields stays pending, even if its status says
completed. Without this option, READY covers code review and CI, not security clearance.
Codex integration must already be configured/requested in the repository. The script
does not initiate reviews or treat a generic approval/emoji as a Codex result.

READY is a point-in-time report, not approval or a substitute for GitHub branch rules.
Changes after the final snapshot can invalidate it. Any merger must recheck and use an
expected head SHA. This script has no merge option.

## Output and failure behavior

`--json` emits one JSON object per changed observation, with PR number, head/base SHA,
status, reasons, URL and UTC timestamp when available. Error observations omit unknown
fields. An empty `--all --once` result emits nothing and succeeds. In all-PR mode,
merged/closed PRs drop out of the open list; their closure is not separately announced.

| Exit | Meaning |
| --- | --- |
| 0 | Single PR ready/merged, or one sweep with all selected PRs ready/merged (including no open PRs) |
| 1 | Blocked or single PR closed without merge |
| 2 | API/authentication/transport/schema error in a one-shot check, or PR-list failure |
| 3 | Pending snapshot or watch timeout, including normal end of an all-PR watch |
| 130 | Interrupted by user |

All-PR discovery follows REST pagination. Per-PR GraphQL collections are bounded to
100 entries; truncated checks/reviews/threads/comments report pending for manual
verification instead of claiming readiness. This keeps each poll bounded. API errors
never count as success. Individual PR errors are retried during watching and reported
as errors; repository listing failures terminate. Restart after fixing authentication.

The Codex summary marker is an observed integration format, not a public stability
contract. A missing, malformed, changed or stale marker stays pending. Only the known
Codex bot author is trusted; copied markers from users/other bots are ignored.

## Verification

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Regression tests cover stale commits, moving heads/bases, spoofed/malformed summaries,
failed/pending checks, required jobs/approvals, unresolved threads, collection truncation,
API errors, pagination, new PR discovery, deduplication and exit codes. Fixtures do not
prove live GitHub authentication or model review. CI also runs a read-only API smoke
check on the current PR in the Ubuntu/Python 3.12 job; pending/blocked is expected while
that job runs. That smoke check validates transport/schema, not successful readiness.
