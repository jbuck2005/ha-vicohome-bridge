import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vicohome_mqtt" / "app"))

import bridge  # noqa: E402
from bridge import hours_from  # noqa: E402


def test_refetch_hours_from_payload():
    assert hours_from(b"PRESS", 2) == 2  # the button: use the hours entity's value
    assert hours_from(b"6", 2) == 6
    assert hours_from(b" 3.4 ", 2) == 3
    assert hours_from(b"0", 2) == 1 and hours_from(b"-5", 2) == 1
    assert hours_from(b"100", 2) == 24
    assert hours_from(b"inf", 2) == 2 and hours_from(b"nan", 2) == 2 and hours_from(b"\xff", 2) == 2


import types  # noqa: E402

import sdfetch  # noqa: E402


class _Clips:
    def busy(self, t): return False
    def path_for(self, t): return Path("/does/not/exist/" + t + ".mp4")
    def url_for(self, t): return "/local/vicohome/" + t + ".mp4"


class _Api:
    def __init__(self, card=True): self.card = card
    def sd_card_present(self, serial): return self.card


def _fake_bridge(mode, hours, card=True):
    b = types.SimpleNamespace()
    b.opts = {"clip_source": mode, "sd_fetch_hours": hours}
    b.clips = _Clips()
    b.tracker = types.SimpleNamespace(set_field=lambda *a: None)
    b.api = _Api(card)
    b._lan_ip = lambda: None
    b._next_sd_fetch = 0.0
    b.publish_clip_storage = lambda: None
    return b


def test_clip_source_and_sd_fetch_defaults():
    o = bridge.load_options()
    assert o["clip_source"] == "cloud_first" and o["sd_fetch_hours"] == 1


def test_sd_only_fetches_every_visit_and_respects_the_interval(monkeypatch):
    calls = []
    monkeypatch.setattr(sdfetch, "fetch_sd_clips",
                        lambda api, serial, visits, **kw: (calls.append((serial, [t for t, _ in visits])), {})[1])
    now = 1_000_000.0
    recs = [{"serial": "cam", "trace_id": "v1", "timestamp": now - 7200},
            {"serial": "cam", "trace_id": "v2", "timestamp": now - 3600}]
    b = _fake_bridge("sd_only", 6)
    bridge.Bridge.sd_backfill(b, now, recs, {})
    assert calls == [("cam", ["v2", "v1"])]            # every visit, newest first
    assert b._next_sd_fetch == now + 6 * 3600          # window advanced by sd_fetch_hours
    bridge.Bridge.sd_backfill(b, now + 60, recs, {})   # before the window reopens: no second wake
    assert calls == [("cam", ["v2", "v1"])]


def test_cloud_only_never_wakes_the_camera(monkeypatch):
    calls = []
    monkeypatch.setattr(sdfetch, "fetch_sd_clips", lambda *a, **k: (calls.append(1), {})[1])
    b = _fake_bridge("cloud_only", 1)
    bridge.Bridge.sd_backfill(b, 1000.0, [{"serial": "cam", "trace_id": "v1", "timestamp": 0}], {})
    assert calls == []


def test_cloud_first_only_fetches_incomplete_short_clips(monkeypatch):
    calls = []
    monkeypatch.setattr(sdfetch, "fetch_sd_clips",
                        lambda api, serial, visits, **k: (calls.append([t for t, _ in visits]), {})[1])
    ev = lambda whole, period: types.SimpleNamespace(received_all_slice=whole, period=period, video_url="u")
    recs = [{"serial": "cam", "trace_id": "done", "timestamp": 0},
            {"serial": "cam", "trace_id": "short", "timestamp": 0, "clip_seconds": 2.0}]
    fresh = {"done": ev(True, 20), "short": ev(False, 20)}
    b = _fake_bridge("cloud_first", 1)
    bridge.Bridge.sd_backfill(b, 1000.0, recs, fresh)
    assert calls == [["short"]]                        # only the incomplete, still-short clip


def test_sd_test_count_from_payload():
    assert bridge.sd_test_count(b"PRESS") == bridge.SD_TEST_DEFAULT
    assert bridge.sd_test_count(b"3") == 3 and bridge.sd_test_count(b"0") == 1
    assert bridge.sd_test_count(b"99") == bridge.SD_MAX_PER_CYCLE
    assert bridge.sd_test_count(b"nan") == bridge.SD_TEST_DEFAULT and bridge.sd_test_count(b"\xff") == 2


