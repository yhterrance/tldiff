# tldiff

A daily TL;DR of lines added/deleted across **everything you authored** on GitHub —
your own repos, forks, and org repos you have access to.

## How it works

1. **REST** `/user/repos` (owner + org-member + collaborator), sorted by most-recent
   push → every repo your token can access that was pushed on/after the window start.
   Discovery is **access-gated**, so it sees private/org repos that the contribution
   graph would hide (see "Private contributions" below).
2. **REST** `commits?author=you&since=…&until=…` → your commit SHAs in each repo.
3. **REST** `commits/{sha}` → exact `additions`/`deletions` per file.
4. `is_countable_file()` drops noise (lockfiles, generated/minified output, binaries,
   vendored/build dirs) so the totals reflect real authored work. Tune the denylists
   at the top of `tldiff.py`.

## Setup

Managed with [uv](https://docs.astral.sh/uv/). One command creates the virtualenv
and installs the locked dependencies:

```bash
cd ~/Projects/tldiff
uv sync
```

Create a GitHub Personal Access Token:
- **Classic** with `repo` scope (covers private repos), or
- **Fine-grained** with read access to the repos you care about.

Store it somewhere the wrapper can read at runtime — **never hardcode it**. The wrapper
defaults to 1Password CLI; edit `run.sh` if you use Keychain or a plain env var.

## Run it manually

```bash
export GITHUB_TOKEN=ghp_...        # or let run.sh fetch it
uv run tldiff.py                # today (UTC)
uv run tldiff.py 2026-05-26     # backfill a specific day
uv run tldiff.py --json         # machine-readable (see below)
uv run tldiff.py --debug        # trace discovery/commits to stderr
uv run tldiff.py --append logs/daily.jsonl   # upsert this run into the dataset
```

Flags compose: `--json --debug` prints JSON to stdout and traces to stderr, so the
stdout stream stays parseable.

Sample output:

```
tldiff for 2026-05-26  (@terrance)
====================================================
  illoca/kami                       +312   -88     (7 commits)
  terrance/dotfiles                 +14    -3      (1 commits)
----------------------------------------------------
  TOTAL                             +326   -91     (8 commits)
```

### JSON mode

`--json` emits one object per run on stdout — handy for charts, weekly rollups, or
piping into `jq`:

```json
{
  "date": "2026-05-26",
  "viewer": "terrance",
  "window": { "since": "2026-05-26T00:00:00+00:00", "until": "2026-05-27T00:00:00+00:00" },
  "totals": { "additions": 326, "deletions": 91, "commits": 8 },
  "repos": [
    { "repo": "illoca/kami", "additions": 312, "deletions": 88, "commits": 7 },
    { "repo": "terrance/dotfiles", "additions": 14, "deletions": 3, "commits": 1 }
  ]
}
```

## Accumulating a dataset (`--append`)

`--append PATH` upserts the run into a JSONL file — **one row per date**, deduped by
date and kept sorted, so re-runs and backfills update a day in place instead of
duplicating it. The scheduled job (see `run.sh`) writes to `logs/daily.jsonl`, which
grows into your chartable history.

```bash
uv run tldiff.py --append logs/daily.jsonl            # today
uv run tldiff.py 2026-05-04 --append logs/daily.jsonl # a past day, same file
```

**Seed history in one shot** — backfill the last 30 days (zsh/bash):

```bash
export GITHUB_TOKEN="$(gh auth token)"
for i in $(seq 1 30); do
  day=$(date -u -v-"$i"d +%F)          # macOS date; GNU: date -u -d "$i days ago" +%F
  uv run tldiff.py "$day" --append logs/daily.jsonl
done
```

**Chart it** — `daily.jsonl` is ready for pandas/Observable/etc. Quick terminal peek:

```bash
jq -r '[.date, .totals.additions, .totals.deletions, .totals.commits] | @tsv' logs/daily.jsonl
```

## Schedule it (macOS launchd)

```bash
cp com.terrance.tldiff.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.terrance.tldiff.plist
```

Runs daily at 23:30 local time; output is appended to `logs/YYYY-MM.log`.

## Notes & limits

- **Day boundary is UTC.** The window and REST `since/until` use UTC, so late-night
  commits land on the UTC date. Change `day_window()` if you want local time.
- **Discovery is access-gated, not profile-gated.** Repos come from `/user/repos`, so
  private and org repos count without enabling "include private contributions" on your
  GitHub profile. You see exactly what your token can read; private org repos behind
  SAML SSO need the token SSO-authorized for that org.
- **Backfilling old dates over-scans.** `discover_repos` has a lower bound (stop at
  repos pushed before the window) but no upper bound, so an old date scans every repo
  pushed since then. Running for *today* (the cron case) only touches today's repos.
- **Rate limit:** authenticated REST is 5000 req/hour. Cost ≈ (repos pushed in range) +
  (your commits that day), since each commit needs one stats request. Fine for personal
  volume.
- **Diff counting:** GitHub counts a modified line as 1 deletion + 1 addition; squash-
  merged PRs attribute the whole PR diff to the merge commit. Totals are diff-line
  counts, not "net" lines.
- **Default branch only.** REST `/commits` lists the default branch, so commits on
  unmerged feature branches aren't counted.
