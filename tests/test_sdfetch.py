import logging, sys, struct
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vicohome_mqtt" / "app"))

import aioice.ice  # noqa: E402
import sdfetch  # noqa: E402
from sdfetch import _clip_path, _match, _shape, assign_segments, segment_name, _Tunnel  # noqa: E402
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


def test_one_recording_goes_to_one_visit():
    # one 120 s recording on the card, and two visits 99 s apart inside it
    seg = [{"name": "long.mp4", "period": 120, "timestamp": 1794517000},
           {"name": "short.mp4", "period": 20, "timestamp": 1794518000}]
    visits = [("visit-a", 1794517002), ("visit-b", 1794517101), ("visit-c", 1794518001), ("visit-d", 1794600000)]
    want, shared = assign_segments(seg, visits)
    assert want == {"long.mp4": ["visit-a"], "short.mp4": ["visit-c"]}   # visit-b keeps its cloud clip
    assert shared == 1                                                   # visit-d matched nothing at all


def test_closest_visit_wins_whatever_the_order():
    seg = [{"name": "long.mp4", "period": 120, "timestamp": 1794517000}]
    want, _ = assign_segments(seg, [("late", 1794517090), ("early", 1794517001)])
    assert want == {"long.mp4": ["early"]}


def test_too_short_recordings_are_skipped():
    seg = [{"name": "blip.mp4", "period": 2, "timestamp": 1794517000}]
    assert assign_segments(seg, [("v", 1794517000)]) == ({}, 0)


def frame(typ, sid, flags, payload=b""):
    return struct.pack(">HBBHHI", 0xABCD, typ, 0, sid, flags, len(payload)) + payload


def tunnel():
    return _Tunnel(None, "TESTSERIAL", None, logging.getLogger("test"))


def test_reassembles_status_body_and_end():
    t = tunnel()
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


def test_parses_chunks_as_they_arrive_even_split_mid_frame():
    t = tunnel()
    raw = (frame(0x12, 9, 0, b'{"contentLength":4}') + frame(0x16, 9, 1, b"wxyz") + frame(0x1A, 9, 2, b""))
    t.rx.append(raw[:15])                 # the status frame, cut short
    assert 9 not in t._collected()
    t.rx.append(raw[15:31])
    assert t._collected()[9]["status"] == b'{"contentLength":4}'
    t.rx.append(raw[31:])
    d = t._collected()[9]
    assert d["body"] == b"wxyz" and d["end"] is True
    assert t._collected()[9]["body"] == b"wxyz"   # calling again doesn't add the frames twice


def test_ice_interface_filter_is_installed_once():
    # Each connect used to wrap get_host_addresses again; a thousand wakes later ICE hit a RecursionError.
    assert aioice.ice.get_host_addresses is sdfetch._host_addresses
    sdfetch._ICE_IFACE[0] = "192.0.2.7"   # what connect() sets; it no longer wraps the function
    try:
        assert sdfetch._host_addresses(use_ipv4=True, use_ipv6=False) == ["192.0.2.7"]
        assert aioice.ice.get_host_addresses is sdfetch._host_addresses
    finally:
        sdfetch._ICE_IFACE[0] = None


def test_unanswered_consent_checks_keep_the_link_open(monkeypatch):
    """The camera stops answering ICE consent checks while it streams a clip; the link must stay up."""
    import asyncio
    from aioice import stun

    class Proto:
        calls = 0

        async def request(self, *a, **k):
            Proto.calls += 1
            raise stun.TransactionError()

    class Pair:
        protocol, remote_addr = Proto(), ("192.0.2.9", 9)

    class Conn:
        _nominated = {1: Pair()}
        remote_password = "pw"
        closed = False

        def build_request(self, pair, nominate):
            return object()

        async def close(self):
            Conn.closed = True

    monkeypatch.setattr(aioice.ice, "CONSENT_INTERVAL", 0.001)
    assert aioice.ice.Connection.query_consent is sdfetch._query_consent_keep

    async def run():
        task = asyncio.ensure_future(sdfetch._query_consent_keep(Conn()))
        while Proto.calls < 3 * sdfetch.CONSENT_LOG_AFTER:
            await asyncio.sleep(0.001)
        task.cancel()

    asyncio.run(run())
    assert not Conn.closed


def test_clip_path_prefers_the_cameras_own_local_url():
    item = {"downloadUrl": "https://127.0.0.1:15000/dc/sd/file/DIR/a.mp4?sn=TESTSERIAL"}
    assert _clip_path(item, "a.mp4", "TESTSERIAL") == "/dc/sd/file/DIR/a.mp4?sn=TESTSERIAL"
    assert _clip_path({"videoUrl": "/dc/video/mp4/DIR/a.mp4"}, "a.mp4", "S") == "/dc/video/mp4/DIR/a.mp4"