def test_sd_test_refetches_the_newest_clips_whatever_the_gates(monkeypatch, tmp_path):
    import threading
    calls = []
    monkeypatch.setattr(sdfetch, "fetch_sd_clips",
                        lambda api, serial, visits, **kw: (calls.append((serial, [t for t, _ in visits])), {})[1])
    recs = [{"serial": "camA", "trace_id": f"a{i}", "timestamp": 100 - i} for i in range(4)]
    recs.append({"serial": "camB", "trace_id": "b0", "timestamp": 50, "sd_tries": 9, "clip_seconds": 30})
    b = _fake_bridge("cloud_first", 24)
    b._next_sd_fetch = 1e12                       # the regular window is far off: the test ignores it
    b.lock = threading.Lock()
    b.sd_testing = True
    b.tracker = types.SimpleNamespace(set_field=lambda *a: None,
                                      newest=lambda n: sorted(recs, key=lambda r: -r["timestamp"])[:n])
    out = bridge.Bridge.sd_test(b, 2)
    assert calls == [("camA", ["a0", "a1"]), ("camB", ["b0"])]   # newest per camera, tries and clips ignored
    assert out == {"cameras": 2, "asked": 3, "saved": 0}
    assert b._next_sd_fetch == 1e12 and b.sd_testing is False


def test_sd_test_never_wakes_a_cloud_only_camera(monkeypatch):
    import threading
    calls = []
    monkeypatch.setattr(sdfetch, "fetch_sd_clips", lambda *a, **k: (calls.append(1), {})[1])
    b = _fake_bridge("cloud_only", 1)
    b.lock = threading.Lock()
    b.tracker = types.SimpleNamespace(newest=lambda n: [{"serial": "cam", "trace_id": "v1", "timestamp": 0}])
    assert bridge.Bridge.sd_test(b, 2) is None and calls == []


def test_sd_only_tries_birds_first_and_replaces_cloud_stand_ins(monkeypatch, tmp_path):
    calls, kws = [], []
    monkeypatch.setattr(sdfetch, "fetch_sd_clips",
                        lambda api, serial, visits, **kw: (calls.append([t for t, _ in visits]), kws.append(kw), {})[2])
    now = 1_000_000.0
    have = tmp_path / "stand_in.mp4"
    have.write_bytes(b"x")
    card = tmp_path / "card.mp4"
    card.write_bytes(b"x")
    recs = [{"serial": "cam", "trace_id": f"u{i}", "timestamp": now - 600 - i, "bird": "Unidentified"}
            for i in range(8)]
    recs += [{"serial": "cam", "trace_id": "jay", "timestamp": now - 9000, "bird": "Blue Jay"},
             {"serial": "cam", "trace_id": "stand_in", "timestamp": now - 700, "bird": "Unidentified",
              "clip_src": "cloud", "clip_seconds": 8},
             {"serial": "cam", "trace_id": "card", "timestamp": now - 650, "bird": "Cardinal",
              "clip_src": "sd", "clip_seconds": 30}]
    b = _fake_bridge("sd_only", 1)
    b.clips.path_for = lambda t: tmp_path / (t + ".mp4")
    bridge.Bridge.sd_backfill(b, now, recs, {})
    batch = calls[0]
    assert batch[0] == "jay"                  # a named bird goes first, however old
    assert "card" not in batch                # the card's clip is already whole
    assert len(batch) == bridge.SD_MAX_PER_CYCLE
    assert "info" in kws[0]


def test_sd_tries_only_count_when_the_camera_listed_its_card(monkeypatch):
    seen = {}
    b = _fake_bridge("sd_only", 1)
    b.tracker = types.SimpleNamespace(set_field=lambda s, t, k, v: seen.__setitem__((t, k), v))
    recs = [{"serial": "cam", "trace_id": "v1", "timestamp": 0}]
    monkeypatch.setattr(sdfetch, "fetch_sd_clips", lambda *a, **kw: {})   # no link: never listed
    bridge.Bridge.sd_backfill(b, 1000.0, recs, {})
    assert ("v1", "sd_tries") not in seen
    monkeypatch.setattr(sdfetch, "fetch_sd_clips", lambda *a, info, **kw: (info.update(listed=True), {})[1])
    b._next_sd_fetch = 0.0
    bridge.Bridge.sd_backfill(b, 1000.0, recs, {})
    assert seen[("v1", "sd_tries")] == 1


def test_clip_keep_holds_a_busy_days_clips(tmp_path, monkeypatch):
    from collections import namedtuple
    now = 1_000_000.0
    recs = [{"trace_id": f"t{i}", "timestamp": now - i * 300} for i in range(400)]   # 289 in the last 24 h (both ends)
    b = types.SimpleNamespace(budget=types.SimpleNamespace(cap=150, maximum=600),
                              tracker=types.SimpleNamespace(newest=lambda n: recs[:n]),
                              clips=types.SimpleNamespace(folder=tmp_path / "clips"))
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(bridge.shutil, "disk_usage", lambda p: usage(0, 0, 50 * 1024 ** 3))
    assert bridge.Bridge.clip_keep(b, now) == 289          # every visit of the last 24 hours
    b.budget.maximum = 200
    assert bridge.Bridge.clip_keep(b, now) == 200          # never past clips_max
    b.budget.maximum = 600
    monkeypatch.setattr(bridge.shutil, "disk_usage", lambda p: usage(0, 0, 1024 ** 3))
    assert bridge.Bridge.clip_keep(b, now) == 150          # low disk: the limit holds
    b.budget.cap = 300
    assert bridge.Bridge.clip_keep(b, now) == 300          # a quiet day: just the limit
