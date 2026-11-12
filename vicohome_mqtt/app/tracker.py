"""Remembers which events were already published, per-camera history and today's counts.

The Vicohome API returns every event in the requested window, newest first. This keeps
track of what was seen so only NEW events are published, oldest first, and the "last"
event is always the newest one.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from datetime import datetime, tzinfo
from typing import Any

from vicohome import Event

LOG = logging.getLogger("vicohome.tracker")

SEEN_KEEP_S = 7 * 24 * 3600


def event_record(ev: Event, tz: tzinfo) -> dict[str, Any]:
    rec = asdict(ev)
    rec.pop("raw_keys", None)
    rec["time"] = datetime.fromtimestamp(ev.timestamp, tz).isoformat(timespec="seconds")
    rec["identified"] = ev.identified
    return rec


class Tracker:
    def __init__(self, path: str, tz: tzinfo, history_size: int = 1000) -> None:
        self.path = path
        self.tz = tz
        self.history_size = history_size
        self.seen: dict[str, float] = {}
        self.cameras: dict[str, dict[str, Any]] = {}
        self.last_poll: float = 0.0
        self.daily: dict[str, int] = {}  # local date -> visits
        self.tracked_since: float | None = None  # visits are complete from this time on
        self.clip_budget: dict[str, Any] = {}
        self.dirty = False
        self._load()

    # ---- persistence ----------------------------------------------------
    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as fh:
                data = json.load(fh)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as err:
            LOG.warning("State file unreadable (%s), starting fresh", err)
            return
        self.seen = {k: float(v) for k, v in (data.get("seen") or {}).items()}
        self.cameras = data.get("cameras") or {}
        self.last_poll = float(data.get("last_poll") or 0)
        self.daily = {k: int(v) for k, v in (data.get("daily") or {}).items()}
        self.tracked_since = data.get("tracked_since")
        self.clip_budget = data.get("clip_budget") or {}
        if not self.daily:  # state from 0.1/0.2: rebuild the counts from the history
            for cam in self.cameras.values():
                for rec in cam.get("history", []):
                    day = self.day_of(rec["timestamp"])
                    self.daily[day] = self.daily.get(day, 0) + 1
        if self.tracked_since is None and self.seen:
            self.tracked_since = min(self.seen.values())

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"seen": self.seen, "cameras": self.cameras, "last_poll": self.last_poll,
                       "daily": self.daily, "tracked_since": self.tracked_since,
                       "clip_budget": self.clip_budget}, fh)
        os.replace(tmp, self.path)
        self.dirty = False

    def day_of(self, ts: float) -> str:
        return datetime.fromtimestamp(ts, self.tz).strftime("%Y-%m-%d")

    def day_complete(self, day: str) -> bool:
        """True if the bridge saw every visit of that local day (tracking began before it started)."""
        if self.tracked_since is None:
            return False
        start = datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=self.tz).timestamp()
        return self.tracked_since <= start

    @property
    def bootstrapped(self) -> bool:
        return bool(self.seen) or self.last_poll > 0

    # ---- events ---------------------------------------------------------
    def window_start(self, now: float, bootstrap_hours: float, overlap_s: float = 1800) -> float:
        """Where the next query should start.

        The cloud sometimes lists an event a few minutes after it happened, so the window
        overlaps the previous poll; duplicates are dropped by trace id.
        """
        if not self.bootstrapped:
            if self.tracked_since is None:
                self.tracked_since = now - bootstrap_hours * 3600
            return now - bootstrap_hours * 3600
        return max(self.last_poll - overlap_s, now - 24 * 3600)

    def ingest(self, events: list[Event]) -> list[Event]:
        """Record events and return the ones not seen before, oldest first."""
        new = [e for e in events if e.trace_id not in self.seen]
        unique: dict[str, Event] = {e.trace_id: e for e in new}
        new = sorted(unique.values(), key=lambda e: (e.timestamp, e.trace_id))
        if new:
            self.dirty = True
        for ev in new:
            self.seen[ev.trace_id] = ev.timestamp
            day = self.day_of(ev.timestamp)
            self.daily[day] = self.daily.get(day, 0) + 1
            cam = self.cameras.setdefault(ev.serial, {"name": ev.camera_name, "history": []})
            cam["name"] = ev.camera_name
            hist = cam["history"]
            hist.insert(0, event_record(ev, self.tz))
            hist.sort(key=lambda r: r["timestamp"], reverse=True)
            del hist[self.history_size:]
            cam["last"] = hist[0]  # history is newest first, so this is always the newest event
        return new

    def refresh(self, events: list[Event]) -> int:
        """Take the newer video length (and its link) of visits already recorded.

        Only a changed period counts: the cloud may sign the links afresh on every query, and
        rewriting the state file each poll would wear the SD card.
        """
        changed = 0
        for ev in events:
            cam = self.cameras.get(ev.serial) or {}
            recs = [r for r in cam.get("history", []) if r["trace_id"] == ev.trace_id]
            if ev.period is None or not recs or recs[0].get("period") == ev.period:
                continue
            changed += 1
            self.set_field(ev.serial, ev.trace_id, "period", ev.period)
            if ev.video_url:
                self.set_field(ev.serial, ev.trace_id, "video_url", ev.video_url)
        return changed

    def set_field(self, serial: str, trace_id: str, key: str, value: Any) -> None:
        self.dirty = True
        cam = self.cameras.get(serial) or {}
        for rec in cam.get("history", []):
            if rec["trace_id"] == trace_id:
                rec[key] = value
        if (cam.get("last") or {}).get("trace_id") == trace_id:
            cam["last"][key] = value

    def prune(self, now: float) -> None:
        cutoff = now - SEEN_KEEP_S
        before = len(self.seen)
        self.seen = {k: v for k, v in self.seen.items() if v >= cutoff}
        oldest_day = self.day_of(now - 90 * 86400)
        days_before = len(self.daily)
        self.daily = {d: n for d, n in self.daily.items() if d >= oldest_day}
        if len(self.seen) != before or len(self.daily) != days_before:
            self.dirty = True

    def newest(self, count: int) -> list[dict[str, Any]]:
        """The newest visits across all cameras."""
        recs = [r for cam in self.cameras.values() for r in cam.get("history", [])]
        return sorted(recs, key=lambda r: r["timestamp"], reverse=True)[:count]

    # ---- summaries ------------------------------------------------------
    def last(self, serial: str) -> dict[str, Any] | None:
        return (self.cameras.get(serial) or {}).get("last")

    def recent(self, serial: str, count: int) -> list[dict[str, Any]]:
        return list((self.cameras.get(serial) or {}).get("history", [])[:count])

    def today(self, serial: str, now: float) -> dict[str, Any]:
        today = datetime.fromtimestamp(now, self.tz).date()
        todays = [r for r in (self.cameras.get(serial) or {}).get("history", [])
                  if datetime.fromtimestamp(r["timestamp"], self.tz).date() == today]
        by_bird: dict[str, int] = {}
        for rec in todays:
            by_bird[rec["bird"]] = by_bird.get(rec["bird"], 0) + 1
        species = sorted((b for b in by_bird if b.lower() not in ("unidentified", "unknown")))
        return {
            "count": len(todays),
            "identified": sum(1 for r in todays if r.get("identified")),
            "species": species,
            "by_bird": dict(sorted(by_bird.items(), key=lambda kv: (-kv[1], kv[0]))),
            "first": todays[-1]["time"] if todays else None,
            "last": todays[0]["time"] if todays else None,
        }
