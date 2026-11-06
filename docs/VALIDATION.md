# Validation record — 0.1.0 preview

This release is ready for a public review PR after repository access is available. It is not a claim of universal compatibility or production certification.

## Reproduce local checks

From the repository root:

```bash
python3 scripts/validate_package.py
python3 -m unittest discover -s skills/universal-workflow/scripts -p 'test_*.py' -v
```

The local release suite contains **48 deterministic tests**: 23 runtime/integration tests, 14 concurrency and failure regressions, and 11 packaging/hook regressions. The authoring environment is Linux with Python 3.12.14. Tests use temporary projects and do not contact model providers or alter live projects.

| Area | Evidence and scope |
| --- | --- |
| Memory lifecycle | Create, read, update, expiry, history, rollback, project/scratch scope, secret-pattern rejection |
| Concurrent integrity | Competing writes, revision-bound hashes after rollback, 12 simultaneous initializers, paused config publication, injected transaction failure |
| Review integrity | Packet tampering, foreign evidence, partial invalid batches, overlapping reviews, stale proposals, derived session counts |
| Evaluation | A real subprocess evaluates controlled cases; passing receipt required for workflows; regressions and fixed-constraint failures rejected; late evaluations cannot rewrite accepted receipts |
| Worker failure | Timeout, nonzero exit, lease exclusion, interruption cleanup on POSIX, fake Claude/Codex executable transport |
| Bounded data | 50 large Unicode events are processed across bounded packets without skipped or duplicated sequences |
| Hooks | Context output shape, no raw response capture, turn-ID deduplication, old-host fallback, nested repository/worktree and explicit non-Git project isolation, malformed location rejection, bounded lock contention |
| Installation | Both-host conflict preflight, custom content preservation, CRLF/mode preservation, AGENTS import, repeat idempotence, plugin/generic license inclusion |
| Packaging | Required files, valid JSON, Python 3.10 syntax, local Markdown links, plugin hooks, absence of raw transcript and project database |

The evaluator tests deliberately use controlled pass/fail fixtures to test the gate. They are not evidence that generated prompts improve real tasks. A project owner must supply an independently meaningful evaluator.

## Additional task exercises

Two separate agent task trials used the workflow on disposable artifacts:

- A small web/cart task preserved existing CommonJS/no-dependency rules and passed seven Node tests for integer currency handling and quantity validation.
- An operations task installed both host copies, preserved instructions, captured sourced observations, prepared and submitted a pending lesson, and verified export/backup. It correctly distinguished supplied log claims from tests actually run.

Separate offline runtime and packaging audits reproduced issues, then checked repairs. Their reusable regression cases are included in the suite. Raw fixture output and private source material are excluded from distribution.

## Checks still requiring another environment

| Check | Status |
| --- | --- |
| GitHub Actions on Linux/macOS/Windows, Python 3.10/3.12 | Matrix configured; hosted execution pending publication |
| Native Claude plugin discovery, commands, and hook events | Format inspected against official documentation; native CLI unavailable here |
| Native Codex discovery and authenticated `exec` | Documented interface implemented; native CLI unavailable here |
| Model-backed review quality and real evaluator improvements | Requires authenticated host and task-specific held-out cases |
| Windows descendant-process cleanup | Only immediate child termination is guaranteed; POSIX process-group checks are skipped on Windows |
| Windows privileged symlink creation | Symlink regressions are POSIX-only; no false pass reported |
| Game engine, browser, render, or deployment integrations | Delegated to destination tools; not exercised by these runtime tests |
| Unattended execution | Scheduler recipe supplied; no schedule activated by the package |
| `@codex review` on the public PR | Pending repository/PR creation and review integration access |

## Release decision

Publish as **0.1.0 preview** with this evidence and the [gap analysis](GAP_ANALYSIS.md). Require hosted CI and review findings to be resolved before merging the implementation PR. Keep live host checks visible; do not upgrade compatibility claims merely because mocks pass.
