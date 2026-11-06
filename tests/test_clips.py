import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vicohome_mqtt" / "app"))

from clips import ClipMaker, clip_name, files_to_prune, playlist_ready  # noqa: E402

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
