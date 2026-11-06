# Authenticated organization, team, and individual memory

`uw_service.py` is an optional loopback HTTP service with authenticated principals, owner-controlled membership, and physically separate namespace stores. It supplies the authorization boundary that plain `uw.py --actor ...` audit labels do not provide. It does not start at installation and does not change the original single-user CLI.

## Trust boundary

Run the service as a dedicated OS account. Its directory must be outside repositories and other client-writable project roots. Clients must not have direct filesystem access to the service state, policy, or installed Python modules. POSIX ownership and private mode bits are checked; on Windows, the administrator must configure an equivalent restrictive ACL. The service rejects linked/reparse state directories but cannot isolate a process that already controls its OS account.

The service listens only on `127.0.0.1`, uses random bearer credentials, and has no TLS or remote-host option. It is not an Internet-facing service, enterprise identity provider, SSO integration, or filesystem sandbox. Keep bearer credentials out of project files, shell history, and request logs. A credential grants its configured scope until revoked; this preview does not implement token expiration.

Policy lives at the service root, outside all namespace stores. No HTTP route edits policy, sets roles, chooses paths, changes evaluators, runs a model or shell command, exports databases, or restores backups. Local administrative commands are privileged operations requiring filesystem access to the service account. Request-supplied actor or role claims are rejected.

## Initialize and provision

Use an existing private parent directory and a new child path:

```bash
SERVICE=/absolute/trusted/universal-workflow/skills/universal-workflow/scripts/uw_service.py
python3 "$SERVICE" init --directory /absolute/private/memory-service
python3 "$SERVICE" provision --directory /absolute/private/memory-service \
  --user alice --org studio --team design --team-writer design
python3 "$SERVICE" provision --directory /absolute/private/memory-service \
  --user bob --org studio --team design
```

Provisioning prints a newly generated token once. Only its SHA-256 digest is stored. There are no default accounts or credentials. User, organization, and team identifiers use lowercase letters, digits, underscores, and hyphens, starting with a letter or digit. The policy supports up to 64 principals and 16 teams per principal, within a 128 KB serialized policy limit. An administrative change that would exceed that limit is rejected before replacing the existing policy; authentication and administration continue using the previous readable version.

Alice can write the design namespace; Bob can read it. Both can read their organization namespace. Neither can write organization memory without `--org-writer`. Each can read and write only their own individual and scratch namespace.

Start explicitly:

```bash
python3 "$SERVICE" serve --directory /absolute/private/memory-service --port 8765
```

The server processes one request at a time, queues at most eight connections, uses five-second absolute connection deadlines as well as socket inactivity timeouts, limits request bodies to 1 MB and responses to 2 MB, and applies per-principal request limits (120/minute by default, configurable from 1 to 600). Invalid credentials have a separate bounded rate bucket. It does not log request bodies, tokens, or project paths. OS and local network controls remain necessary against denial of service.

Each namespace is limited to 1,000 current memories and 128 KB per serialized memory. New memories, including accepted proposals, enforce these limits inside their transaction. Recall retains only the requested best matches and scans at most 1,000 memories across the selected namespace and inherited scopes combined. Preparation uses the same bounded reader. A store enlarged beyond these limits by local tools, or a search exceeding the combined scan budget, returns HTTP 413 instead of a partial result. An administrator must maintain oversized stores with the trusted local tools. These limits bound service reads and writes; they are not a total disk quota for event and revision history.

## HTTP contract

Send JSON with `Content-Type: application/json` and `Authorization: Bearer TOKEN` to `POST /v1/SCOPE/ACTION`. Responses are `{"result": ...}` on success or a generic `{"error": ...}`; errors do not echo private payloads or internal filesystem paths. `GET` is not a data-access route.

| Scope | Target selection | Read | Write | Inherited review/recall context |
| --- | --- | --- | --- | --- |
| `organization` | Optional `namespace` must equal authenticated organization | Members | Explicit organization writers | None |
| `team` | Required `namespace` must be an authenticated team membership | Team members | Explicit writers of that team | Organization |
| `individual` | Derived from authenticated organization and user; no client namespace | That principal only | That principal only | Organization and that principal's teams |
| `scratch` | Derived from authenticated organization and user; no client namespace | That principal only | That principal only | Organization, that principal's teams, own individual memory |

Groups with the same name in different organizations have separate stores. Membership never grants access to another user's individual or scratch namespace. Inheritance flows from higher scopes into a selected lower scope. A review at organization or team scope does not receive lower/private data. Inherited entries are read-only context, never mutable targets or event evidence of the selected review. Explicit writes still require the target namespace's writer grant.

