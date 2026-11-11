#!/usr/bin/env python3
#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
"""Post a merge recommendation on a Dependabot PR once its checks finish (dependabot-triage.yml).

    python scripts/dependabot_recommendation.py --repo OWNER/NAME --pr 555 --head-sha <sha> [--dry-run]

Patch/minor bumps merge themselves (dependabot-auto-merge.yml); they get a comment only when a
check fails. A major bump, or any red PR, gets one comment, edited in place on every later run:
a verdict (MERGE, MERGE AFTER SMOKE, HOLD or WAIT), the packages, the checks and the security
advisories it fixes. A red PR is labelled ``agent-ready`` (Repo-Triage SOP) and the label is
removed once it is green. Standard library plus the ``gh`` CLI only.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field

MARKER = "<!-- dependabot-recommendation -->"
LABEL = "agent-ready"
LEVELS = ("patch", "minor", "major")
MAX_LISTED = 10  # advisories listed in the comment; the rest are counted
SEVERITY_ORDER = ("low", "medium", "high", "critical")
# A cancelled run is normally superseded by a newer push, so it is not a failure.
FAILED = {"failure", "timed_out", "action_required", "startup_failure"}

_GROUP_UPDATE = re.compile(r"Updates `([^`]+)` from (\S+?) to (\S+?)\.?$", re.MULTILINE)
_SINGLE_UPDATE = re.compile(r"Bumps \[([^\]]+)\]\([^)]*\) from (\S+?) to (\S+?)\.?$", re.MULTILINE)
_META_NAME = re.compile(r"^- dependency-name: (\S+)$", re.MULTILINE)


@dataclass
class Update:
    name: str
    old: str
    new: str
    dep_type: str = "unknown"  # direct:production, direct:development, indirect

    @property
    def level(self) -> str:
        return semver_level(self.old, self.new)


@dataclass
class Checks:
    failed: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)
    passed: int = 0


@dataclass
class Advisory:
    package: str
    ghsa: str
    severity: str
    summary: str


def _parts(version: str) -> list[int]:
    nums = []
    for piece in version.lstrip("v").split("."):
        match = re.match(r"\d+", piece)
        nums.append(int(match.group()) if match else 0)
    return (nums + [0, 0, 0])[:3]


def semver_level(old: str, new: str) -> str:
    """The semver level of a bump, the way Dependabot's metadata names it (0.x is not special)."""
    a, b = _parts(old), _parts(new)
    if a[0] != b[0]:
        return "major"
    if a[1] != b[1]:
        return "minor"
    return "patch"


def parse_updates(body: str, commit_message: str = "") -> list[Update]:
    """Packages and versions from a Dependabot PR body; dependency types from its commit metadata."""
    found = _GROUP_UPDATE.findall(body) or _SINGLE_UPDATE.findall(body)
    updates = [Update(name, old, new) for name, old, new in found]
    # The commit carries a YAML block: "- dependency-name: x" then "  dependency-type: y".
    types: dict[str, str] = {}
    for block in re.split(r"^(?=- dependency-name:)", commit_message, flags=re.MULTILINE):
        name = _META_NAME.search(block)
        dep_type = re.search(r"^\s+dependency-type: (\S+)$", block, re.MULTILINE)
        if name and dep_type:
            types[name.group(1).lower()] = dep_type.group(1)
    for update in updates:
        update.dep_type = types.get(update.name.lower(), "unknown")
    return updates


def dependency_group(commit_message: str) -> str:
    match = re.search(r"^\s+dependency-group: (\S+)$", commit_message, re.MULTILINE)
    return match.group(1) if match else ""


def summarize_checks(check_runs: list[dict], ignore: str = "") -> Checks:
    """Fold check runs into failed / pending / passed. ``ignore`` drops this workflow's own job."""
    checks = Checks()
    latest: dict[str, dict] = {}
    for run in check_runs:  # a re-run leaves several runs with one name: keep the newest
        name = run.get("name", "")
        if name == ignore:
            continue
        if name not in latest or (run.get("started_at") or "") > (latest[name].get("started_at") or ""):
            latest[name] = run
    for name, run in sorted(latest.items()):
        if run.get("status") != "completed":
            checks.pending.append(name)
        elif run.get("conclusion") in FAILED:
            checks.failed.append(name)
        else:
            checks.passed += 1
    return checks


def verdict(updates: list[Update], checks: Checks, advisories: list[Advisory],
            group: str) -> tuple[str, str]:
    """(verdict, one-line reason). Fixed rules, so the same inputs always give the same answer."""
    if checks.failed:
        names = ", ".join(checks.failed)
        return "HOLD", f"{len(checks.failed)} check(s) failed: {names}. Labelled `{LABEL}` for an agent to fix."
    if checks.pending:
        return "WAIT", f"{len(checks.pending)} check(s) still running; this comment updates when they finish."
    top = max((u.level for u in updates), key=LEVELS.index, default="patch")
    if top != "major":
        return "MERGE", "Patch/minor bump with every check green; auto-merge handles it."
    if advisories:
        rank = {s: i for i, s in enumerate(SEVERITY_ORDER)}
        worst = max((a.severity for a in advisories), key=lambda s: rank.get(s, -1))
        noun = "advisory" if len(advisories) == 1 else "advisories"
        return "MERGE", f"Fixes {len(advisories)} open security {noun} (worst: {worst}) and every check is green."
    if "security" in group:
        return "MERGE", "Security update group and every check is green."
    if updates and all(u.dep_type == "direct:development" for u in updates):
        return "MERGE", "Development-only dependencies and every check is green."
    return ("MERGE AFTER SMOKE", "Major bump of a runtime dependency. CI is green, but read the release notes in "
            "the PR body for removals, and run the rc checklist (docs/RELEASING.md) before `release` moves.")


