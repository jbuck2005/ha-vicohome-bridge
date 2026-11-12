import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vicohome_mqtt" / "app"))

from tracker import Tracker  # noqa: E402
from vicohome import normalise_confidence, parse_device, parse_event, parse_timestamp  # noqa: E402

TZ = ZoneInfo("America/Toronto")


def raw(trace, ts, bird=None, conf=None):
    r = {"traceId": trace, "timestamp": ts, "serialNumber": "abc123", "deviceName": "Bird Cam",
         "period": 20.0, "imageUrl": f"https://img/{trace}.jpg", "videoUrl": f"https://vid/{trace}.m3u8",
         "keyshots": [{"imageUrl": f"https://key/{trace}.jpg"}]}
    if bird:
        r["subcategoryInfoList"] = [{"objectType": "bird", "objectName": bird,
                                     "birdStdName": "Latinus", "confidence": conf}]
    return r


def test_parse_event_fields():
    ev = parse_event(raw("t1", 1793826067, "Blue Jay", 0.87))
    assert ev.bird == "Blue Jay" and ev.latin == "Latinus"
    assert ev.confidence == 87.0
    assert ev.keyshot_url == "https://key/t1.jpg"
    assert ev.identified


def test_parse_event_unidentified_and_bad():
    assert parse_event(raw("t1", 1793826067)).bird == "Unidentified"
    assert not parse_event(raw("t1", 1793826067)).identified
    assert parse_event({"traceId": "x"}) is None


def test_timestamp_and_confidence_scales():
    assert parse_timestamp(1793826067000) == 1793826067
    assert parse_timestamp("1793826067") == 1793826067
    assert normalise_confidence(87) == 87
    assert normalise_confidence(0.5) == 50
    assert normalise_confidence(None) is None


def test_parse_device():
    d = parse_device({"serialNumber": "abc", "deviceName": "Cam", "batteryLevel": 96,
                      "signalStrength": -60, "ip": "192.168.1.50", "isCharging": 0})
    assert d.battery == 96 and d.signal == -60 and d.charging is False


def test_newest_first_api_order_gives_newest_last(tmp_path):
    tr = Tracker(str(tmp_path / "s.json"), TZ)
    # API order: newest first (this is what broke the old add-on)
    batch = [parse_event(raw(f"t{i}", 1793826000 - i * 600)) for i in range(5)]
    new = tr.ingest(batch)
    assert [e.trace_id for e in new] == ["t4", "t3", "t2", "t1", "t0"]  # published oldest first
    assert tr.last("abc123")["trace_id"] == "t0"  # state = newest


def test_duplicates_are_not_republished(tmp_path):
    path = str(tmp_path / "s.json")
    tr = Tracker(path, TZ)
    tr.ingest([parse_event(raw("a", 1793826000)), parse_event(raw("b", 1793826600))])
    tr.last_poll = 1793827000
    tr.save()
    tr2 = Tracker(path, TZ)  # survives a restart
    new = tr2.ingest([parse_event(raw("c", 1793827200)), parse_event(raw("b", 1793826600)),
                      parse_event(raw("c", 1793827200))])
    assert [e.trace_id for e in new] == ["c"]
    assert tr2.last("abc123")["trace_id"] == "c"


def test_refresh_takes_a_longer_period_but_ignores_relinked_urls(tmp_path):
    path = str(tmp_path / "s.json")
    tr = Tracker(path, TZ)
    short = raw("a", 1793826000) | {"period": 2.0, "videoUrl": "https://vid/a.m3u8?sig=1"}
    tr.ingest([parse_event(short)])
    tr.save()
    tr = Tracker(path, TZ)  # after a restart "last" and the history row are separate dicts
    assert tr.refresh([parse_event(short | {"videoUrl": "https://vid/a.m3u8?sig=2"})]) == 0
    assert not tr.dirty and tr.last("abc123")["video_url"].endswith("sig=1")
    assert tr.refresh([parse_event(short | {"period": 20.0, "videoUrl": "https://vid/a.m3u8?sig=3"}),
                       parse_event(raw("unknown", 1793826600))]) == 1
    assert tr.dirty
    for rec in (tr.last("abc123"), tr.recent("abc123", 1)[0]):
        assert rec["period"] == 20.0 and rec["video_url"].endswith("sig=3")


def test_late_listed_older_event_does_not_replace_last(tmp_path):
    tr = Tracker(str(tmp_path / "s.json"), TZ)
    tr.ingest([parse_event(raw("new", 1793826600))])
    tr.ingest([parse_event(raw("old", 1793826000))])  # cloud listed it late
    assert tr.last("abc123")["trace_id"] == "new"


def test_window_start(tmp_path):
    tr = Tracker(str(tmp_path / "s.json"), TZ)
    now = 1793830000
    assert tr.window_start(now, 24) == now - 24 * 3600
    tr.ingest([parse_event(raw("a", now - 100))])
    tr.last_poll = now - 60
    assert tr.window_start(now, 24) == now - 60 - 1800


def test_today_summary(tmp_path):
    tr = Tracker(str(tmp_path / "s.json"), TZ)
    midnight = datetime(2026, 11, 4, tzinfo=TZ).timestamp()
    tr.ingest([
        parse_event(raw("y", midnight - 60, "Robin", 90)),        # yesterday
        parse_event(raw("a", midnight + 3600, "Blue Jay", 80)),
        parse_event(raw("b", midnight + 7200, "Blue Jay", 70)),
        parse_event(raw("c", midnight + 9000)),                   # unidentified
    ])
    s = tr.today("abc123", midnight + 10000)
    assert s["count"] == 3 and s["identified"] == 2
    assert s["species"] == ["Blue Jay"]
    assert s["by_bird"] == {"Blue Jay": 2, "Unidentified": 1}
    assert s["last"].startswith("2026-11-04T02:30")


def test_history_is_capped(tmp_path):
    tr = Tracker(str(tmp_path / "s.json"), TZ, history_size=3)
    tr.ingest([parse_event(raw(f"t{i}", 1793826000 + i)) for i in range(10)])
    assert [r["trace_id"] for r in tr.recent("abc123", 10)] == ["t9", "t8", "t7"]


def test_daily_counts_and_complete_days(tmp_path):
    tr = Tracker(str(tmp_path / "s.json"), TZ)
    midnight = datetime(2026, 11, 5, tzinfo=TZ).timestamp()
    tr.window_start(midnight + 3600, 24)  # first start: tracking began 23:00 on the 4th
    tr.ingest([parse_event(raw("a", midnight - 600)), parse_event(raw("b", midnight + 60)),
               parse_event(raw("c", midnight + 120))])
    assert tr.daily == {"2026-11-04": 1, "2026-11-05": 2}
    assert not tr.day_complete("2026-11-04") and tr.day_complete("2026-11-05")
    tr.save()
    tr2 = Tracker(str(tmp_path / "s.json"), TZ)
    assert tr2.daily == tr.daily and not tr2.dirty


def test_old_state_rebuilds_daily_counts(tmp_path):
    import json
    path = tmp_path / "s.json"
    tr = Tracker(str(path), TZ)
    tr.ingest([parse_event(raw("a", 1793826000)), parse_event(raw("b", 1793826600))])
    tr.save()
    data = json.loads(path.read_text())
    for k in ("daily", "tracked_since", "clip_budget"):
        data.pop(k)
    path.write_text(json.dumps(data))
    tr2 = Tracker(str(path), TZ)
    assert sum(tr2.daily.values()) == 2 and tr2.tracked_since == 1793826000
