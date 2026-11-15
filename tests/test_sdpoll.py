"""visit_source sd_poll: visits read off the camera's SD card. Made-up serials and times only."""

import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vicohome_mqtt" / "app"))

import bridge  # noqa: E402
import sdfetch  # noqa: E402
from clips import TRACE_RE  # noqa: E402
from tracker import Tracker  # noqa: E402
from vicohome import Device, Event  # noqa: E402

from zoneinfo import ZoneInfo  # noqa: E402

NOW = 1_800_000_000.0
SERIAL = "TESTCAM0001"


def _dev(serial=SERIAL, name="Feeder"):
    return Device(serial=serial, name=name, model="CG625", ip="", battery=80, signal=-60, charging=None,
                  wifi_channel=None, location="", network="", mac="")


class _Mqtt:
    def __init__(self): self.sent = []
    def publish(self, topic, payload, qos=0, retain=False): self.sent.append((topic, payload))


def _fake(tmp_path, opts=None):
    b = types.SimpleNamespace()
    b.opts = {"visit_source": "sd_poll", "sd_poll_minutes": 60, "bootstrap_hours": 24, **(opts or {})}
    b.tracker = Tracker(str(tmp_path / "state.json"), ZoneInfo("UTC"))
    b.device_info = {SERIAL: _dev()}
    b.clips = None
    b.base = "vicohome_mqtt"
    b.mqtt = _Mqtt()
    b.events = []
    b._next_sd_poll = 0.0
    b._lan_ip = lambda: None
    b.ensure_discovery = lambda serial: None
    b.publish_clip_storage = lambda: None
    b.publish_live_event = lambda rec: b.events.append(rec["trace_id"])
    b._save_sd_clip = lambda rec, clip: b"\xff\xd8frame"
    b._sd_known = lambda serial: bridge.Bridge._sd_known(b, serial)
    b.ingest_sd_recordings = lambda *a: bridge.Bridge.ingest_sd_recordings(b, *a)
    return b


def test_poll_minutes_are_held_to_the_floor_and_ceiling():
    assert bridge.sd_poll_minutes({}) == 60
    assert bridge.sd_poll_minutes({"sd_poll_minutes": 5}) == 15
    assert bridge.sd_poll_minutes({"sd_poll_minutes": 99999}) == 1440
    assert bridge.sd_poll_minutes({"sd_poll_minutes": "x"}) == 60
    o = bridge.load_options()
    assert o["visit_source"] == "cloud" and o["sd_poll_minutes"] == 60


def test_sd_poll_makes_clips_come_from_the_card():
    assert bridge.clip_mode({"visit_source": "sd_poll", "clip_source": "cloud_first"}) == "sd_only"
    assert bridge.clip_mode({"visit_source": "cloud", "clip_source": "cloud_only"}) == "cloud_only"


def test_trace_id_is_stable_safe_and_hides_the_serial():
    t = bridge.sd_trace_id(SERIAL, 1_799_999_000.4)
    assert t == bridge.sd_trace_id(SERIAL, 1_799_999_000.9)
    assert t != bridge.sd_trace_id("OTHERCAM0002", 1_799_999_000)
    assert TRACE_RE.match(t) and SERIAL.lower() not in t.lower()


def test_new_recordings_become_visits_once_and_fresh_ones_wait(tmp_path):
    b = _fake(tmp_path)
    b.tracker.last_sd_poll = NOW - 3600   # not the first read: events fire
    recs = [{"name": "a.mp4", "timestamp": NOW - 1800, "period": 20, "data": b"mp4"},
            {"name": "b.mp4", "timestamp": NOW - 30, "period": 10}]          # still recording
    assert bridge.Bridge.ingest_sd_recordings(b, SERIAL, recs, NOW, False) == 1
    assert b.events == [bridge.sd_trace_id(SERIAL, NOW - 1800)]
    last = b.tracker.last(SERIAL)
    assert last["bird"] == "Unidentified" and last["period"] == 20.0
    assert ("vicohome_mqtt/%s/image" % SERIAL, b"\xff\xd8frame") in b.mqtt.sent
    assert bridge.Bridge.ingest_sd_recordings(b, SERIAL, recs[:1], NOW + 600, False) == 0   # already known


def test_first_read_loads_history_without_events(tmp_path):
    b = _fake(tmp_path)
    recs = [{"name": "a.mp4", "timestamp": NOW - 7200, "period": 20}]
    assert bridge.Bridge.ingest_sd_recordings(b, SERIAL, recs, NOW, True) == 1
    assert b.events == []


def test_recordings_the_cloud_already_listed_are_not_counted_twice(tmp_path):
    b = _fake(tmp_path)
    b.tracker.ingest([Event("cloudtrace01", SERIAL, "Feeder", NOW - 600, "Blue Jay", "", 90.0, 20.0, "", "", "")])
    known = bridge.Bridge._sd_known(b, SERIAL)
    assert known({"timestamp": NOW - 603, "period": 20})        # same visit, the card's clock a little off
    assert not known({"timestamp": NOW - 1200, "period": 20})   # a different visit


