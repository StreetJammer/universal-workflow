# Validation record — 0.1.0 preview

The foundation was merged through [PR #1](https://github.com/StreetJammer/universal-workflow/pull/1). It is not a claim of universal compatibility or production certification.

## Reproduce local checks

From the repository root:

```bash
python3 scripts/validate_package.py
python3 -m unittest discover -s skills/universal-workflow/scripts -p 'test_*.py' -v
```

The suite contains **169 deterministic tests**: the 60-test foundation, ten session-trace regressions, twenty-one maintenance regressions, seventeen event-review policy tests, eleven maintenance-policy integration tests, nineteen parallel-review tests, and twenty-one live HTTP service tests, six history-pagination tests, and four policy-capacity tests. The authoring environment is Linux with Python 3.12.14. Tests use temporary projects and do not contact model providers or alter live projects. Linux/macOS skip one Windows-only junction test; Windows skips the POSIX-specific cases explicitly; service ACL deployment still requires native operator verification.

| Area | Evidence and scope |
| --- | --- |
| Memory lifecycle | Create, read, update, expiry, history, rollback, project/scratch scope, secret-pattern rejection, private backup permissions before SQLite opens the file, concurrent backup-destination preservation |
| Concurrent integrity | Competing writes, revision-bound hashes after rollback, 12 simultaneous initializers, paused config publication, injected transaction failure, bounded retry of Windows delete-pending locks |
| Review integrity | Packet tampering, foreign evidence, partial invalid batches, overlapping reviews, stale proposals, derived session counts |
| Evaluation | A real subprocess evaluates controlled cases; passing receipt required for workflows; regressions and fixed-constraint failures rejected; late evaluations cannot rewrite accepted receipts |
| Worker failure | Timeout, nonzero exit, lease exclusion, interruption cleanup on POSIX, fake Claude/Codex executable transport |
| Bounded data | 50 large Unicode events are processed across bounded packets without skipped or duplicated sequences |
| Hooks | Context output shape, no raw response capture, turn-ID deduplication, old-host fallback, nested repository/worktree and explicit non-Git project isolation, malformed location rejection, bounded lock contention |
| Installation | Both-host conflict preflight, forged module rejection, full entry-set/type validation, internal symlink/junction rejection, FIFO rejection without opening it, custom content preservation, CRLF/mode preservation, AGENTS import, repeat idempotence, plugin/generic license inclusion |
| Authenticated sharing | Real loopback HTTP, cross-organization/team/user/scratch isolation, inherited context, policy/credential revocation, workflow rollback/expiry denial, policy-size preservation, bounded recall/quotas and Unicode packet round trips |
| Parallel review | Concurrent subprocess shards, evidence isolation, deterministic conflicts/denominators, bounded retries, timeout/interruption cleanup, policy propagation and policy changes during workers |
| Review policy | Bounded guidance, unchanged evidence, policy compare-and-swap, snapshot integrity and invalidation before/during evaluation and acceptance |
| Memory maintenance | No-event sweeps, cross-batch candidates, resumable cursors, multi-source atomic merges, retirement/restore, stale hashes and workflow evaluation gates |
| Selected session traces | Rich review-context retention, redaction before DB/WAL persistence, bounded input, project isolation, stable identities, late-conflict batch rollback, subprocess import |
| Packaging | Required files, valid JSON, Python 3.10 syntax, local Markdown links, plugin hooks, absence of raw transcript and project database |

The evaluator tests deliberately use controlled pass/fail fixtures to test the gate. They are not evidence that generated prompts improve real tasks. A project owner must supply an independently meaningful evaluator.

## Additional task exercises

Independent agent task trials used the workflow on disposable artifacts:

- A small web/cart task preserved existing CommonJS/no-dependency rules and passed seven Node tests for integer currency handling and quantity validation.
- A selected-session task captured a failed and corrected build command, imported two rich turns, verified credential redaction before persistence/export, and confirmed duplicate import did not repeat events. The evidence reached a pending proposal without automatic promotion.
- An operations task installed both host copies, preserved instructions, captured sourced observations, prepared and submitted a pending lesson, and verified export/backup. It correctly distinguished supplied log claims from tests actually run.

Separate offline runtime and packaging audits reproduced issues, then checked repairs. Their reusable regression cases are included in the suite. Raw fixture output and private source material are excluded from distribution.

## Checks still requiring another environment

| Check | Status |
| --- | --- |
| GitHub Actions on Linux/macOS/Windows, Python 3.10/3.12 | All six jobs passed on merged PR #1; feature revisions require their own latest checks |
| Native Claude plugin discovery, commands, and hook events | Format inspected against official documentation; native CLI unavailable here |
| Native Codex discovery and authenticated `exec` | Documented interface implemented; native CLI unavailable here |
| Model-backed review quality and real evaluator improvements | Requires authenticated host and task-specific held-out cases |
| Windows descendant-process cleanup | Only immediate child termination is guaranteed; POSIX process-group checks are skipped on Windows |
| Windows privileged symlink creation | Symlink regressions are POSIX-only; no false pass reported |
| Game engine, browser, render, or deployment integrations | Delegated to destination tools; not exercised by these runtime tests |
| Unattended execution | Scheduler recipe supplied; no schedule activated by the package |
| `@codex review` on the public PR | Code review found backup permissions; security review found forged installation destinations. Both reproduced and repaired with regressions; code/security reviews completed before PR #1 merged. Follow each feature PR for its latest review state. |

## Release decision

The first hosted run exposed a Windows delete-pending directory race during initialization and an unclosed SQLite connection in the backup test. Both are repaired in this PR. Three added regressions exercise transient retry, bounded persistent denial, and propagation of unrelated permission errors; the hosted Windows jobs provide native confirmation.

Codex also identified backups created as `0644` under a typical POSIX umask, although the source was `0600`. Backups now reserve the destination atomically with `0600` permissions before SQLite opens it. Tests check the initial permissions and preservation of a concurrently created destination.

The security review reproduced an accepted destination containing an extra `scripts/json.py` that executes during the runtime's import. A harmless marker confirmed the original behavior. Existing destinations now undergo complete entry-set and type validation without following internal links. Seven new regression tests cover injected modules, bytecode/caches, hidden files, extra directories, linked files/directories, Windows junctions, missing or swapped entries, special files, plugin exports, and rejection before either host is installed.

Publish as **0.1.0 preview** with this evidence and the [gap analysis](GAP_ANALYSIS.md). Require hosted CI and review findings to be resolved before merging the implementation PR. Keep live host checks visible; do not upgrade compatibility claims merely because mocks pass.

Independent service review reproduced administrative policy-size lockout, workflow rollback bypass, and unbounded recall materialization. The service now preflights policy serialization, denies workflow rollback/expiration to HTTP writers, and uses bounded service-only retrieval and quotas. Additional Unicode transport tests preserve both review watermarks when splitting large packets.

Pre-merge review also reproduced credential revocation failing at the exact policy-size cap and oversized histories becoming unreadable. Admission now reserves security-operation capacity, legacy full-size policies compact safely for rotation/revocation, and history supports byte-bounded cursor pagination. Regressions include all 64 revocations at the old cap, revision growth, large Unicode histories and concurrent updates between pages.
