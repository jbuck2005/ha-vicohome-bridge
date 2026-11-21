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
    assert calls == [("cam", ["v1", "v2"])]            # the card is the source for every visit
    assert b._next_sd_fetch == now + 6 * 3600          # window advanced by sd_fetch_hours
    bridge.Bridge.sd_backfill(b, now + 60, recs, {})   # before the window reopens: no second wake
    assert calls == [("cam", ["v1", "v2"])]


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
