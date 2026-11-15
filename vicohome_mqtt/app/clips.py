"""Turn Vicohome HLS clips (.m3u8 + signed .ts segments) into plain MP4 files.

A downloaded .m3u8 is only a list of expiring cloud links and most players refuse it, so the
bridge remuxes each clip (no re-encoding) into an MP4 under Home Assistant's /config/www,
which HA serves at /local/... . Only the newest N clips are kept.

The cloud can list a visit, with a finished-looking playlist, before the whole video is uploaded,
and add segments later. So a clip is only made once its playlist stops growing, and is checked
again a few times afterwards; if the playlist grew, the file is made again under the same name.
Clips that were still cut short (made by an older version, or before the cloud had the whole
video) can be made again later with remake(): the bridge asks for that once an hour.
"""

from __future__ import annotations

import logging
import os
import re
import struct
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Callable

LOG = logging.getLogger("vicohome.clips")

TRACE_RE = re.compile(r"^[A-Za-z0-9_-]{4,80}$")
MAX_ATTEMPTS = 10
GIVE_UP_AFTER_S = 45 * 60
TRUST_AFTER_S = 5 * 60  # after this, make the clip even if the playlist is still changing
RECHECK_AFTER_S = (120, 300, 900, 1800)  # look for a longer playlist this long after saving
PERIOD_SLACK_S = 1.5
REFETCH_SLACK_S = 1.0  # a saved MP4 and its playlist can differ by a fraction of a second


def playlist_ready(text: str) -> bool:
    """True when the playlist is complete (the cloud may still be uploading segments)."""
    if not text.lstrip().startswith("#EXTM3U"):
        return False
    has_segment = any(line.strip() and not line.startswith("#") for line in text.splitlines())
    return has_segment and "#EXT-X-ENDLIST" in text


def playlist_size(text: str) -> tuple[int, float]:
    """(segments, seconds) of a playlist."""
    segments = sum(1 for line in text.splitlines() if line.strip() and not line.startswith("#"))
    seconds = 0.0
    for match in re.finditer(r"^#EXTINF:([0-9.]+)", text, re.M):
        try:
            seconds += float(match.group(1))
        except ValueError:
            pass
    return segments, round(seconds, 2)


def grew(new: tuple[int, float], old: tuple[int, float] | None) -> bool:
    return old is not None and (new[0] > old[0] or new[1] > old[1] + 0.5)


def _boxes(fh, start: int, end: int):
    """(type, body start, box end) of the MP4 boxes between start and end."""
    pos = start
    while pos + 8 <= end:
        fh.seek(pos)
        head = fh.read(8)
        if len(head) < 8:
            return
        size, kind = struct.unpack(">I4s", head)
        body = pos + 8
        if size == 1:  # 64-bit size
            size = struct.unpack(">Q", fh.read(8))[0]
            body = pos + 16
        elif size == 0:  # to the end of the file
            size = end - pos
        if size < body - pos:
            return
        yield kind, body, pos + size
        pos += size


def mp4_seconds(path: Path) -> float | None:
    """Length of an MP4 from its movie header, or None if the file isn't one. No ffprobe needed."""
    try:
        with open(path, "rb") as fh:
            end = fh.seek(0, 2)
            for kind, body, box_end in _boxes(fh, 0, end):
                if kind != b"moov":
                    continue
                for kind2, body2, box_end2 in _boxes(fh, body, box_end):
                    if kind2 != b"mvhd":
                        continue
                    fh.seek(body2)
                    mvhd = fh.read(box_end2 - body2)
                    if mvhd[0] == 1:  # version 1: 64-bit times
                        scale, length = struct.unpack(">IQ", mvhd[20:32])
                    else:
                        scale, length = struct.unpack(">II", mvhd[12:20])
                    return round(length / scale, 2) if scale else None
    except (OSError, struct.error, IndexError):
        pass
    return None


def clip_name(trace_id: str) -> str:
    if not TRACE_RE.match(trace_id):
        raise ValueError("unexpected trace id")
    return f"{trace_id}.mp4"


def files_to_prune(existing: list[str], keep_trace_ids: list[str]) -> list[str]:
    keep = {clip_name(t) for t in keep_trace_ids if TRACE_RE.match(t)}
    return sorted(f for f in existing if f.endswith(".mp4") and f not in keep)