def render(updates: list[Update], checks: Checks, advisories: list[Advisory], group: str, run_url: str) -> str:
    word, reason = verdict(updates, checks, advisories, group)
    lines = [MARKER, f"## Dependabot recommendation: **{word}**", "", reason, ""]
    if updates:
        lines += ["| Package | From | To | Level | Type |", "|---|---|---|---|---|"]
        lines += [f"| `{u.name}` | {u.old} | {u.new} | {u.level} | {u.dep_type} |" for u in updates]
        lines.append("")
    status = f"Checks: {checks.passed} passed"
    if checks.failed:
        status += f", {len(checks.failed)} failed ({', '.join(checks.failed)})"
    if checks.pending:
        status += f", {len(checks.pending)} running"
    lines.append(status + ".")
    if advisories:
        lines.append("")
        lines.append("Open advisories this fixes:")
        rank = {s: i for i, s in enumerate(SEVERITY_ORDER)}
        ranked = sorted(advisories, key=lambda a: -rank.get(a.severity, -1))
        lines += [f"- {a.severity} {a.ghsa} `{a.package}`: {a.summary.strip()}" for a in ranked[:MAX_LISTED]]
        if len(ranked) > MAX_LISTED:
            lines.append(f"- and {len(ranked) - MAX_LISTED} more (Security tab, Dependabot alerts)")
    if group:
        lines += ["", f"Dependabot group: `{group}`."]
    lines += ["", f"_Rules: scripts/dependabot_recommendation.py. Run: {run_url}_"]
    return "\n".join(lines) + "\n"


def should_comment(updates: list[Update], checks: Checks, has_comment: bool) -> bool:
    """Comment on majors and red PRs; keep an existing comment current; stay quiet otherwise."""
    major = any(u.level == "major" for u in updates)
    return major or bool(checks.failed) or has_comment


# ---- GitHub I/O (gh CLI) -------------------------------------------------------------------

def gh(*args: str, payload: dict | None = None) -> str:
    result = subprocess.run(["gh", *args], input=json.dumps(payload) if payload else None,
                            capture_output=True, text=True, check=True)
    return result.stdout


def gh_json(path: str) -> object:
    return json.loads(gh("api", path, "--paginate", "--slurp"))


def fetch_advisories(repo: str, updates: list[Update]) -> list[Advisory]:
    """Open Dependabot alerts the new versions fix. Best effort: an API refusal returns []."""
    out = []
    for update in updates:
        try:
            pages = gh_json(f"repos/{repo}/dependabot/alerts?state=open&package={update.name}&per_page=100")
        except subprocess.CalledProcessError:
            return out
        for alert in (a for page in pages for a in page):
            patched = ((alert.get("security_vulnerability") or {}).get("first_patched_version") or {}).get("identifier")
            if patched and _parts(patched) <= _parts(update.new):
                advisory = alert.get("security_advisory") or {}
                entry = Advisory(update.name, advisory.get("ghsa_id", "?"), advisory.get("severity", "?"),
                                 advisory.get("summary", ""))
                if all(a.ghsa != entry.ghsa for a in out):
                    out.append(entry)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--head-sha", required=True)
    parser.add_argument("--run-url", default="")
    parser.add_argument("--self-check", default="recommend", help="this workflow's job name, ignored")
    parser.add_argument("--dry-run", action="store_true", help="print the comment, change nothing")
    args = parser.parse_args(argv)

    pr = json.loads(gh("api", f"repos/{args.repo}/pulls/{args.pr}"))
    if (pr.get("user") or {}).get("login") != "dependabot[bot]" or pr.get("state") != "open":
        print(f"PR #{args.pr} is not an open Dependabot PR; nothing to do.")
        return 0
    if pr["head"]["sha"] != args.head_sha:
        print(f"PR #{args.pr} head moved past {args.head_sha}; the newer run will report.")
        return 0

    commits = [c for page in gh_json(f"repos/{args.repo}/pulls/{args.pr}/commits?per_page=100") for c in page]
    messages = [c["commit"]["message"] for c in commits]
    bot_commit = next((m for m in messages if "updated-dependencies:" in m), "")
    updates = parse_updates(pr.get("body") or "", bot_commit)
    group = dependency_group(bot_commit)
    runs = [r for page in gh_json(f"repos/{args.repo}/commits/{args.head_sha}/check-runs?per_page=100")
            for r in page["check_runs"]]
    checks = summarize_checks(runs, ignore=args.self_check)
    advisories = fetch_advisories(args.repo, updates)
    body = render(updates, checks, advisories, group, args.run_url)

    comments = [c for page in gh_json(f"repos/{args.repo}/issues/{args.pr}/comments?per_page=100") for c in page]
    mine = next((c for c in comments if MARKER in (c.get("body") or "")), None)
    labels = {label["name"] for label in pr.get("labels", [])}
    if args.dry_run:
        print(body)
        return 0

    if checks.failed and LABEL not in labels:
        gh("api", "-X", "POST", f"repos/{args.repo}/issues/{args.pr}/labels", "--input", "-",
           payload={"labels": [LABEL]})
    elif not checks.failed and not checks.pending and LABEL in labels:
        gh("api", "-X", "DELETE", f"repos/{args.repo}/issues/{args.pr}/labels/{LABEL}")
    if mine:
        gh("api", "-X", "PATCH", f"repos/{args.repo}/issues/comments/{mine['id']}", "--input", "-",
           payload={"body": body})
    elif should_comment(updates, checks, has_comment=False):
        gh("api", "-X", "POST", f"repos/{args.repo}/issues/{args.pr}/comments", "--input", "-",
           payload={"body": body})
    print(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
