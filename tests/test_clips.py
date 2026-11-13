import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vicohome_mqtt" / "app"))

from clips import ClipMaker, clip_name, files_to_prune, mp4_seconds, playlist_ready, playlist_size  # noqa: E402

COMPLETE = "#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:2.0,\nhttps://x/a.ts?sig=1\n#EXT-X-ENDLIST\n"


def test_playlist_ready():
    assert playlist_ready(COMPLETE)
    assert not playlist_ready(COMPLETE.replace("#EXT-X-ENDLIST\n", ""))  # still uploading
    assert not playlist_ready("#EXTM3U\n#EXT-X-ENDLIST\n")  # no segments
    assert not playlist_ready("<html>error</html>")


def test_clip_name_rejects_odd_ids():
    assert clip_name("0000000017938810222aBcDeFgHiJ") == "0000000017938810222aBcDeFgHiJ.mp4"
    with pytest.raises(ValueError):
        clip_name("../../etc/passwd")


def test_files_to_prune():
    existing = ["aaaaaaa1.mp4", "aaaaaaa2.mp4", "aaaaaaa3.mp4", ".aaaaaaa4.part.mp4x", "notes.txt"]
    assert files_to_prune(existing, ["aaaaaaa2", "aaaaaaa3"]) == ["aaaaaaa1.mp4"]


def test_queue_skips_existing_failed_and_duplicates(tmp_path):
    cm = ClipMaker(tmp_path, "/local/vicohome", lambda url: None)
    (tmp_path / "exists00.mp4").write_bytes(b"x")
    assert cm.queue("cam", "exists00", "https://v") == "/local/vicohome/exists00.mp4"
    assert cm.queue("cam", "new00001", "https://v") is None
    cm.queue("cam", "new00001", "https://v")
    assert list(cm.pending) == ["new00001"]
    cm.failed.add("bad00001")
    cm.queue("cam", "bad00001", "https://v")
    assert "bad00001" not in cm.pending


def test_not_ready_clip_is_retried_then_dropped(tmp_path):
    cm = ClipMaker(tmp_path, "/local/v", lambda url: "#EXTM3U\n")
    cm.queue("cam", "slow0001", "https://v")
    for _ in range(9):
        assert cm.process() == []
        assert "slow0001" in cm.pending
    cm.process()
    assert "slow0001" not in cm.pending and "slow0001" in cm.failed


def playlist(*durations, ended=True):
    body = "".join(f"#EXTINF:{d},\nhttps://x/{i}.ts?sig=1\n" for i, d in enumerate(durations))
    return "#EXTM3U\n#EXT-X-VERSION:3\n" + body + ("#EXT-X-ENDLIST\n" if ended else "")


def test_playlist_size():
    assert playlist_size(playlist(2.0, 2.0, 1.5)) == (3, 5.5)
    assert playlist_size(COMPLETE) == (1, 2.0)


class FakeCloud:
    """A clock and a playlist that can change between polls; remuxes are recorded, not run."""

    def __init__(self, tmp_path, text):
        self.now, self.text, self.saved = 1_000_000.0, text, []
        self.cm = ClipMaker(tmp_path, "/local/v", lambda url: self.text, clock=lambda: self.now)
        self.cm._remux = self.remux

    def remux(self, trace_id, text):
        self.saved.append((trace_id, playlist_size(text)))
        (self.cm.folder / clip_name(trace_id)).write_bytes(b"mp4")
        return True

    def poll(self, seconds=60):
        self.now += seconds
        return self.cm.process()


def test_new_visit_waits_until_the_playlist_stops_growing(tmp_path):
    fc = FakeCloud(tmp_path, playlist(2.0))  # the cloud lists the visit after the first segment
    fc.cm.queue("cam", "grow0001", "https://v", period=2.0, visit_ts=fc.now - 20)
    assert fc.poll(0) == []  # first sight: never trusted
    fc.text = playlist(2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0)
    assert fc.poll() == []  # grew: wait again
    assert fc.poll() == [("cam", "grow0001", "/local/v/grow0001.mp4")]
    assert fc.saved == [("grow0001", (10, 20.0))]