def test_clip_path_ignores_cloud_urls_and_falls_back():
    item = {"downloadUrl": "https://cloud.example/a.mp4?sig=x", "videoUrl": ""}
    assert _clip_path(item, "a.mp4", "S") == "/dc/video/mp4/a.mp4?sn=S"
    assert _clip_path({}, "a.mp4", "S") == "/dc/video/mp4/a.mp4?sn=S"


def test_shape_masks_serial_and_digits():
    assert _shape("/dc/x/mp4/2026-01-02.mp4?sn=TESTSERIAL", "TESTSERIAL") == "/dc/x/mp4/####-##-##.mp4?sn=<serial>"


def test_gives_up_when_the_camera_stops_acknowledging(monkeypatch):
    import asyncio
    import sdfetch

    class Chunk:
        tsn = 5

    class Sctp:
        _sent_queue = [Chunk()]

    class Pc:
        sctp = Sctp()
        iceConnectionState = "completed"

    t = tunnel()
    t.pc = Pc()
    monkeypatch.setattr(sdfetch, "UNACKED_GIVE_UP_S", 0.5)
    assert asyncio.run(t._wait(3, 10, label="x.mp4")) is None
    assert t.stalled is True
    assert asyncio.run(t.fetch("y.mp4", 10)) is None   # the rest of the session is skipped


def test_link_statistics_count_both_directions_without_addresses():
    import sdfetch

    class Proto:
        def __init__(self):
            self.sent = []

    class Transport:
        def __init__(self):
            self.sent = []

        def get_extra_info(self, name):
            return None

        def sendto(self, data, addr=None):
            self.sent.append(data)

    sdfetch.net_reset()
    p = sdfetch.aioice.ice.StunProtocol.__new__(sdfetch.aioice.ice.StunProtocol)
    p.receiver = type("R", (), {"data_received": lambda self, d, c: None})()
    p.local_candidate = type("C", (), {"component": 1})()
    t = Transport()
    sdfetch._sp_cm_count(p, t)
    p.transport.sendto(b"\x17" * 100, ("192.0.2.9", 5000))
    sdfetch._sp_dr_count(p, b"\x17" * 300, ("192.0.2.9", 5000))
    line = sdfetch.net_stats(None)
    assert "in 1 pkts" in line and "out 1 pkts" in line and "1 source(s)" in line
    assert "192.0.2.9" not in line
    sdfetch.NET.clear()


def test_receive_window_option_sets_the_advertised_window():
    import sdfetch

    sdfetch.RX_WINDOW_KB[0] = 128
    try:
        t = sdfetch.RTCSctpTransport.__new__(sdfetch.RTCSctpTransport)
        t._advertised_rwnd = 1024 * 1024
        orig = sdfetch._sctp_init
        sdfetch._sctp_init = lambda self, *a, **k: None
        try:
            sdfetch._sctp_init_rwnd(t)
        finally:
            sdfetch._sctp_init = orig
        assert t._advertised_rwnd == 128 * 1024
    finally:
        sdfetch.RX_WINDOW_KB[0] = 0


def test_advertised_candidate_swaps_only_the_private_address():
    import sdfetch

    line = "candidate:abc 1 udp 2130706431 172.30.33.2 41494 typ host"
    assert sdfetch._advertised(line, "172.30.33.2", "192.0.2.10") == \
        "candidate:abc 1 udp 2130706431 192.0.2.10 41494 typ host"
    assert sdfetch._advertised(line, "172.30.33.2", None) == line
    assert sdfetch._advertised(line, "198.51.100.1", "192.0.2.10") == line   # not our address: untouched


def test_mapped_address_is_read_from_a_check_response():
    import sdfetch
    from aioice import stun

    resp = stun.Message(message_method=stun.Method.BINDING, message_class=stun.Class.RESPONSE)
    resp.attributes["XOR-MAPPED-ADDRESS"] = ("192.0.2.10", 41494)
    sdfetch.net_reset()
    sdfetch._stun_count(bytes(resp), "in")
    assert sdfetch.NET["mapped"] == ("192.0.2.10", 41494) and sdfetch.NET["in_resp"] == 1
    sdfetch.NET.clear()


def test_nat_address_round_trips_through_the_data_file(tmp_path, monkeypatch):
    import sdfetch

    monkeypatch.setattr(sdfetch, "NAT_FILE", str(tmp_path / "sd_nat_ip"))
    assert sdfetch._load_nat_ip() is None
    sdfetch._save_nat_ip("192.0.2.10")
    assert sdfetch._load_nat_ip() == "192.0.2.10"
    (tmp_path / "sd_nat_ip").write_text("not an address")
    assert sdfetch._load_nat_ip() is None