class ClipMaker:
    def __init__(self, folder: Path, url_prefix: str, fetch_text: Callable[[str], str | None],
                 ffmpeg: str = "ffmpeg", timeout_s: int = 180,
                 clock: Callable[[], float] = time.time, ffprobe: str = "ffprobe") -> None:
        self.folder = folder
        self.url_prefix = url_prefix.rstrip("/")
        self.fetch_text = fetch_text
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe
        self.timeout_s = timeout_s
        self.clock = clock
        # trace_id -> {serial, video_url, period, attempts, since, visit, size, remake, have}
        self.pending: dict[str, dict] = {}
        # Clips already saved that may still grow: trace_id -> {serial, video_url, period, size, saved, checks, due}
        self.rechecks: dict[str, dict] = {}
        self.failed: set[str] = set()  # gave up; not retried until the add-on restarts
        self.saved: dict[str, tuple[str, float]] = {}  # trace_id -> (serial, seconds) of files written; the bridge empties it

    def url_for(self, trace_id: str) -> str:
        return f"{self.url_prefix}/{clip_name(trace_id)}"

    def path_for(self, trace_id: str) -> Path:
        return self.folder / clip_name(trace_id)

    def busy(self, trace_id: str) -> bool:
        return trace_id in self.pending or trace_id in self.rechecks

    def queue(self, serial: str, trace_id: str, video_url: str, period: float | None = None,
              visit_ts: float | None = None) -> str | None:
        """Queue a clip. Returns its URL straight away if the file already exists."""
        if not TRACE_RE.match(trace_id):
            return None
        if (self.folder / clip_name(trace_id)).exists():
            return self.url_for(trace_id)
        if video_url and trace_id not in self.pending and trace_id not in self.failed:
            now = self.clock()
            self.pending[trace_id] = {"serial": serial, "video_url": video_url, "period": period,
                                      "attempts": 0, "since": now, "visit": min(visit_ts or now, now),
                                      "size": None, "remake": False, "have": None}
        return None

    def remake(self, serial: str, trace_id: str, video_url: str, period: float | None,
               visit_ts: float | None, have_s: float | None) -> bool:
        """Make a clip again from a fresh cloud link, because it is missing or shorter than the cloud's video.

        have_s is the length of the file we have (None if it is missing): the clip is only written
        when the cloud's playlist is longer than that. These jobs run after the live ones and are
        dropped, not blacklisted, when the cloud never offers more.
        """
        if not TRACE_RE.match(trace_id) or not video_url or self.busy(trace_id):
            return False
        now = self.clock()
        self.pending[trace_id] = {"serial": serial, "video_url": video_url, "period": period,
                                  "attempts": 0, "since": now, "visit": min(visit_ts or now, now),
                                  "size": None, "remake": True, "have": have_s}
        return True

    def update(self, trace_id: str, video_url: str, period: float | None) -> None:
        """Newer details of a visit from the cloud: its video may have grown since it was queued."""
        job = self.pending.get(trace_id) or self.rechecks.get(trace_id)
        if not job:
            return
        if video_url:
            job["video_url"] = video_url
        if period is not None:
            job["period"] = period
        if trace_id in self.rechecks and self._short_of_period(job):
            job["due"] = self.clock()  # the cloud says the video is longer than our file: look now

    @staticmethod
    def _short_of_period(job: dict) -> bool:
        return bool(job.get("period") and job.get("size") and job["period"] > job["size"][1] + PERIOD_SLACK_S)

    def process(self, budget_s: float = 120) -> list[tuple[str, str, str]]:
        """Try the pending clips. Returns (serial, trace_id, url) for each one made.

        A clip made again because the cloud's video grew keeps its URL, so it is not returned.
        """
        made, start = [], self.clock()
        for trace_id, job in sorted(self.pending.items(), key=lambda kv: (kv[1]["remake"], kv[1]["since"])):
            if self.clock() - start > budget_s:
                break
            job["attempts"] += 1
            ok = self._make(trace_id, job)
            if ok and job["have"] is None:
                made.append((job["serial"], trace_id, self.url_for(trace_id)))
            if ok or job["attempts"] >= MAX_ATTEMPTS or self.clock() - job["since"] > GIVE_UP_AFTER_S:
                if not ok and job["remake"]:
                    LOG.info("The cloud offered nothing longer for clip %s; leaving it for now", trace_id)
                elif not ok:
                    LOG.warning("Gave up on the clip for %s after %d tries", trace_id, job["attempts"])
                    self.failed.add(trace_id)
                del self.pending[trace_id]
        for trace_id, job in list(self.rechecks.items()):
            if self.clock() - start > budget_s:
                break
            if self.clock() >= job["due"]:
                self._recheck(trace_id, job)
        return made

    def _make(self, trace_id: str, job: dict) -> bool:
        text = self.fetch_text(job["video_url"])
        if not text or not playlist_ready(text):
            LOG.debug("Clip %s not ready yet", trace_id)
            return False
        size, last = playlist_size(text), job["size"]
        job["size"] = size
        if job["have"] is not None and size[1] <= job["have"] + 0.5:
            LOG.debug("Clip %s: the cloud's playlist (%.1f s) is no longer than our file", trace_id, size[1])
            return False
        if self.clock() - job["visit"] < TRUST_AFTER_S:
            # Wait until two fetches a poll apart agree and the playlist is as long as the visit.
            if last is None or size != last or self._short_of_period(job):
                LOG.debug("Clip %s may still be growing (%d segments, %.1f s)", trace_id, *size)
                return False
        if not self._remux(trace_id, text, visit_ts=job["visit"]):
            return False
        now = self.clock()
        self.saved[trace_id] = (job["serial"], size[1])
        if now - job["visit"] <= GIVE_UP_AFTER_S:  # a fresh visit's video may still grow
            self.rechecks[trace_id] = {"serial": job["serial"], "video_url": job["video_url"],
                                       "period": job.get("period"), "size": size, "saved": now,
                                       "checks": 0, "due": now + RECHECK_AFTER_S[0]}
        return True

    def _recheck(self, trace_id: str, job: dict) -> None:
        job["checks"] += 1
        if job["checks"] >= len(RECHECK_AFTER_S):
            del self.rechecks[trace_id]
        else:
            job["due"] = job["saved"] + RECHECK_AFTER_S[job["checks"]]
        if not (self.folder / clip_name(trace_id)).exists():  # pruned meanwhile
            self.rechecks.pop(trace_id, None)
            return
        text = self.fetch_text(job["video_url"])
        if not text or not playlist_ready(text):
            return
        size = playlist_size(text)
        if not grew(size, job["size"]):
            return
        LOG.info("Clip %s grew in the cloud (%.1f s -> %.1f s), saving it again",
                 trace_id, job["size"][1], size[1])
        if self._remux(trace_id, text):
            job["size"] = size
            self.saved[trace_id] = (job["serial"], size[1])

    def file_seconds(self, trace_id: str) -> float | None:
        """Length of a saved MP4, or None if ffprobe can't tell."""
        cmd = [self.ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
               str(self.folder / clip_name(trace_id))]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return float(res.stdout.strip()) if res.returncode == 0 else None
        except (subprocess.TimeoutExpired, OSError, ValueError):
            return None

    def refetch(self, visits: list[tuple[str, str]]) -> dict[str, int]:
        """Make saved clips again where the cloud now has a longer video.

        visits: (trace_id, video_url) for each visit to look at, with "" as the URL when the
        cloud no longer lists it. Only clips already on disk are looked at, so the clip limit holds.
        """
        counts = {"checked": 0, "remade": 0, "unchanged": 0, "gone": 0, "failed": 0}
        for trace_id, video_url in visits:
            if not TRACE_RE.match(trace_id) or not (self.folder / clip_name(trace_id)).exists():
                continue
            counts["checked"] += 1
            text = self.fetch_text(video_url) if video_url else None
            if not text or not playlist_ready(text):
                counts["gone"] += 1
                continue
            size, have = playlist_size(text), self.file_seconds(trace_id)
            if have is not None and size[1] <= have + REFETCH_SLACK_S:
                counts["unchanged"] += 1
                continue
            if not self._remux(trace_id, text):
                counts["failed"] += 1
                continue
            counts["remade"] += 1
            if trace_id in self.rechecks:
                self.rechecks[trace_id]["size"] = size
        LOG.info("Re-fetched clips: checked %d, re-made %d, unchanged %d, gone from the cloud %d%s",
                 counts["checked"], counts["remade"], counts["unchanged"], counts["gone"],
                 f", failed {counts['failed']}" if counts["failed"] else "")
        return counts

    def _remux(self, trace_id: str, text: str, visit_ts: float | None = None) -> bool:
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
            # A fresh visit's clip is saved a poll or two after its event line, often after the next
            # visit's lines, so name the visit it belongs to.
            visit = f" for the visit at {time.strftime('%H:%M:%S', time.localtime(visit_ts))}" if visit_ts else ""
            LOG.info("Saved clip %s (%d kB, %.1f s)%s", final.name, final.stat().st_size // 1024,
                     playlist_size(text)[1], visit)
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
