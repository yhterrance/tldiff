#!/usr/bin/env python3
"""tldiff — daily TL;DR of lines added/deleted across everything you authored on GitHub.

Pipeline:
  1. REST /user/repos (owner+org+collaborator, push-sorted) -> repos to inspect
  2. REST list commits (author=you, since/until)            -> your SHAs per repo
  3. REST per-commit                                        -> exact additions/deletions

Discovery is access-gated (not the privacy-gated contribution graph), so private/org
repos count as long as the token can read them.

Auth: set GITHUB_TOKEN (PAT with `repo` scope for private repos).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests  # managed via uv; see pyproject.toml

API = "https://api.github.com"
TOKEN = os.environ.get("GITHUB_TOKEN", "")

SESSION = requests.Session()
SESSION.headers.update(
    {
        "Authorization": f"bearer {TOKEN}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "tldiff",
    }
)


# Denylist: a file counts UNLESS it matches one of these. Default-count means
# new file types you author (sql, toml, sh, etc.) are included automatically;
# the cost is that an unknown generated type slips through until added here.

# Dependency manifests / lockfiles — churn a lot, not authored progress.
EXCLUDED_NAMES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "npm-shrinkwrap.json",
    "poetry.lock", "pipfile.lock", "cargo.lock", "package.resolved",  # py/rust/swift
}

# Generated, minified, or binary outputs — carry real extensions but aren't code.
EXCLUDED_SUFFIXES = (
    ".d.ts", ".min.js", ".min.css", ".map",        # TS decls / bundler output
    "_pb2.py", "_pb2.pyi", "_pb2_grpc.py",         # python protobuf codegen
    ".pb.rs", ".pb.swift", ".generated.swift",     # rust / swift codegen
    ".snap", ".lock",                              # test snapshots / misc locks
    # binaries & assets (no meaningful "lines")
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".svg",
    ".zip", ".gz", ".tar", ".woff", ".woff2", ".ttf", ".mp4", ".mov",
)

# Path segments that mean "not my source" regardless of extension.
EXCLUDED_DIRS = (
    "node_modules/", "dist/", "build/", "out/", ".next/", "coverage/",  # JS/TS
    "target/", ".build/", "deriveddata/", "pods/",                     # rust/swift
    ".venv/", "venv/", "__pycache__/", "site-packages/",               # python
    "vendor/", "third_party/", "generated/", "gen/", ".git/",          # general
)


def is_countable_file(filename: str) -> bool:
    """True if this file's +/- lines should count toward the daily total.

    Denylist: everything counts except lockfiles, generated/minified output,
    binaries, and vendored/build directories. `filename` is the repo-relative
    path GitHub reports, e.g. "src/app.py".
    """
    path = filename.lower()
    if path.rsplit("/", 1)[-1] in EXCLUDED_NAMES:
        return False
    if path.endswith(EXCLUDED_SUFFIXES):
        return False
    if any(path.startswith(d) or f"/{d}" in path for d in EXCLUDED_DIRS):
        return False
    return True


def discover_repos(since: datetime, debug: bool) -> list[str]:
    """Every repo the token can access that was pushed to on/after `since`.

    Access-gated discovery: enumerates owner + org-member + collaborator repos
    sorted by most-recent push, and stops as soon as a repo predates the window.
    Unlike the contribution graph, this sees private repos regardless of the
    "include private contributions" profile setting — that was the bug.
    """
    repos: list[str] = []
    page = 1
    while True:
        batch = rest(
            "/user/repos",
            {
                "affiliation": "owner,collaborator,organization_member",
                "sort": "pushed",
                "direction": "desc",
                "per_page": 100,
                "page": page,
            },
        )
        if not batch:
            break
        for r in batch:
            # Sorted by push desc, so the first older repo means we're done.
            if datetime.fromisoformat(r["pushed_at"]) < since:
                dbg(debug, f"stop: {r['full_name']} pushed {r['pushed_at']} < window")
                return repos
            repos.append(r["full_name"])
        if len(batch) < 100:
            break
        page += 1
    return repos


def rest(path: str, params: dict | None = None) -> list | dict:
    # path is always an internal f-string ("/repos/{owner}/{name}/..."), never
    # user input; requests sends it to the pinned API host with safe encoding.
    r = SESSION.get(f"{API}{path}", params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def day_window(day: datetime) -> tuple[str, str]:
    """Return (since, until) ISO8601 UTC bounds for the calendar day in `day`."""
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return start.isoformat(), end.isoformat()


def dbg(on: bool, *args) -> None:
    """Print a diagnostic line to stderr (so it never pollutes the summary)."""
    if on:
        print("[debug]", *args, file=sys.stderr)


def upsert_jsonl(path: Path, record: dict, debug: bool) -> None:
    """Insert/replace `record` in a JSONL dataset, keyed by its "date".

    One row per date (re-runs and backfills update in place rather than
    duplicate), kept sorted by date so the file is chart-ready as-is.
    """
    rows: dict[str, dict] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                dbg(debug, f"skipping malformed line in {path}")
                continue
            rows[rec.get("date", "")] = rec
    rows[record["date"]] = record
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for d in sorted(rows):
            f.write(json.dumps(rows[d], separators=(",", ":")) + "\n")
    dbg(debug, f"upserted {record['date']} -> {path} ({len(rows)} rows total)")


def main() -> None:
    if not TOKEN:
        sys.exit("Set GITHUB_TOKEN first.")

    # Flags: --debug traces to stderr, --json emits machine-readable stdout,
    # --append PATH upserts this run into a JSONL dataset (one row per date).
    # The first remaining non-flag arg is an optional YYYY-MM-DD.
    args = sys.argv[1:]
    append_path: str | None = None
    if "--append" in args:
        i = args.index("--append")
        if i + 1 >= len(args):
            sys.exit("--append requires a file path")
        append_path = args[i + 1]
        del args[i : i + 2]  # drop flag + its value so it isn't read as a date
    debug = "--debug" in args
    as_json = "--json" in args
    dates = [a for a in args if not a.startswith("-")]
    if dates:
        day = datetime.fromisoformat(dates[0]).replace(tzinfo=timezone.utc)
    else:
        day = datetime.now(timezone.utc)
    since, until = day_window(day)
    dbg(debug, f"UTC window: {since} .. {until}")
    dbg(debug, "NOTE: window is UTC; local-evening commits land on the next UTC day.")

    login = rest("/user")["login"]
    dbg(debug, f"viewer: @{login}")
    repos = discover_repos(day.replace(hour=0, minute=0, second=0, microsecond=0), debug)
    dbg(debug, f"discovery: {len(repos)} repo(s) pushed on/after window start")

    total_add = total_del = total_commits = 0
    per_repo: dict[str, tuple[int, int, int]] = {}

    for full in repos:
        owner, name = full.split("/", 1)
        commits = rest(
            f"/repos/{owner}/{name}/commits",
            {"author": login, "since": since, "until": until, "per_page": 100},
        )
        if commits:
            dbg(debug, f"{full}: {len(commits)} commit(s) by @{login}")
        r_add = r_del = 0
        for c in commits:
            detail = rest(f"/repos/{owner}/{name}/commits/{c['sha']}")
            for f in detail.get("files", []):
                if is_countable_file(f["filename"]):
                    r_add += f.get("additions", 0)
                    r_del += f.get("deletions", 0)
        if commits:
            per_repo[full] = (r_add, r_del, len(commits))
            total_add += r_add
            total_del += r_del
            total_commits += len(commits)

    out = {
        "date": since[:10],
        "viewer": login,
        "window": {"since": since, "until": until},
        "totals": {
            "additions": total_add,
            "deletions": total_del,
            "commits": total_commits,
        },
        "repos": [
            {"repo": full, "additions": a, "deletions": d, "commits": n}
            for full, (a, d, n) in sorted(per_repo.items())
        ],
    }

    if append_path:
        upsert_jsonl(Path(append_path), out, debug)

    if as_json:
        print(json.dumps(out, indent=2))
        return

    print(f"\ntldiff for {since[:10]}  (@{login})")
    print("=" * 52)
    for full, (a, d, n) in sorted(per_repo.items()):
        print(f"  {full:<34} +{a:<6} -{d:<6} ({n} commits)")
    print("-" * 52)
    print(f"  {'TOTAL':<34} +{total_add:<6} -{total_del:<6} ({total_commits} commits)")


if __name__ == "__main__":
    main()
