"""Which releases were made on a given day, for the one-release-a-day rule in release.yml.

A tag's own date can't answer this. Publishing a release on GitHub makes a lightweight tag, and git
dates a lightweight tag by its commit, which can be days older than the release. So the day comes
from the release's `publishedAt` (GitHub's release list), and for an annotated tag pushed by hand
without a release, from its tagger date. A lightweight tag with no release has no date of its own
and is not counted.

Usage (the inputs are what the workflow collects):
  gh release list --limit 100 --json tagName,publishedAt,isDraft > releases.json
  git for-each-ref 'refs/tags/v*' --format='%(refname:short) %(objecttype) %(taggerdate:iso-strict)' > tags.txt
  python release_day.py releases.json tags.txt --tz America/Toronto [--exclude v1.2.3] [--now ISO]

Prints `today=`, `tomorrow=` and `same_day=` (space-separated tags) lines, quoted so a shell can
source them.
"""

from __future__ import annotations

import argparse
import json
import shlex
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def _parse(stamp: str) -> datetime | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def release_times(releases: list[dict], tag_lines: list[str]) -> dict[str, datetime]:
    """When each v* release was made: a published release's time, else an annotated tag's tagger date."""
    times: dict[str, datetime] = {}
    for rel in releases:
        when = _parse(rel.get("publishedAt") or "")
        if rel.get("isDraft") or when is None or not str(rel.get("tagName", "")).startswith("v"):
            continue
        times[rel["tagName"]] = when
    for line in tag_lines:
        parts = line.split()
        if len(parts) < 3 or parts[1] != "tag" or parts[0] in times:
            continue  # lightweight ("commit"): its date is the commit's, not the tag's
        when = _parse(parts[2])
        if when is not None:
            times[parts[0]] = when
    return times


def same_day(times: dict[str, datetime], day: date, tz: ZoneInfo, exclude: str | None = None) -> list[str]:
    return sorted(tag for tag, when in times.items() if tag != exclude and when.astimezone(tz).date() == day)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("releases")
    ap.add_argument("tags")
    ap.add_argument("--tz", default="UTC")
    ap.add_argument("--exclude")
    ap.add_argument("--now", help="ISO time to treat as now (tests)")
    args = ap.parse_args(argv)
    tz = ZoneInfo(args.tz or "UTC")
    with open(args.releases, encoding="utf-8") as fh:
        releases = json.load(fh)
    with open(args.tags, encoding="utf-8") as fh:
        tag_lines = fh.read().splitlines()
    now = (_parse(args.now) if args.now else datetime.now(timezone.utc)).astimezone(tz)
    today = now.date()
    tags = same_day(release_times(releases, tag_lines), today, tz, args.exclude)
    print(f"today={today.isoformat()}")
    print(f"tomorrow={(today + timedelta(days=1)).isoformat()}")
    print(f"same_day={shlex.quote(' '.join(tags))}")  # quoted: the workflow sources these lines


if __name__ == "__main__":
    main()