def test_new_visit_waits_while_the_cloud_says_the_video_is_longer(tmp_path):
    fc = FakeCloud(tmp_path, playlist(2.0))
    fc.cm.queue("cam", "grow0002", "https://v", period=2.0, visit_ts=fc.now)
    fc.cm.update("grow0002", "https://v2", 20.0)
    assert fc.poll(0) == [] and fc.poll() == []  # stable, but 18 s short of the period
    assert fc.cm.pending["grow0002"]["video_url"] == "https://v2"
    for _ in range(3):
        fc.poll()
    assert fc.poll() != []  # five minutes after the visit: saved anyway
    assert fc.saved == [("grow0002", (1, 2.0))]


def test_old_visit_is_saved_at_once(tmp_path):
    fc = FakeCloud(tmp_path, playlist(2.0, 2.0))
    fc.cm.queue("cam", "old00001", "https://v", period=4.0, visit_ts=fc.now - 3600)
    assert fc.poll(0) != []


def test_saved_clip_is_made_again_when_the_cloud_video_grows(tmp_path):
    fc = FakeCloud(tmp_path, playlist(2.0))
    fc.cm.queue("cam", "late0001", "https://v", period=2.0, visit_ts=fc.now - 30)
    fc.poll(0)
    assert fc.poll() != []  # looked finished twice: saved short
    fc.text = playlist(*[2.0] * 9)
    assert fc.poll(60) == []  # before the first recheck
    assert fc.poll(60) == []  # recheck at 2 min: made again, same URL, so not reported as new
    assert fc.saved == [("late0001", (1, 2.0)), ("late0001", (9, 18.0))]
    for _ in range(40):  # no more growth: no more remuxes, and the rechecks end
        fc.poll()
    assert len(fc.saved) == 2 and not fc.cm.rechecks


def test_longer_period_triggers_an_early_recheck(tmp_path):
    fc = FakeCloud(tmp_path, playlist(2.0))
    fc.cm.queue("cam", "late0002", "https://v", visit_ts=fc.now - 600)
    fc.poll(0)
    fc.text = playlist(*[2.0] * 10)
    fc.cm.update("late0002", "https://v3", 2.0)  # not longer than the file: wait for the schedule
    assert fc.poll(5) == [] and len(fc.saved) == 1
    fc.cm.update("late0002", "https://v3", 20.0)
    fc.poll(5)
    assert fc.saved[-1] == ("late0002", (10, 20.0))


def test_recheck_stops_when_the_clip_was_pruned(tmp_path):
    fc = FakeCloud(tmp_path, playlist(2.0))
    fc.cm.queue("cam", "gone0001", "https://v", visit_ts=fc.now - 600)
    fc.poll(0)
    fc.cm.prune([])
    fc.text = playlist(2.0, 2.0)
    fc.poll(200)
    assert fc.saved == [("gone0001", (1, 2.0))] and not fc.cm.rechecks


def test_remade_clip_keeps_its_url_and_reports_its_length(tmp_path):
    fc = FakeCloud(tmp_path, playlist(2.0))
    (tmp_path / "shrt0001.mp4").write_bytes(b"old")
    assert fc.cm.remake("cam", "shrt0001", "https://fresh", 20.0, fc.now - 7200, have_s=2.0)
    assert fc.poll(0) == [] and fc.saved == []  # the cloud still offers only 2 s: nothing written
    fc.text = playlist(*[2.0] * 10)
    assert fc.poll() == []  # made again under the same name, so not reported as a new clip
    assert fc.saved == [("shrt0001", (10, 20.0))]
    assert fc.cm.saved == {"shrt0001": ("cam", 20.0)}
    assert not fc.cm.rechecks and not fc.cm.pending  # an old visit: no rechecks afterwards


def test_remake_of_a_missing_clip_is_reported_like_a_new_one(tmp_path):
    fc = FakeCloud(tmp_path, playlist(2.0, 2.0))
    assert fc.cm.remake("cam", "gone0002", "https://fresh", 4.0, fc.now - 7200, have_s=None)
    assert fc.poll(0) == [("cam", "gone0002", "/local/v/gone0002.mp4")]


