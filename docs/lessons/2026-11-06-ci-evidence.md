# Verified project lesson: inspect job-level CI evidence

- **Lesson ID:** ci-evidence-freshness-2026-11-06
- **Kind / scope:** Observed project lesson; `StreetJammer/universal-workflow`.
- **Verified source commit:** `49cd9369794941cf8da77e9029a32e25ac583f25`.
- **Verified on:** 2026-11-06, from GitHub Actions job metadata and decoded logs.
- **Recorded by:** ChatGPT, using the repository's Universal Workflow instructions and GitHub connector.
- **Revalidate:** Before applying these counts to a different commit, or by 2026-12-06, whichever comes first. The results below are historical evidence, not a permanent green status.

## Lesson

A green latest CI attempt is not evidence that every matrix job was freshly executed. In the inspected single-job rerun, GitHub returned six successful job records for attempt 2, but five retained execution timestamps from the earlier attempt. Only Ubuntu/Python 3.12 had new execution timestamps. Report fresh checks separately from retained results, and distinguish the unittest total from tests that actually passed.

For this project's verification reports, inspect each job's `started_at` and `completed_at`, then read its test summary. Do not infer freshness from a new job ID, the `run_attempt` field, or a green aggregate status alone. Do not call skipped tests passed tests.

## Evidence

The rerun was requested for original Ubuntu/Python 3.12 job `127816936363` in run `42195123613`. The latest attempt returned these records:

| Matrix job | Attempt-2 job ID | Execution timestamps, UTC on 2026-11-06 | Evidence classification |
| --- | --- | --- | --- |
| Ubuntu, Python 3.12 | `128044737600` | 17:30:10 to 17:30:40 | Fresh execution requested during this inspection |
| Ubuntu, Python 3.10 | `128044737459` | 09:28:22 to 09:28:53 | Retained earlier execution |
| macOS, Python 3.12 | `128044738243` | 09:28:25 to 09:29:25 | Retained earlier execution |
| Windows, Python 3.10 | `128044738930` | 09:28:22 to 09:31:09 | Retained earlier execution |
| Windows, Python 3.12 | `128044788124` | 09:28:21 to 09:30:57 | Retained earlier execution |

The sixth successful matrix record was macOS/Python 3.10, job `128044794917`. Its result was not treated as a fresh execution or used for a numerical test-count claim in this lesson.

Sources: [CI attempt 2](https://github.com/StreetJammer/universal-workflow/actions/runs/42195123613/attempts/2), [fresh Ubuntu job](https://github.com/StreetJammer/universal-workflow/actions/runs/42195123613/job/128044737600), and [retained Windows job](https://github.com/StreetJammer/universal-workflow/actions/runs/42195123613/job/128044788124). The original Windows log was also checked at [job 127816936646](https://github.com/StreetJammer/universal-workflow/actions/runs/42195123613/job/127816936646).

### Commands and observed outcomes

The fresh Ubuntu job used CPython 3.12.16 and executed both commands required by the project:

```bash
python scripts/validate_package.py
python -m unittest discover -s skills/universal-workflow/scripts -p "test_*.py" -v
```

Package validation succeeded. Its output confirmed package structure, JSON, Python 3.10 syntax, local links, and hook targets.

At 17:30:38 UTC, the unit-test summary reported `Ran 184 tests in 23.231s` and `OK (skipped=1)`: **183 passed, 1 skipped, 0 failures/errors**. The skipped case was `test_internal_windows_junction_is_rejected`, with reason `Windows junction behavior`.

The retained Windows/Python 3.12 log ended at 09:30:53 UTC with `Ran 184 tests in 139.409s` and `OK (skipped=7)`: **177 passed, 7 skipped, 0 failures/errors**. Among the skips were POSIX permission/cancellation cases and `test_model_adapters_with_mock_executables`. These are earlier results inspected in this session, not a newly executed Windows run.

The commands and matrix are defined in [the pinned CI workflow](https://github.com/StreetJammer/universal-workflow/blob/49cd9369794941cf8da77e9029a32e25ac583f25/.github/workflows/ci.yml). [The pinned project guidance](https://github.com/StreetJammer/universal-workflow/blob/49cd9369794941cf8da77e9029a32e25ac583f25/AGENTS.md) explicitly separates fixture tests from live native CLI/model checks.

## Storage and verification limits

This is one evidence-backed repository lesson saved through Universal Workflow's authorized durable-storage fallback. It is not a generated project-store export, a workflow/prompt modification, or an assertion of improved model performance.

The Windows checkout and its existing project memory store were not accessible from this chat. No local SQLite memory was initialized, updated, or recalled. No native host discovery, authentication, real model call, local-PC end-to-end trial, deployment, or scheduler activation was verified by this inspection. Runtime code and fixed project policy were not changed.
