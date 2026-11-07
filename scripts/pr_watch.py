#!/usr/bin/env python3
"""Read-only GitHub PR readiness watcher. Requires Python 3.10+ and authenticated gh."""
import argparse
from datetime import datetime, timezone
import json
import re
import subprocess
import sys
import time

QUERY = """query($owner:String!, $name:String!, $number:Int!) {
  repository(owner:$owner, name:$name) { pullRequest(number:$number) {
    number url state isDraft headRefOid baseRefOid mergeable mergeStateStatus reviewDecision
    comments(last:100) { pageInfo { hasPreviousPage } nodes {
      databaseId updatedAt body author { login __typename }
    } }
    reviewThreads(first:100) { pageInfo { hasNextPage } nodes { isResolved } }
    latestReviews(first:100) { pageInfo { hasNextPage } nodes { state } }
    commits(last:1) { nodes { commit { oid statusCheckRollup {
      contexts(first:100) { pageInfo { hasNextPage } nodes {
        __typename
        ... on CheckRun { name status conclusion detailsUrl }
        ... on StatusContext { context state targetUrl }
      } }
    } } } }
  } }
}"""
BOT_LOGINS = {"chatgpt-codex-connector", "chatgpt-codex-connector[bot]"}
MARKER = re.compile(r"<!-- codex-review-state:v1\s+(.*?)\s*-->", re.S)


class WatchError(Exception):
    pass


def gh_json(*args):
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True,
                                encoding="utf-8", timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WatchError("GitHub CLI unavailable or timed out; install gh and run gh auth login.") from exc
    if result.returncode:
        # Do not echo API bodies, CLI diagnostics or authentication material.
        raise WatchError("GitHub CLI request failed; check gh auth status, repository access and rate limits.")
    try:
        data = json.loads(result.stdout)
    except ValueError as exc:
        raise WatchError("GitHub CLI returned invalid JSON.") from exc
    if isinstance(data, dict) and data.get("errors"):
        raise WatchError("GitHub GraphQL returned errors; readiness is unknown.")
    return data


def fetch_pr(repo, number):
    owner, name = repo.split("/")
    data = gh_json("api", "--hostname", "github.com", "graphql", "-f", "query=" + QUERY,
                   "-f", "owner=" + owner, "-f", "name=" + name, "-F", f"number={number}")
    try:
        pr = data["data"]["repository"]["pullRequest"]
        if not pr:
            raise KeyError("pullRequest")
        return pr
    except (KeyError, TypeError) as exc:
        raise WatchError("Pull request unavailable.") from exc


def open_prs(repo):
    pages = gh_json("api", "--hostname", "github.com", "--paginate", "--slurp",
                    f"repos/{repo}/pulls?state=open&per_page=100")
    return [pr["number"] for page in pages for pr in page]


