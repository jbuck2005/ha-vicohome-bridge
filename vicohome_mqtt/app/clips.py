"""Turn Vicohome HLS clips (.m3u8 + signed .ts segments) into plain MP4 files.

A downloaded .m3u8 is only a list of expiring cloud links and most players refuse it, so the
bridge remuxes each clip (no re-encoding) into an MP4 under Home Assistant's /config/www,
which HA serves at /local/... . Only the newest N clips are kept.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

LOG = logging.getLogger("vicohome.clips")

TRACE_RE = re.compile(r"^[A-Za-z0-9_-]{4,80}$")
MAX_ATTEMPTS = 10
GIVE_UP_AFTER_S = 45 * 60


def playlist_ready(text: str) -> bool:
    """True when the playlist is complete (the cloud may still be uploading segments)."""
    if not text.lstrip().startswith("#EXTM3U"):
        return False
    has_segment = any(line.strip() and not line.startswith("#") for line in text.splitlines())
    return has_segment and "#EXT-X-ENDLIST" in text


def clip_name(trace_id: str) -> str:
    if not TRACE_RE.match(trace_id):
        raise ValueError("unexpected trace id")
    return f"{trace_id}.mp4"


def files_to_prune(existing: list[str], keep_trace_ids: list[str]) -> list[str]:
    keep = {clip_name(t) for t in keep_trace_ids if TRACE_RE.match(t)}
    return sorted(f for f in existing if f.endswith(".mp4") and f not in keep)


class ClipMaker:
    def __init__(self, folder: Path, url_prefix: str, fetch_text: Callable[[str], str | None],
                 ffmpeg: str = "ffmpeg", timeout_s: int = 180) -> None:
        self.folder = folder
        self.url_prefix = url_prefix.rstrip("/")
        self.fetch_text = fetch_text
        self.ffmpeg = ffmpeg
        self.timeout_s = timeout_s
        self.pending: dict[str, dict] = {}  # trace_id -> {serial, video_url, attempts, since}
        self.failed: set[str] = set()  # gave up; not retried until the add-on restarts

    def url_for(self, trace_id: str) -> str:
        return f"{self.url_prefix}/{clip_name(trace_id)}"

    def queue(self, serial: str, trace_id: str, video_url: str) -> str | None:
        """Queue a clip. Returns its URL straight away if the file already exists."""
        if not TRACE_RE.match(trace_id):
            return None
        if (self.folder / clip_name(trace_id)).exists():
            return self.url_for(trace_id)
        if video_url and trace_id not in self.pending and trace_id not in self.failed:
            self.pending[trace_id] = {"serial": serial, "video_url": video_url,
                                      "attempts": 0, "since": time.time()}
        return None

    def process(self, budget_s: float = 120) -> list[tuple[str, str, str]]:
        """Try the pending clips. Returns (serial, trace_id, url) for each one made."""
        made, start = [], time.time()
        for trace_id, job in sorted(self.pending.items(), key=lambda kv: kv[1]["since"]):
            if time.time() - start > budget_s:
                break
            job["attempts"] += 1
            ok = self._make(trace_id, job["video_url"])
            if ok:
                made.append((job["serial"], trace_id, self.url_for(trace_id)))
            if ok or job["attempts"] >= MAX_ATTEMPTS or time.time() - job["since"] > GIVE_UP_AFTER_S:
                if not ok:
                    LOG.warning("Gave up on the clip for %s after %d tries", trace_id, job["attempts"])
                    self.failed.add(trace_id)
                del self.pending[trace_id]
        return made

    def _make(self, trace_id: str, video_url: str) -> bool:
        text = self.fetch_text(video_url)
        if not text or not playlist_ready(text):
            LOG.debug("Clip %s not ready yet", trace_id)
            return False
        self.folder.mkdir(parents=True, exist_ok=True)
        final = self.folder / clip_name(trace_id)
        tmp_out = self.folder / f".{trace_id}.part.mp4"
        with tempfile.NamedTemporaryFile("w", suffix=".m3u8", delete=False) as fh:
            fh.write(text)
            playlist = fh.name
        cmd = [self.ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
               "-protocol_whitelist", "file,http,https,tcp,tls,crypto",
               "-i", playlist, "-c", "copy", "-bsf:a", "aac_adtstoasc",
               "-movflags", "+faststart", "-y", str(tmp_out)]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=self.timeout_s)
            if res.returncode != 0 or not tmp_out.exists() or tmp_out.stat().st_size < 1024:
                # ffmpeg errors can quote the signed segment URLs: log only the first words.
                err = (res.stderr or "").strip().splitlines()[-1:] or ["no output"]
                LOG.warning("ffmpeg failed for %s: %s", trace_id, re.sub(r"https?://\S+", "<url>", err[0])[:200])
                tmp_out.unlink(missing_ok=True)
                return False
            os.replace(tmp_out, final)
            LOG.info("Saved clip %s (%d kB)", final.name, final.stat().st_size // 1024)
            return True
        except (subprocess.TimeoutExpired, OSError) as err:
            LOG.warning("Clip %s failed: %s", trace_id, err.__class__.__name__)
            tmp_out.unlink(missing_ok=True)
            return False
        finally:
            os.unlink(playlist)

    def prune(self, keep_trace_ids: list[str]) -> int:
        if not self.folder.is_dir():
            return 0
        removed = 0
        for name in files_to_prune([p.name for p in self.folder.iterdir()], keep_trace_ids):
            (self.folder / name).unlink(missing_ok=True)
            removed += 1
        return removed


def _ceil10(value: float) -> int:
    return int(-(-value // 10) * 10)


class ClipBudget:
    """Self-sizing clip limit, audited once per (complete) day.

    A day "fills the space" when that day's visits alone reach the limit, i.e. the limit could not
    hold even one day of clips. Two such days in a row grow the limit; seven days in a row using at
    most half of it shrink it (never below the minimum).
    """

    GROW_AFTER_DAYS = 2
    SHRINK_AFTER_DAYS = 7
    MIN_FREE_BYTES_TO_GROW = 2 * 1024 ** 3
    KEEP_AUDITS = 30

    def __init__(self, state: dict, minimum: int, maximum: int) -> None:
        self.state = state
        self.minimum = max(1, minimum)
        self.maximum = max(self.minimum, maximum)
        state.setdefault("audits", [])
        state.setdefault("full_streak", 0)
        state.setdefault("light_streak", 0)
        state["cap"] = min(self.maximum, max(self.minimum, int(state.get("cap") or self.minimum)))

    @property
    def cap(self) -> int:
        return self.state["cap"]

    @property
    def last_audit_day(self) -> str | None:
        return self.state.get("last_audit_day")

    def audit(self, day: str, visits: int, free_bytes: int | None = None) -> dict:
        st, cap = self.state, self.state["cap"]
        full, light = visits >= cap, visits <= cap / 2
        st["full_streak"] = st["full_streak"] + 1 if full else 0
        st["light_streak"] = st["light_streak"] + 1 if light else 0
        recent = [a["visits"] for a in st["audits"]] + [visits]
        action = "keep"
        if st["full_streak"] >= self.GROW_AFTER_DAYS:
            peak = max(recent[-self.GROW_AFTER_DAYS:])
            new = min(self.maximum, max(cap + 30, _ceil10(peak * 1.25)))
            if free_bytes is not None and free_bytes < self.MIN_FREE_BYTES_TO_GROW:
                action = "grow blocked: low disk space"
            elif new > cap:
                st["cap"], action = new, f"grow {cap}->{new}"
                st["full_streak"] = 0
            else:
                action = f"at maximum ({self.maximum})"
        elif st["light_streak"] >= self.SHRINK_AFTER_DAYS:
            peak = max(recent[-self.SHRINK_AFTER_DAYS:])
            new = max(self.minimum, _ceil10(peak * 1.5))
            if new < cap:
                st["cap"], action = new, f"shrink {cap}->{new}"
            st["light_streak"] = 0
        record = {"date": day, "visits": visits, "limit_before": cap, "limit_after": st["cap"],
                  "filled": full, "action": action}
        st["audits"] = (st["audits"] + [record])[-self.KEEP_AUDITS:]
        st["last_audit_day"] = day
        LOG.info("Clip audit %s: %d visits, limit %d -> %d (%s)", day, visits, cap, st["cap"], action)
        return record