def test_remake_runs_after_live_clips_and_is_dropped_without_blacklisting(tmp_path):
    fc = FakeCloud(tmp_path, playlist(2.0))
    (tmp_path / "shrt0002.mp4").write_bytes(b"old")
    fc.cm.remake("cam", "shrt0002", "https://fresh", 20.0, fc.now - 7200, have_s=2.0)
    fc.now += 10
    fc.cm.queue("cam", "live0001", "https://v", period=2.0, visit_ts=fc.now - 3600)
    assert fc.cm.remake("cam", "shrt0002", "https://again", 20.0, fc.now - 7200, 2.0) is False  # already queued
    fc.poll(0)
    assert fc.saved == [("live0001", (1, 2.0))]  # the live one went first
    for _ in range(9):
        fc.poll()
    assert "shrt0002" not in fc.cm.pending and "shrt0002" not in fc.cm.failed


def mp4(version=0, scale=1000, length=2040):
    import struct

    def box(kind, body):
        return struct.pack(">I4s", 8 + len(body), kind) + body
    if version:
        mvhd = bytes([1, 0, 0, 0]) + b"\0" * 16 + struct.pack(">IQ", scale, length) + b"\0" * 80
    else:
        mvhd = bytes([0, 0, 0, 0]) + b"\0" * 8 + struct.pack(">II", scale, length) + b"\0" * 80
    return box(b"ftyp", b"isom\0\0\0\0isom") + box(b"moov", box(b"mvhd", mvhd) + box(b"trak", b"\0" * 20)) + box(b"mdat", b"\0" * 100)


def test_mp4_seconds_reads_the_movie_header(tmp_path):
    (tmp_path / "a.mp4").write_bytes(mp4())
    assert mp4_seconds(tmp_path / "a.mp4") == 2.04
    (tmp_path / "b.mp4").write_bytes(mp4(version=1, scale=90000, length=1800000))
    assert mp4_seconds(tmp_path / "b.mp4") == 20.0
    (tmp_path / "c.mp4").write_bytes(b"not a movie at all")
    assert mp4_seconds(tmp_path / "c.mp4") is None
    assert mp4_seconds(tmp_path / "missing.mp4") is None


from clips import ClipBudget  # noqa: E402


def run(budget, visits):
    for i, v in enumerate(visits):
        budget.audit(f"2026-12-{i + 1:02d}", v, free_bytes=50 * 1024 ** 3)


def test_budget_starts_at_minimum_and_one_full_day_is_not_enough():
    b = ClipBudget({}, 60, 600)
    assert b.cap == 60
    run(b, [70])
    assert b.cap == 60 and b.state["full_streak"] == 1


def test_budget_grows_after_two_full_days_in_a_row():
    b = ClipBudget({}, 60, 600)
    run(b, [70, 120])
    assert b.cap == 150  # max(60 + 30, ceil10(120 * 1.25))
    assert b.state["audits"][-1]["action"] == "grow 60->150"
    run(b, [160, 100])  # second day doesn't fill 150 -> no growth
    assert b.cap == 150


def test_budget_full_days_must_be_consecutive():
    b = ClipBudget({}, 60, 600)
    run(b, [70, 30, 70])
    assert b.cap == 60


def test_budget_keeps_growing_until_days_fit():
    b = ClipBudget({}, 60, 600)
    run(b, [200, 200])        # -> 250
    run(b, [260, 260])        # -> 330
    run(b, [260, 260, 260])   # fits now
    assert b.cap == 330


def test_budget_respects_maximum_and_low_disk():
    b = ClipBudget({}, 60, 100)
    run(b, [500, 500])
    assert b.cap == 100
    b2 = ClipBudget({}, 60, 600)
    b2.audit("2026-12-01", 90, free_bytes=1024 ** 3)
    b2.audit("2026-12-02", 90, free_bytes=1024 ** 3)
    assert b2.cap == 60 and "low disk" in b2.state["audits"][-1]["action"]


def test_budget_shrinks_after_seven_light_days_but_not_below_minimum():
    b = ClipBudget({"cap": 300}, 60, 600)
    run(b, [100] * 6)
    assert b.cap == 300
    run(b, [100])
    assert b.cap == 150  # ceil10(100 * 1.5)
    run(b, [10] * 7)
    assert b.cap == 60


def test_budget_minimum_raises_an_old_cap():
    assert ClipBudget({"cap": 30}, 60, 600).cap == 60