def test_poll_window_interval_and_retry(tmp_path, monkeypatch):
    calls = []
    result = {"value": None}

    def fake_poll(api, serial, start, stop, known=None, **kw):
        calls.append((serial, start, stop))
        return result["value"]

    monkeypatch.setattr(sdfetch, "poll_sd_recordings", fake_poll)
    b = _fake(tmp_path, {"bootstrap_hours": 2})
    b.api = object()

    bridge.Bridge.sd_poll(b, NOW)                    # first read fails: window kept for the retry
    assert calls[-1] == (SERIAL, NOW - 7200, NOW) and b.tracker.last_sd_poll == 0
    bridge.Bridge.sd_poll(b, NOW + 60)               # within the interval: the camera is left alone
    assert len(calls) == 1

    result["value"] = []
    bridge.Bridge.sd_poll(b, NOW + 3600)
    assert calls[-1][1] == NOW + 3600 - 7200 and b.tracker.last_sd_poll == NOW + 3600
    bridge.Bridge.sd_poll(b, NOW + 7200)             # later reads overlap the last one
    assert calls[-1][1] == NOW + 3600 - bridge.SD_POLL_OVERLAP_S

    b2 = _fake(tmp_path)                             # after a restart the interval still holds
    b2.api = object()
    b2.tracker.last_sd_poll = NOW
    bridge.Bridge.sd_poll(b2, NOW + 600)
    assert len(calls) == 3


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_jpeg_frame_from_a_clip(tmp_path):
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "2",
                    "-c:v", "libx264", str(clip)], check=True)
    frame = bridge.jpeg_frame(clip, 1.0)
    assert frame and frame.startswith(b"\xff\xd8")
    assert bridge.jpeg_frame(tmp_path / "missing.mp4") is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_sd_clip_is_saved_with_a_snapshot(tmp_path):
    clip = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10", "-t", "3",
                    "-c:v", "libx264", str(clip)], check=True)
    b = _fake(tmp_path)
    folder = tmp_path / "www"
    b.clips = types.SimpleNamespace(folder=folder, ffmpeg="ffmpeg", path_for=lambda t: folder / f"{t}.mp4",
                                    url_for=lambda t: f"/local/vicohome/{t}.mp4")
    b._save_image = lambda rec, data, ctype: "vicohome/feeder/x.jpg"
    trace = bridge.sd_trace_id(SERIAL, NOW - 600)
    b.tracker.ingest([Event(trace, SERIAL, "Feeder", NOW - 600, "Unidentified", "", None, 3.0, "", "", "")])
    rec = b.tracker.last(SERIAL)
    frame = bridge.Bridge._save_sd_clip(b, rec, clip.read_bytes())
    assert frame.startswith(b"\xff\xd8")
    assert (folder / f"{trace}.mp4").exists() and not list(folder.glob("*.sdpart"))
    last = b.tracker.last(SERIAL)
    assert last["clip_url"].endswith(f"{trace}.mp4") and last["saved_image"] and last["clip_seconds"] > 2
    assert bridge.Bridge._save_sd_clip(b, rec, b"not an mp4") is None


def test_poll_lists_new_recordings_and_fetches_the_newest(monkeypatch):
    fetched = []

    class FakeTunnel:
        def __init__(self, *a): pass
        async def connect(self): pass
        async def prepare(self): pass
        async def close(self): pass

        async def list_segments(self, start, stop):
            return [{"name": f"{i}.mp4", "timestamp": 1000 + i * 100, "period": 20} for i in range(5)] + \
                   [{"name": "tiny.mp4", "timestamp": 2000, "period": 1}]

        async def fetch(self, name, timeout):
            fetched.append(name)
            return b"mp4:" + name.encode()

    monkeypatch.setattr(sdfetch, "_Tunnel", FakeTunnel)
    got = sdfetch.poll_sd_recordings(None, SERIAL, 0, 9999, known=lambda s: s["timestamp"] == 1000, max_fetch=2)
    assert [s["name"] for s in got] == ["1.mp4", "2.mp4", "3.mp4", "4.mp4"]   # oldest first, known and tiny dropped
    assert fetched == ["4.mp4", "3.mp4"] and "data" not in got[0]


def test_poll_returns_none_when_the_session_fails(monkeypatch):
    class Broken:
        def __init__(self, *a): pass
        async def connect(self): raise RuntimeError("no ticket")
        async def close(self): pass

    monkeypatch.setattr(sdfetch, "_Tunnel", Broken)
    assert sdfetch.poll_sd_recordings(None, SERIAL, 0, 1) is None
