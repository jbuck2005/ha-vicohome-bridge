import sys, struct
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vicohome_mqtt" / "app"))

from sdfetch import _match, segment_name, _Tunnel  # noqa: E402
from datetime import timezone, timedelta  # noqa: E402

EST = timezone(timedelta(hours=-5))


def test_segment_name_local_start():
    # 2026-11-12 16:05:50 -05:00 -> the camera's filename form
    assert segment_name(1794517550, EST) == "2026-11-12T16-05-50-05-00"


def test_match_picks_the_covering_segment():
    seg = [{"name": "a.mp4", "period": 20, "timestamp": 1794517550},
           {"name": "b.mp4", "period": 20, "timestamp": 1794517000}]
    # a visit at 16:05:42 is covered by the 16:05:50 segment (starts a few s later, within slack)
    assert _match(seg, 1794517542)["name"] == "a.mp4"
    # a visit far from any segment start
    assert _match(seg, 1794500000) is None


def frame(typ, sid, flags, payload=b""):
    return struct.pack(">HBBHHI", 0xABCD, typ, 0, sid, flags, len(payload)) + payload


def test_reassembles_status_body_and_end():
    t = _Tunnel.__new__(_Tunnel)
    t.rx = [
        frame(0x12, 7, 0, b'{"httpCode":200,"errorCode":0,"contentLength":6}'),
        frame(0x16, 7, 1, b"abc"),
        frame(0x16, 7, 1, b"def"),
        frame(0x1A, 7, 2, b""),
    ]
    d = t._collected()[7]
    assert d["end"] is True
    assert d["body"] == b"abcdef"
    assert _Tunnel._declared(d) == 6