def evaluate(pr, require_checks=(), require_security=False):
    """Fail closed on missing, truncated or stale evidence; never perform a merge."""
    reasons = []
    failures = []
    head = pr["headRefOid"]
    report = {"pr": pr["number"], "url": pr["url"], "head": head,
              "base": pr["baseRefOid"], "status": "pending", "reasons": reasons}
    if pr["state"] != "OPEN":
        report.update(status=pr["state"].lower(), reasons=["PR is no longer open"])
        return report
    if pr["isDraft"]:
        reasons.append("PR is a draft")
    if pr["mergeable"] == "CONFLICTING":
        failures.append("merge conflicts")
    elif pr["mergeable"] != "MERGEABLE":
        reasons.append("mergeability is unknown")
    if pr["mergeStateStatus"] not in {"CLEAN", "HAS_HOOKS"}:
        reasons.append("GitHub merge state: " + str(pr["mergeStateStatus"]))
    if pr["reviewDecision"] == "CHANGES_REQUESTED":
        failures.append("changes requested")
    elif pr["reviewDecision"] not in {None, "APPROVED"}:
        reasons.append("required approval pending")
    for key in ("reviewThreads", "latestReviews"):
        if pr[key]["pageInfo"]["hasNextPage"]:
            reasons.append(key + " exceeds 100 entries; manual review required")
    if any(not t["isResolved"] for t in pr["reviewThreads"]["nodes"]):
        failures.append("unresolved review threads")
    if any(r["state"] == "CHANGES_REQUESTED" for r in pr["latestReviews"]["nodes"]):
        failures.append("review requests changes")

    commits = pr["commits"]["nodes"]
    rollup = None
    if commits and commits[-1]["commit"]["oid"] == head:
        rollup = commits[-1]["commit"].get("statusCheckRollup")
    checks = rollup["contexts"]["nodes"] if rollup else []
    if rollup and rollup["contexts"]["pageInfo"]["hasNextPage"]:
        reasons.append("more than 100 check contexts; manual verification required")
    successful = 0
    names = set()
    for check in checks:
        if check["__typename"] == "CheckRun":
            name = check["name"]
            state = check.get("conclusion") if check["status"] == "COMPLETED" else "PENDING"
        elif check["__typename"] == "StatusContext":
            name, state = check["context"], check["state"]
        else:
            reasons.append("unknown check type")
            continue
        names.add(name)
        if state == "SUCCESS":
            successful += 1
        elif state in {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED", "STALE"}:
            failures.append("check failed: " + name)
        elif state not in {"SKIPPED", "NEUTRAL"}:
            reasons.append("check pending: " + name)
    if not successful:
        reasons.append("no successful check on the current head")
    for name in require_checks:
        if name not in names:
            reasons.append("expected check missing: " + name)
        elif not any((c.get("name", c.get("context")) == name and
                      (c.get("conclusion") == "SUCCESS" and c.get("status") == "COMPLETED"
                       or c.get("state") == "SUCCESS")) for c in checks):
            reasons.append("expected check has not passed: " + name)

    if pr["comments"]["pageInfo"]["hasPreviousPage"]:
        reasons.append("more than 100 comments; manual verification required")

    # Only trust the known GitHub bot, never copied markers in user comments.
    comments = sorted(pr["comments"]["nodes"],
                      key=lambda c: (c["updatedAt"], c["databaseId"] or 0), reverse=True)
    summary = None
    for comment in comments:
        author = comment.get("author") or {}
        if author.get("__typename") != "Bot" or author.get("login") not in BOT_LOGINS:
            continue
        match = MARKER.search(comment["body"])
        if match:
            try:
                summary = json.loads(match.group(1))
            except ValueError:
                pass
            # The newest summary is authoritative, even if malformed or stale.
            break
    for kind in (["code_review", "security_review"] if require_security else ["code_review"]):
        review = summary.get(kind) if isinstance(summary, dict) else None
        if not isinstance(review, dict) or review.get("commit_sha") != head:
            reasons.append(kind + ": missing or stale Codex result")
        elif review.get("has_unresolved_findings") is True or review.get("has_any_findings") is True:
            failures.append(kind + ": findings require review")
        elif (review.get("status") != "completed" or
              review.get("has_any_findings") is not False or
              review.get("has_unresolved_findings") is not False):
            reasons.append(kind + ": clean completion not confirmed")
    report["reasons"] = failures + reasons
    report["status"] = "blocked" if failures else "pending" if reasons else "ready"
    return report


def snapshot(repo, number, require_checks=(), require_security=False):
    pr = fetch_pr(repo, number)
    report = evaluate(pr, require_checks, require_security)
    if report["status"] == "ready":
        # Confirm readiness twice so a push/base update during collection cannot pass.
        confirmed = fetch_pr(repo, number)
        report = evaluate(confirmed, require_checks, require_security)
        if (pr["headRefOid"], pr["baseRefOid"]) != (confirmed["headRefOid"], confirmed["baseRefOid"]):
            report.update(status="pending", reasons=["head or base changed during inspection"])
    return report


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="GitHub.com OWNER/REPO")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--pr", type=positive)
    target.add_argument("--all", action="store_true", help="rediscover all open PRs each interval")
    parser.add_argument("--once", action="store_true", help="one sweep, then exit")
    parser.add_argument("--interval", type=positive, default=60, help="seconds between sweeps (minimum 15)")
    parser.add_argument("--timeout", type=positive, default=3600, help="watch duration in seconds")
    parser.add_argument("--json", action="store_true", help="emit JSON Lines")
    parser.add_argument("--require-check", action="append", default=[], help="exact check name; repeatable")
    parser.add_argument("--require-security", action="store_true", help="also require explicit clean Codex security evidence")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("--repo must be OWNER/REPO")
    if args.interval < 15:
        parser.error("--interval must be at least 15 seconds")
    deadline = time.monotonic() + args.timeout
    seen = {}
    first = True
    try:
        while first or time.monotonic() < deadline:
            first = False
            numbers = open_prs(args.repo) if args.all else [args.pr]
            reports = []
            for number in numbers:
                if time.monotonic() >= deadline:
                    break
                try:
                    report = snapshot(args.repo, number, args.require_check, args.require_security)
                except (WatchError, KeyError, TypeError, ValueError) as exc:
                    report = {"pr": number, "status": "error", "reasons": [str(exc) if isinstance(exc, WatchError)
                              else "Unexpected API response; readiness is unknown"]}
                reports.append(report)
                serialized = json.dumps(report, sort_keys=True)
                if seen.get(number) != serialized:
                    seen[number] = serialized
                    report["observed_at"] = datetime.now(timezone.utc).isoformat()
                    if args.json:
                        print(json.dumps(report), flush=True)
                    else:
                        print(f"#{number}: {report['status'].upper()} — " +
                              ("; ".join(report["reasons"]) or "current CI and Codex review are clear"), flush=True)
            # Remove departed PRs so reopening them emits a fresh observation.
            seen = {n: v for n, v in seen.items() if n in numbers}
            statuses = {r["status"] for r in reports}
            if args.once or (not args.all and statuses & {"ready", "blocked", "merged", "closed"}):
                if len(reports) != len(numbers):
                    return 3
                if "error" in statuses:
                    return 2
                if statuses & {"blocked", "closed"}:
                    return 1
                return 3 if "pending" in statuses else 0
            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(min(args.interval, remaining))
        print("Watch timeout reached.", file=sys.stderr)
        return 3
    except WatchError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