All actions validate a strict request shape. Add `namespace` only where allowed by the scope table.

| Action | JSON fields |
| --- | --- |
| `status` | None; returns bounded counts, version, and project ID without filesystem paths |
| `recall` | Optional `query`, `limit` (1–20), `include_inherited` (default true) |
| `current`, `history` | `key`; history returns at most 200 latest revisions |
| `remember` | `key`, `body`, `evidence`; optional `expected_hash`, `kind`, `ttl_days` |
| `record` | `event` containing the normal bounded outcome fields; session/event IDs are bound to the authenticated principal |
| `expire` | `key`, `expected_hash`; workflow memories require local owner administration |
| `rollback` | `key`, `expected_hash`, positive `revision`; neither current nor target revision may be a workflow |
| `review-prepare` | Optional boolean `force` |
| `review-get` | `review_id` |
| `review-submit` | Exact `packet` returned by preparation and model/host `response` |
| `proposals` | None; up to 100 visible pending proposals from a bounded scan |
| `proposal-get` | `id` |
| `proposal-decide` | `id`, boolean `accept` |

For example, an authenticated team writer can send this JSON to `/v1/team/remember`:

```json
{
  "namespace": "design",
  "key": "build-command",
  "body": "Use the project-owned verified build task.",
  "evidence": ["build-log:verified"],
  "kind": "fact"
}
```

The same writer can prepare a review at `/v1/team/review-prepare` with `{"namespace":"design","force":true}`. The returned packet contains only that team's outcomes/memories plus bounded read-only organization context. This service never sends the packet to a model automatically.

Prepared reviews and derived proposals are bound to the authenticated principal, namespace, and policy revision. Even another writer of a shared namespace cannot retrieve or decide the first principal's review packet. Submit and decision operations validate this binding again. Normal review packet integrity, stale-memory hash checks, transactional decisions, and workflow evaluator gates still apply. The service has no evaluator endpoint; an administrator must run a trusted evaluation locally before accepting a workflow proposal. Ordinary lesson proposals can be accepted through the authenticated decision route.

HTTP clients cannot expire workflows or roll back from or into a workflow revision. These operations remain available to the trusted local owner; service writers must use an evaluated proposal to replace workflow content. Prepared packets are trimmed to a 600 KB submission envelope measured with ASCII-escaped JSON, leaving room for the reviewer response. If events span multiple packets, the stored watermark advances only through the included events, so remaining events are eligible for the next review. Preparation failures do not leave orphaned prepared packets.

## Change or revoke access

All commands reread the current policy under an exclusive administrative lock. HTTP operations use the same lock for authorization and mutation, preventing an administrative revocation from landing between the grant check and a submission/decision write. HTTP authentication reloads policy for each request; no restart is needed.

```bash
# Replace memberships and write grants; this removes the prior design membership.
python3 "$SERVICE" membership --directory /absolute/private/memory-service \
  --user alice --org studio --team research

# Immediately invalidate the old token and return a new one once.
python3 "$SERVICE" rotate --directory /absolute/private/memory-service --user alice

# Disable the current credential immediately.
python3 "$SERVICE" revoke --directory /absolute/private/memory-service --user alice
```

`--expected-revision NUMBER` makes any administrative change conditional on the policy revision. Every administrative update invalidates previously prepared service reviews and pending proposals, including ones prepared before a membership was revoked. The raw memory/history remains preserved. Prepare new reviews using current grants; the service does not silently rebind old review evidence to a new policy. Revoked users must be explicitly rotated to receive an enabled credential again.

Generated policy, credentials, namespace stores, and review data are private deployment state. Do not commit them or include them in plugin exports.

## Verification and current limits

`test_service.py` uses a real loopback HTTP server and multiple independent bearer credentials. It tests unauthenticated/forged claims, cross-organization/team/user/scratch isolation, inherited read versus write access, review-input filtering, foreign packet/proposal access, membership revocation, token rotation/revocation without restart, owner-policy revision and size guards, bounded routes and scans, quota enforcement on accepted proposals, Unicode review batching, workflow rollback/expiration rejection, body/rate limits, and private state permissions. Workflow gate tests use a local fixture evaluator; tests call no model providers.

This release deliberately omits HTTP trace import, maintenance/parallel-worker execution, evaluator execution, policy editing, and raw export/backup routes. Those operations remain owner-controlled local tools until a scoped service adapter is added and tested. The service is an enforceable local authenticated interface; it does not establish enterprise deployment security or protect against its own OS account.
