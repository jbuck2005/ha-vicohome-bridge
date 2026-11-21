"""Fetch a visit's full recording from the camera's microSD card when the cloud clip is short.

Battery/solar Vicohome cameras upload a short cloud clip when the link is poor, but keep the whole
recording on the card. The app plays that over a WebRTC data channel: it opens a live session, then
tunnels HTTP requests to the camera ("SafeRtc": a local proxy wraps each request as a data-channel
message ``{"action":"httpToDevice",...}``; the camera answers with ``AB CD <type> ..`` frames, type
0x12 a JSON status, 0x16 a body chunk, 0x1a end). The recordings are plain MP4 at
the listing's own local ``downloadUrl`` (else ``/dc/video/mp4/<file>?sn=<serial>``); the list comes from ``/library/newselectlibrary``
with ``fromSDCard`` over the tunnel. The camera serves the card only after a ``stopLive`` command.

This runs on the same LAN as the camera (the add-on host), so the media goes peer-to-peer. One live
session exists per camera at a time, so a fetch briefly signs the phone app out of the account; the
bridge only does it when a cloud clip is incomplete and the camera has a card.

WebRTC signalling and the aiortc work-arounds follow the MIT project averagenative/vicolocal; the
compact-SDP codec is vendored as vico_sdp.py. Nothing here is Vicohome's own code.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import secrets
import socket as _socket
import string
import struct
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import aioice.ice
import aioice.stun
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack
from aiortc.rtcsctptransport import RTCSctpTransport
from aiortc.sdp import candidate_from_sdp

from vico_sdp import compress_offer, decompress_answer

LOG = logging.getLogger("vicohome.sdfetch")

# ---- aiortc work-arounds (see module docstring / vicolocal) ----------------------------------
# The camera answers DTLS setup:active, so it is the SCTP client; force us to SCTP server or both
# send INIT and the data channel never opens.
RTCSctpTransport.is_server = property(lambda self: True)  # type: ignore[assignment]

import pylibsrtp  # noqa: E402

_srtp_init = pylibsrtp.Session.__init__


def _srtp_big_buffer(self, *a, **k):
    _srtp_init(self, *a, **k)
    self._cdata = pylibsrtp.ffi.new("char[]", 65535)
    self._buffer = pylibsrtp.ffi.buffer(self._cdata)


pylibsrtp.Session.__init__ = _srtp_big_buffer

_sp_cm = aioice.ice.StunProtocol.connection_made


def _sp_cm_bigrcv(self, transport):
    _sp_cm(self, transport)
    try:
        s = transport.get_extra_info("socket")
        if s is not None:
            s.setsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF, 8 * 1024 * 1024)
    except Exception:
        pass


aioice.ice.StunProtocol.connection_made = _sp_cm_bigrcv


# ---- link statistics (for the SD stall seen through NAT without host networking) ------------
# Counts every UDP datagram the ICE sockets send and receive, socket errors (an ICMP error from a
# NAT or the camera shows up as error_received), and the receive buffer the kernel really granted.
# Only counts and times are kept: no addresses or payloads.
NET: dict = {}


def net_reset() -> None:
    NET.clear()
    NET.update({"in": 0, "in_b": 0, "in_stun": 0, "out": 0, "out_b": 0, "out_stun": 0, "send_err": 0,
                "sock_err": 0, "last_in": 0.0, "last_out": 0.0, "srcs": set(), "rcvbuf": 0, "sacks": 0,
                "last_sack": 0.0, "udp0": _udp_counters(), "kinds_in": {}, "kinds_out": {}})


def _udp_counters() -> dict:
    """The kernel's UDP counters for this network namespace (/proc/net/snmp), e.g. RcvbufErrors."""
    try:
        with open("/proc/net/snmp", encoding="ascii") as fh:
            rows = [line.split() for line in fh if line.startswith("Udp:")]
        return dict(zip(rows[0][1:], (int(v) for v in rows[1][1:]))) if len(rows) >= 2 else {}
    except (OSError, ValueError):
        return {}


_RTCP = {200: "sr", 201: "rr", 202: "sdes", 203: "bye", 205: "nack", 206: "pli"}
_DTLS = {20: "ccs", 21: "alert", 22: "hs", 23: "data"}


def _kind(data) -> str:
    """What a datagram on the link is, by its first bytes (RFC 7983): STUN, DTLS record type, RTCP type
    or RTP. DTLS "data" carries the SCTP data channel; an "alert" means one side is closing DTLS."""
    if not data:
        return "empty"
    b = data[0]
    if b < 4:
        return "stun"
    if 20 <= b <= 63:
        return "dtls-" + _DTLS.get(b, "other")
    if 128 <= b <= 191:
        pt = data[1] if len(data) > 1 else 0
        return "rtcp-" + _RTCP.get(pt, str(pt)) if 192 <= pt <= 223 else "rtp"
    return "other"


def _count_kind(data, way: str) -> None:
    kinds = NET.setdefault("kinds_" + way, {})
    k = _kind(data)
    kinds[k] = kinds.get(k, 0) + 1


def _is_stun(data) -> bool:
    return len(data) >= 20 and data[0] < 2 and data[4:8] == b"\x21\x12\xa4\x42"


# ---- NAT: advertise the address the camera really sees -------------------------------------
# Without host networking our only candidate is the add-on network's private address, which the
# camera can't reach; it learns our NAT'd address from our checks instead. The camera answers each
# check with the address it saw (XOR-MAPPED-ADDRESS). The bridge keeps that LAN address in /data and,
# on the next session, advertises its candidate at that address (same port: Docker's NAT keeps the
# source port when it can), so the camera has no unreachable candidate. sd_nat_candidate: off stops it.
NAT_FILE = "/data/sd_nat_ip"
NAT_CANDIDATE = [True]


def _note_mapped(data) -> None:
    try:
        msg = aioice.stun.parse_message(data)
    except ValueError:
        return
    mapped = msg.attributes.get("XOR-MAPPED-ADDRESS")
    if mapped and "mapped" not in NET:
        NET["mapped"] = (str(mapped[0]), int(mapped[1]))


def _load_nat_ip() -> str | None:
    try:
        with open(NAT_FILE, encoding="ascii") as fh:
            ip = fh.read().strip()
        _socket.inet_aton(ip)
        return ip
    except (OSError, ValueError):
        return None


def _save_nat_ip(ip: str) -> None:
    try:
        with open(NAT_FILE + ".tmp", "w", encoding="ascii") as fh:
            fh.write(ip)
        os.replace(NAT_FILE + ".tmp", NAT_FILE)
    except OSError:
        pass


def _advertised(line: str, local_ip: str | None, nat_ip: str | None) -> str:
    """The candidate line with the add-on network's address swapped for the LAN address the camera sees."""
    if not (local_ip and nat_ip) or local_ip == nat_ip:
        return line
    parts = line.split()
    if len(parts) > 5 and parts[4] == local_ip:
        parts[4] = nat_ip
    return " ".join(parts)


def _stun_count(data, way: str) -> None:
    """Count a STUN message as a request or a response, per direction, and when the last one was seen.

    A request we send that gets no response means our packets no longer reach the camera (or its
    answers no longer reach us); a request the camera sends that we answer shows its side is alive."""
    if not _is_stun(data):
        return
    t = (data[0] << 8) | data[1]
    kind = "req" if (((t >> 7) & 2) | ((t >> 4) & 1)) == 0 else "resp"
    if way == "in" and kind == "resp":
        _note_mapped(data)
    NET[f"{way}_{kind}"] = NET.get(f"{way}_{kind}", 0) + 1
    NET[f"last_{way}_{kind}"] = time.time()


_sp_dr = aioice.ice.StunProtocol.datagram_received


def _sp_dr_count(self, data, addr):
    if NET:
        NET["in"] += 1
        NET["in_b"] += len(data)
        NET["last_in"] = time.time()
        NET["in_stun"] += _is_stun(data)
        _stun_count(data, "in")
        _count_kind(data, "in")
        NET["srcs"].add((addr[0], addr[1]))
    return _sp_dr(self, data, addr)


aioice.ice.StunProtocol.datagram_received = _sp_dr_count

_sp_err = aioice.ice.StunProtocol.error_received


def _sp_err_count(self, exc):
    if NET:
        NET["sock_err"] += 1
        if NET["sock_err"] <= 3:
            LOG.info("sdfetch: the link's socket reported an error: %s", exc.__class__.__name__)
    return _sp_err(self, exc)


aioice.ice.StunProtocol.error_received = _sp_err_count

_sp_cm2 = aioice.ice.StunProtocol.connection_made


def _sp_cm_count(self, transport):
    _sp_cm2(self, transport)
    try:
        s = transport.get_extra_info("socket")
        if s is not None and NET:
            NET["rcvbuf"] = max(NET["rcvbuf"], s.getsockopt(_socket.SOL_SOCKET, _socket.SO_RCVBUF))
    except Exception:
        pass
    send = transport.sendto

    def sendto(data, addr=None):
        if NET:
            NET["out"] += 1
            NET["out_b"] += len(data)
            NET["last_out"] = time.time()
            NET["out_stun"] += _is_stun(data)
            _stun_count(data, "out")
            _count_kind(data, "out")
        try:
            return send(data, addr)
        except Exception:
            if NET:
                NET["send_err"] += 1
            raise

    transport.sendto = sendto


aioice.ice.StunProtocol.connection_made = _sp_cm_count

_sctp_sack = RTCSctpTransport._send_sack


async def _sctp_sack_count(self):
    if NET:
        NET["sacks"] += 1
        NET["last_sack"] = time.time()
    return await _sctp_sack(self)


RTCSctpTransport._send_sack = _sctp_sack_count

# Optional SCTP receive window (sd_rx_window_kb): how much the camera may send before it waits
# for our acknowledgements. aiortc's default is 1 MB; a smaller window means smaller bursts.
RX_WINDOW_KB = [0]
_sctp_init = RTCSctpTransport.__init__


def _sctp_init_rwnd(self, *a, **k):
    _sctp_init(self, *a, **k)
    if RX_WINDOW_KB[0]:
        self._advertised_rwnd = int(RX_WINDOW_KB[0]) * 1024


RTCSctpTransport.__init__ = _sctp_init_rwnd


def _local_port(pc):
    try:
        return pc.sctp.transport.transport._connection._nominated.get(1).local_candidate.port
    except Exception:
        return None


def _pair_kind(pc) -> str:
    """The candidate types of the ICE pair in use, e.g. "host -> host" (no addresses)."""
    try:
        conn = pc.sctp.transport.transport._connection
        pair = conn._nominated.get(1)
        return f"{pair.local_candidate.type} -> {pair.remote_candidate.type}"
    except Exception:
        return "pair unknown"


def net_stats(pc) -> str:
    """One line of link statistics: datagrams each way, seconds since the last one each way, STUN,
    socket errors, the kernel's UDP drop counters since the session began, and SCTP progress."""
    if not NET:
        return "-"
    now = time.time()

    def ago(t):
        return f"{now - t:.0f}s" if t else "never"

    udp, udp0 = _udp_counters(), NET.get("udp0") or {}
    drops = {k: udp.get(k, 0) - udp0.get(k, 0) for k in ("InErrors", "RcvbufErrors", "SndbufErrors")
             if k in udp}
    sctp = getattr(pc, "sctp", None) if pc else None
    sc = "-"
    if sctp is not None:
        sc = (f"rx tsn {getattr(sctp, '_last_received_tsn', None)} gaps {len(getattr(sctp, '_sack_misordered', ()))} "
              f"dups {len(getattr(sctp, '_sack_duplicates', ()))} rwnd {getattr(sctp, '_advertised_rwnd', 0) // 1024}k "
              f"unacked {len(getattr(sctp, '_sent_queue', ()))} sacks {NET['sacks']} (last {ago(NET['last_sack'])})")
    return (f"in {NET['in']} pkts {NET['in_b'] // 1024} kB (last {ago(NET['last_in'])}, {len(NET['srcs'])} "
            f"source(s)); out {NET['out']} pkts {NET['out_b'] // 1024} kB (last {ago(NET['last_out'])}, send errors "
            f"{NET['send_err']}); our checks {NET.get('out_req', 0)} sent, {NET.get('in_resp', 0)} answered (last answer "
            f"{ago(NET.get('last_in_resp', 0))}); camera checks {NET.get('in_req', 0)} (last {ago(NET.get('last_in_req', 0))}), "
            f"{NET.get('out_resp', 0)} answered; socket errors "
            f"{NET['sock_err']}; rcvbuf {NET['rcvbuf'] // 1024} kB; udp {drops}; sctp {sc}; "
            f"kinds in {_kinds(NET.get('kinds_in'))} out {_kinds(NET.get('kinds_out'))}")


def _deaf() -> bool:
    """True when our last 2+ checks went unanswered for 6 s while the camera's own checks keep coming:
    the camera sends to us, but what we send no longer seems to reach it."""
    if not NET:
        return False
    now = time.time()
    unanswered = NET.get("out_req", 0) - NET.get("in_resp", 0)
    last_resp, last_req = NET.get("last_in_resp", 0), NET.get("last_in_req", 0)
    return unanswered >= 2 and bool(last_resp) and now - last_resp >= 6 and bool(last_req) and now - last_req < 3


def _kinds(d) -> str:
    return " ".join(f"{k}={v}" for k, v in sorted((d or {}).items())) or "-"


# Keep the link up when the camera stops answering ICE consent checks (RFC 7675). aioice closes the
# connection after six unanswered checks, about 30 s. The camera goes quiet on STUN once it streams a
# clip, so a clip that takes longer than that was cut off. Our own timeouts still end a dead session.
CONSENT_LOG_AFTER = 6


async def _query_consent_keep(self) -> None:
    from aioice import stun
    failures = 0
    while True:
        await asyncio.sleep(aioice.ice.CONSENT_INTERVAL * (0.8 + 0.4 * secrets.randbelow(1000) / 1000))
        for pair in list(self._nominated.values()):
            try:
                await pair.protocol.request(self.build_request(pair, nominate=False), pair.remote_addr,
                                            integrity_key=self.remote_password.encode("utf8"), retransmissions=0)
                if failures >= CONSENT_LOG_AFTER:
                    LOG.info("sdfetch: the camera answers ICE consent checks again")
                failures = 0
            except stun.TransactionError:
                failures += 1
                if failures == CONSENT_LOG_AFTER:
                    LOG.info("sdfetch: the camera stopped answering ICE consent checks; keeping the link open")


aioice.ice.Connection.query_consent = _query_consent_keep

# Gather ICE candidates on the LAN interface only. Patched once here: wrapping the function again on every
# connect stacked one wrapper per camera wake, and after about a thousand of them ICE hit a RecursionError.
_get_host_addresses = aioice.ice.get_host_addresses
_ICE_IFACE: list[str | None] = [None]


def _host_addresses(*a, **k):
    addrs = _get_host_addresses(*a, **k)
    want = _ICE_IFACE[0]
    if not want:
        return addrs
    return [h for h in addrs if h == want] or [want]


aioice.ice.get_host_addresses = _host_addresses

APP_INFO = {"appName": "VicoHome", "appType": "Android", "bundle": "com.smartaddx.vicohome",
            "channelId": 1000, "tenantId": "vicoo", "version": 406000877, "versionName": "4.6.0"}
LOCAL_ROOT = "https://127.0.0.1:15000"
_LOCAL_HOSTS = {"", "127.0.0.1", "localhost", "api-local.safemo.com"}
STATS_EVERY_S = 2        # link statistics while a clip comes off the card
STALL_NOTE_S = 3         # note once when no data has arrived for this long
UNACKED_GIVE_UP_S = 20   # the camera took the link but stopped acknowledging our messages
RESUME_AFTER_S = 6       # a clip that gets nothing new for this long is asked for again from where it stopped
RESUMES = 3              # at most this many times per clip
SEND_AUDIO = [True]      # sd_send_audio: send the camera a silent audio stream (off broke the listing in dev.17)
SESSIONS = 2             # sessions per run: a recording that blocks one gets the rest a fresh session


def _clip_paths(item: dict, name: str, serial: str) -> list[tuple[str, str]]:
    """Every tunnel path one listed recording can be asked for by, best first, as (source, path):
    the listing's local downloadUrl, then its local videoUrl, then a path built from the name."""
    out: list[tuple[str, str]] = []
    for key in ("downloadUrl", "videoUrl"):
        u = item.get(key)
        if not isinstance(u, str) or not u.strip():
            continue
        p = urlparse(u.strip())
        if (p.hostname or "") not in _LOCAL_HOSTS or not p.path.startswith("/"):
            continue
        path = p.path + (f"?{p.query}" if p.query else "")
        if path not in (q for _, q in out):
            out.append((key, path))
    built = f"/dc/video/mp4/{name}?sn={serial}"
    if built not in (q for _, q in out):
        out.append(("name", built))
    return out


def _clip_path(item: dict, name: str, serial: str) -> str:
    """The tunnel path to GET for one listed recording.

    The listing's own ``downloadUrl`` / ``videoUrl`` is used when it points at the camera (a relative
    path or the local tunnel root), so the request is the one the camera itself offers. Anything else,
    such as a cloud URL, is ignored and the path is built from the file name.
    """
    for key in ("downloadUrl", "videoUrl"):
        u = item.get(key)
        if not isinstance(u, str) or not u.strip():
            continue
        p = urlparse(u.strip())
        if (p.hostname or "") not in _LOCAL_HOSTS or not p.path.startswith("/"):
            continue
        return p.path + (f"?{p.query}" if p.query else "")
    return f"/dc/video/mp4/{name}?sn={serial}"


def _shape(text: str, serial: str) -> str:
    """A URL with the serial and every digit masked, for debug logs (shows the form, not the clip)."""
    text = text.replace(serial, "<serial>") if serial else text
    return re.sub(r"[0-9]", "#", text).replace("mp#", "mp4")


def _scp_frame(body: bytes, stream_id: int) -> bytes:
    return struct.pack(">HBBHHI", 0xABCD, 0x18, 0, stream_id, 0, len(body)) + body


def _local_candidates(pc):
    out, mid, idx = [], None, -1
    for line in pc.localDescription.sdp.splitlines():
        if line.startswith("m="):
            idx += 1
            mid = None
        elif line.startswith("a=mid:"):
            mid = line[6:].strip()
        elif line.startswith("a=candidate:"):
            out.append((line[2:], mid or "0", idx if idx >= 0 else 0))
    return out


def _payload_of(msg: dict):
    p = msg.get("messagePayload")
    if isinstance(p, dict):
        return p
    if isinstance(p, str) and p:
        try:
            return json.loads(base64.b64decode(p + "=" * (-len(p) % 4)))
        except Exception:
            try:
                return json.loads(p)
            except ValueError:
                return None
    return p


def _b64json(obj) -> str:
    return base64.b64encode(json.dumps(obj, separators=(",", ":")).encode()).decode()


def segment_name(ts: float, tz) -> str:
    """The camera names a recording by its local start time, ':' -> '-'."""
    return datetime.fromtimestamp(ts, tz).strftime("%Y-%m-%dT%H-%M-%S") + _offset(ts, tz)


def _offset(ts: float, tz) -> str:
    off = datetime.fromtimestamp(ts, tz).utcoffset() or timezone.utc.utcoffset(None)
    secs = int(off.total_seconds())
    sign = "+" if secs >= 0 else "-"
    secs = abs(secs)
    return f"{sign}{secs // 3600:02d}-{(secs % 3600) // 60:02d}"


class _Tunnel:
    """One live WebRTC session to a camera, used to list and fetch SD recordings."""

    def __init__(self, api, serial: str, iface_ip: str | None, log: logging.Logger):
        self.api, self.serial, self.iface_ip, self.log = api, serial, iface_ip, log
        self.pc = None
        self.channel = None
        self.rx: list[bytes] = []
        self._seen = 0                       # chunks of rx already parsed into _streams
        self._buf = bytearray()              # unparsed tail (an incomplete frame)
        self._parsed_bytes = 0               # bytes already parsed out of _buf (for progress logs)
        self._streams: dict[int, dict] = {}
        self.stream = secrets.randbelow(1000)
        self.first_frame = None
        self.done = None
        self._unacked = None                 # (tsn, since) of the oldest unacknowledged message
        self.stalled = False                 # the camera stopped acknowledging; the session is no use

    # ---- framing / reassembly ----
    def _collected(self) -> dict:
        """Frames received so far, by stream id. Parsed incrementally: _wait calls this every 0.3 s while a
        clip of several MB arrives, so only the chunks that came in since the last call are read."""
        if self._seen < len(self.rx):
            self._buf += b"".join(self.rx[self._seen:])
            self._seen = len(self.rx)
        raw, j = self._buf, 0
        while j + 12 <= len(raw):
            if raw[j:j + 2] != b"\xab\xcd":
                j += 1
                continue
            typ = raw[j + 2]
            sid = struct.unpack(">H", raw[j + 4:j + 6])[0]
            fl = struct.unpack(">H", raw[j + 6:j + 8])[0]
            ln = struct.unpack(">I", raw[j + 8:j + 12])[0]
            if j + 12 + ln > len(raw):
                break
            pl = bytes(raw[j + 12:j + 12 + ln])
            j += 12 + ln
            if self.log.isEnabledFor(logging.DEBUG):
                self.log.debug("sdfetch frame: type %#x stream %d flags %d length %d", typ, sid, fl, ln)
            d = self._streams.setdefault(sid, {"status": None, "body": bytearray(), "end": False})
            if typ == 0x1A:
                d["end"] = True
            elif typ == 0x12 and fl == 0:
                d["status"] = pl
            elif typ in (0x12, 0x16):
                d["body"] += pl
        self._parsed_bytes += j
        del raw[:j]   # keep only an incomplete frame at the end
        return self._streams

    @staticmethod
    def _declared(d) -> int:
        try:
            return int(json.loads(d["status"].decode()).get("contentLength") or 0) if d and d["status"] else 0
        except ValueError:
            return 0

    async def _wait(self, sid: int, timeout: float, label: str | None = None, stall_s: float | None = None):
        t0 = time.time()
        next_note = t0 + 10
        next_stats = t0 + STATS_EVERY_S
        last_rx, last_change, stall_noted = self._rx_bytes(), t0, False
        deaf_noted = False
        while time.time() - t0 < timeout:
            d = self._collected().get(sid)
            if label:
                rx = self._rx_bytes()
                if rx != last_rx:
                    if stall_noted:
                        self.log.info("sdfetch: %s: data again after %.0f s: %s", label, time.time() - last_change,
                                      net_stats(self.pc))
                    last_rx, last_change, stall_noted = rx, time.time(), False
                elif not stall_noted and time.time() - last_change >= STALL_NOTE_S:
                    stall_noted = True
                    self.log.info("sdfetch: %s: nothing new for %d s: %s", label, STALL_NOTE_S, net_stats(self.pc))
                if time.time() >= next_stats:
                    next_stats += STATS_EVERY_S
                    self.log.info("sdfetch link: %s", net_stats(self.pc))
            if d and (d["end"] or (self._declared(d) and len(d["body"]) >= self._declared(d))):
                return d
            if self.done is not None and self.done.is_set():
                break   # the camera ended the session or ICE failed; nothing more will arrive
            if label and not deaf_noted and _deaf():
                deaf_noted = True
                self.log.info("sdfetch: %s: the camera stopped answering our checks but still sends its own; our "
                              "packets may no longer be reaching it: %s", label, net_stats(self.pc))
            if stall_s and d and d["status"] and time.time() - last_change >= stall_s:
                break   # stalled part-way: the caller asks again from where it stopped
            if label and self._unacked_for() >= UNACKED_GIVE_UP_S:
                # Seen on real hardware: the camera keeps answering ICE but its data channel stops
                # acknowledging (it never takes the request), so waiting longer brings nothing.
                self.log.warning("sdfetch: %s: the camera stopped acknowledging our requests for %d s; giving up",
                                 label, UNACKED_GIVE_UP_S)
                self.stalled = True
                break
            if label and time.time() >= next_note:
                next_note += 10
                got = len(d["body"]) if d else 0
                self.log.info("sdfetch: %s: %d of %d kB after %.0f s (%d kB received in all, ICE %s, request "
                              "unacknowledged for %.0f s)", label, got // 1024, self._declared(d) // 1024,
                              time.time() - t0, self._rx_bytes() // 1024,
                              self.pc.iceConnectionState if self.pc else "-", self._unacked_for())
            await asyncio.sleep(0.3)
        return self._collected().get(sid)

    def _unacked_for(self) -> float:
        """Seconds the oldest data-channel message has waited for the camera's acknowledgement (0 if none)."""
        sent = getattr(getattr(self.pc, "sctp", None), "_sent_queue", None)
        if not sent:
            self._unacked = None
            return 0.0
        tsn = getattr(sent[0], "tsn", None)
        if not self._unacked or self._unacked[0] != tsn:
            self._unacked = (tsn, time.time())
        return time.time() - self._unacked[1]

    def _rx_bytes(self) -> int:
        return len(self._buf) + sum(len(c) for c in self.rx[self._seen:]) + self._parsed_bytes

    def _send_cmd(self, action: str, params=None) -> int:
        self.stream = (self.stream + 1) % 0xFFFF
        rid = f"{int(time.time() * 1000)}-{secrets.randbelow(1000)}"
        body = {"requestID": rid, "connectionID": "7893feb", "timeStamp": int(time.time()), "action": action}
        if params is not None:
            body["parameters"] = params
        self.channel.send(_scp_frame(json.dumps(body, separators=(",", ":")).encode(), self.stream))
        return self.stream

    def _send_http(self, method: str, path: str, headers=None, body="") -> int:
        self.stream = (self.stream + 1) % 0xFFFF
        data = {"acceptType": 0, "body": body,
                "headers": (headers or []) + [f"safe-rtc-root-url:{LOCAL_ROOT}", "safe-rtc-version:1"],
                "localDomain": "https://api-local.safemo.com", "method": method, "path": path}
        raw = json.dumps({"action": "httpToDevice", "data": data}, separators=(",", ":")).encode()
        self.channel.send(_scp_frame(raw, self.stream))
        return self.stream

    # ---- session ----
    async def connect(self, timeout: float = 45) -> None:
        _ICE_IFACE[0] = self.iface_ip   # read by _host_addresses below (one patch, not one per connect)
        net_reset()
        ticket = self.api.webrtc_ticket(self.serial)
        data = ticket.get("data") or {}
        if ticket.get("result") != 0 or not data.get("accessToken"):
            raise RuntimeError(f"webrtc ticket failed: result={ticket.get('result')} {ticket.get('msg')}")
        uid = data["id"]
        session_id = f"Android-{uid}-{int(time.time() * 1000)}"
        url = f"wss://{urlparse(data['signalServer']).hostname}:{data.get('signalPort') or 443}{data['websocketPath']}"

        pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        self.pc = pc
        pc._RTCPeerConnection__cname = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(16))
        if SEND_AUDIO[0]:
            pc.addTrack(AudioStreamTrack())   # silence, about 50 RTP packets a second
        else:
            pc.addTransceiver("audio", direction="sendrecv")   # same offer, but no packets sent
        pc.addTransceiver("video", direction="recvonly")
        self.channel = pc.createDataChannel(self.serial)  # the camera streams SD media on the serial-labelled channel
        loop = asyncio.get_running_loop()
        self.first_frame = loop.create_future()
        self.done = asyncio.Event()
        dc_open = loop.create_future()

        @self.channel.on("open")
        def _open():
            if not dc_open.done():
                dc_open.set_result(True)

        @self.channel.on("close")
        def _close():
            self.log.info("sdfetch: the camera closed the data channel")

        @self.channel.on("message")
        def _msg(m):
            self.rx.append(m if isinstance(m, (bytes, bytearray)) else m.encode("utf-8", "replace"))

        @pc.on("track")
        def _track(track):
            if track.kind != "video":
                return

            async def grab():
                from aiortc.contrib.media import MediaRelay
                sub = MediaRelay().subscribe(track)
                while not self.done.is_set():
                    try:
                        await sub.recv()
                    except Exception:
                        return
                    if not self.first_frame.done():
                        self.first_frame.set_result(True)
            asyncio.ensure_future(grab())

        @pc.on("iceconnectionstatechange")
        def _ice():
            if pc.iceConnectionState == "failed":
                self.done.set()

        import websockets
        self._ws_cm = websockets.connect(url, open_timeout=10)
        ws = await self._ws_cm.__aenter__()
        self._ws = ws
        pending, offered, answered = [], [False], [False]
        offer_msg: dict = {}

        async def send(obj):
            await ws.send(json.dumps(obj, separators=(",", ":")))

        async def on_message(msg):
            method, mtype = msg.get("method"), msg.get("messageType")
            self.log.debug("sdfetch signalling: %s %s", method, mtype or "")
            if method == "AUTH_RESPONSE":
                if msg.get("code") != 0:
                    self.done.set()
                    return
                await send({"method": "JOIN_LIVE", "group": data.get("groupId") or self.serial,
                            "role": data.get("role") or "viewer", "name": uid,
                            "traceId": data.get("traceId") or f"trace-{int(time.time() * 1000)}",
                            "traceparent": f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01",
                            "recipientClientId": self.serial, "supportRtcConnectAcceleration": 1,
                            "accessToken": data["accessToken"]})
            elif method == "JOIN_LIVE_RESPONSE":
                if msg.get("code") == 0:
                    await on_message({"method": "PEER_IN"})
            elif method == "PEER_IN" and offered[0] and not answered[0]:
                if time.time() - offer_msg.get("_t", 0) >= 3:
                    offer_msg["_t"] = time.time()
                    await send({k: v for k, v in offer_msg.items() if not k.startswith("_")})
            elif method == "PEER_IN" and not offered[0]:
                offered[0] = True
                await pc.setLocalDescription(await pc.createOffer())
                comp = compress_offer(pc.localDescription.sdp)
                offer_msg.update({"method": "TRANSMIT", "messageType": "SDP_OFFER",
                                  "messagePayload": {"type": "offer", "sdp": comp},
                                  "recipientClientId": self.serial, "senderClientId": uid, "sessionId": session_id,
                                  "viewerType": "a4x_sdk", "resolution": "auto", "version": "0.0.2", "sdpMode": 0,
                                  "_t": time.time()})
                await send({k: v for k, v in offer_msg.items() if not k.startswith("_")})
            elif method == "TRANSMIT" and mtype in ("SDP_ANSWER", "SDP_OFFER_RESPONSE"):
                p = _payload_of(msg)
                sdp_field = p.get("sdp") if isinstance(p, dict) else None
                answer = decompress_answer(sdp_field) if isinstance(sdp_field, dict) else sdp_field
                if not answer:
                    return
                answered[0] = True
                await pc.setRemoteDescription(RTCSessionDescription(sdp=answer, type="answer"))
                for c in pending:
                    await pc.addIceCandidate(c)
                pending.clear()
                nat_ip = _load_nat_ip() if NAT_CANDIDATE[0] else None
                for line, mid, idx in [(c, m, i) for c, m, i in _local_candidates(pc) if m == "0"]:
                    adv = _advertised(line, self.iface_ip, nat_ip)
                    if adv != line:
                        self.log.info("sdfetch: advertising the LAN address the camera saw last time")
                    line = adv
                    await send({"method": "TRANSMIT", "messageType": "ICE_CANDIDATE",
                                "messagePayload": _b64json({"candidate": line, "sdpMid": mid, "sdpMLineIndex": idx}),
                                "recipientClientId": self.serial, "senderClientId": uid,
                                "sessionId": session_id, "viewerType": "a4x_sdk", "version": "0.0.1"})
            elif method == "TRANSMIT" and mtype == "ICE_CANDIDATE":
                p = _payload_of(msg)
                line = (p or {}).get("candidate", "")
                if not line:
                    return
                cand = candidate_from_sdp(line.split(":", 1)[1] if line.startswith("candidate:") else line)
                cand.sdpMid, cand.sdpMLineIndex = p.get("sdpMid"), p.get("sdpMLineIndex")
                if pc.remoteDescription:
                    await pc.addIceCandidate(cand)
                else:
                    pending.append(cand)
            elif method in ("PEER_OUT", "NEED_REAUTH"):
                self.log.info("sdfetch: the camera ended the session (%s)", method)
                self.done.set()

        async def reader():
            async for raw in ws:
                try:
                    await on_message(json.loads(raw))
                except Exception as err:
                    self.log.debug("sdfetch signalling error: %s", err)

        await send({"method": "AUTH", "accessToken": data["accessToken"], "version": "0.0.1",
                    "id": uid, "clientType": "app", "status": "normal"})
        self._reader = asyncio.ensure_future(reader())
        try:
            await asyncio.wait_for(dc_open, timeout)
        except asyncio.TimeoutError:
            # Name the stage, so a link that can't reach the camera isn't logged as a bare timeout.
            raise RuntimeError(f"no link to the camera after {timeout:.0f} s (ICE {pc.iceConnectionState})") from None
        self.log.info("sdfetch: link up (%s); rcvbuf %d kB, rx window %d kB", _pair_kind(pc), NET.get("rcvbuf", 0) // 1024,
                      (getattr(pc.sctp, "_advertised_rwnd", 0) if pc.sctp else 0) // 1024)
        mapped = NET.get("mapped")
        if mapped and self.iface_ip and mapped[0] != self.iface_ip:
            local_port = _local_port(pc)
            self.log.info("sdfetch: behind NAT; the camera sees us at a %s address, port %s", 
                          "known" if mapped[0] == _load_nat_ip() else "new",
                          "kept" if local_port == mapped[1] else f"changed ({local_port} -> {mapped[1]})")
            if NAT_CANDIDATE[0] and mapped[0] != _load_nat_ip():
                _save_nat_ip(mapped[0])

    async def close(self) -> None:
        try:
            if self.done:
                self.done.set()
            if getattr(self, "_reader", None):
                self._reader.cancel()
            if self.pc:
                await self.pc.close()
            if getattr(self, "_ws_cm", None):
                await self._ws_cm.__aexit__(None, None, None)
        except Exception:
            pass

    # ---- high level ----
    async def prepare(self, frame_wait: float = 35) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(self.first_frame), frame_wait)
        except asyncio.TimeoutError:
            self.log.info("sdfetch: no video frame from the camera in %.0f s (audio %s); continuing: %s",
                          frame_wait, "sent" if SEND_AUDIO[0] else "not sent", net_stats(self.pc))
        await asyncio.sleep(1)
        self._send_cmd("stopLive", {})
        await asyncio.sleep(1)
        self._send_http("POST", "/dc/rtcconnection/open", body=json.dumps({"deviceSdpMode": 0, "serialNumber": self.serial}))
        await asyncio.sleep(2)

    async def list_segments(self, start: float, stop: float) -> list[dict]:
        body = {"app": {"appType": "Android", "version": 0}, "crossCameraOnly": False, "fromSDCard": True,
                "storeType": "sd_card", "serialNumber": [self.serial], "startTimestamp": int(start),
                "endTimestamp": int(stop), "from": 0, "to": 2000, "language": "en", "countryNo": "US"}
        sid = self._send_http("POST", "/library/newselectlibrary", headers=["Content-Type:application/json"], body=json.dumps(body))
        d = await self._wait(sid, 15)
        out = []
        if d and d["body"]:
            try:
                items = (json.loads(d["body"].decode()).get("data") or {}).get("list") or []
                if items and isinstance(items[0], dict):
                    first = items[0]
                    self.log.debug("sdfetch list: first item has %s; traceId %s", sorted(first), first.get("traceId"))
                    for key in ("downloadUrl", "videoUrl"):
                        if isinstance(first.get(key), str):
                            self.log.debug("sdfetch list: %s looks like %s", key, _shape(first[key], self.serial))
                for it in items:
                    tr = it.get("traceId", "")
                    if tr.endswith(".mp4"):
                        name = tr.rsplit("/", 1)[-1]
                        alts = _clip_paths(it, name, self.serial)
                        out.append({"name": name, "path": _clip_path(it, name, self.serial), "paths": alts,
                                    "period": it.get("period"), "timestamp": it.get("timestamp")})
            except ValueError:
                pass
        if not out:
            if not d or not d["status"]:
                self.log.info("sdfetch: the camera didn't answer the card listing in 15 s: %s", net_stats(self.pc))
            else:
                self.log.info("sdfetch: the card listing came back empty (%d bytes; %s)", len(d["body"]),
                              _shape(d["status"].decode(errors="replace")[:120], self.serial))
        return out

    async def fetch(self, name: str, timeout: float = 90, path: str | None = None) -> bytes | None:
        """GET one clip. If it stalls part-way, ask again with a Range from the byte it stopped at."""
        if self.stalled:
            return None
        path = path or f"/dc/video/mp4/{name}?sn={self.serial}"
        self.log.debug("sdfetch: GET %s", _shape(path, self.serial))
        deadline = time.time() + timeout
        body, total, tries = bytearray(), 0, 0
        while True:
            start = len(body)
            gid = self._send_http("GET", path,
                                  headers=["Accept:*/*", "Connection:close", "Host:127.0.0.1:15000",
                                           "Icy-MetaData:1", f"Range:bytes={start}-"])
            d = await self._wait(gid, max(5.0, deadline - time.time()), label=name,
                                 stall_s=RESUME_AFTER_S if tries < RESUMES else None)
            if not d or not d["status"]:
                if tries:
                    self.log.info("sdfetch: %s: the camera didn't answer the request from byte %d", name, start)
                return None
            dl = self._declared(d)
            if not tries:
                total = dl
            elif dl == total and start:
                self.log.info("sdfetch: %s: the camera ignored the range and sent the file from the start", name)
                body, start = bytearray(), 0
            elif dl != total - start:
                self.log.info("sdfetch: %s: the retry came back with %d bytes, expected %d; giving up", name, dl,
                              total - start)
                return None
            elif tries:
                self.log.info("sdfetch: %s: the camera answered the request from byte %d", name, start)
            body += d["body"][:dl] if dl else d["body"]
            if total and len(body) >= total:
                if tries:
                    self.log.info("sdfetch: %s: complete after %d retr%s", name, tries, "y" if tries == 1 else "ies")
                return bytes(body[:total])
            if (self.stalled or (self.done is not None and self.done.is_set()) or tries >= RESUMES
                    or time.time() >= deadline or not total):
                return None
            tries += 1
            self.log.info("sdfetch: %s: stalled at %d of %d kB; asking again from there (try %d of %d): %s", name,
                          len(body) // 1024, total // 1024, tries, RESUMES, net_stats(self.pc))

def _match(seg: list[dict], vts: float) -> dict | None:
    cover = [s for s in seg if s.get("timestamp") and s["timestamp"] - 5 <= vts <= s["timestamp"] + (s["period"] or 20) + 2]
    pool = cover or [s for s in seg if s.get("timestamp") and abs(s["timestamp"] - vts) <= 30]
    return min(pool, key=lambda s: abs(s["timestamp"] - vts)) if pool else None


def assign_segments(seg: list[dict], visits: list[tuple]) -> tuple[dict[str, list], int]:
    """Map SD recording name -> [trace] with each recording given to at most one visit, and count the
    visits left out because their recording went to another visit.

    Two visits can fall inside one long recording (the camera keeps recording while something keeps
    moving). Giving that file to both would make two visits out of one recording, so it goes only to the
    visit that started closest to it; the other keeps whatever clip the cloud gave it."""
    best: dict[str, tuple] = {}
    matched = 0
    for trace, vts in visits:
        m = _match(seg, vts)
        if not m or (m.get("period") or 0) < 3:
            continue
        matched += 1
        gap = abs(m["timestamp"] - vts)
        if m["name"] not in best or gap < best[m["name"]][1]:
            best[m["name"]] = (trace, gap)
    return {name: [trace] for name, (trace, _) in best.items()}, matched - len(best)


async def _run(api, serial: str, visits: list[tuple], iface_ip, per_clip_s, log) -> dict:
    """Fetch the visits' recordings. A recording whose request the camera never acknowledges blocks
    the whole data channel, so the session is closed and the rest are fetched in a fresh one, with the
    stuck recording tried last there by its next path (videoUrl, then one built from the name)."""
    out: dict = {}
    want: dict | None = None
    alts: dict[str, list] = {}
    stuck: dict[str, int] = {}          # name -> index of the path to try next
    for session in range(1, SESSIONS + 1):
        t = _Tunnel(api, serial, iface_ip, log)
        try:
            await t.connect()
            await t.prepare(35 if session == 1 else 8)   # a camera already awake needs no long wait
            if want is None:
                lo = min(v[1] for v in visits) - 60
                hi = max(v[1] for v in visits) + 60
                seg = await t.list_segments(lo, hi)
                log.info("sdfetch: camera lists %d SD segments for the window", len(seg))
                want, shared = assign_segments(seg, visits)
                if shared:
                    log.info("sdfetch: %d visit(s) share a recording with a closer visit; they keep their cloud clip",
                             shared)
                alts = {s["name"]: s.get("paths") or [("listing", s.get("path"))] for s in seg}
            todo = [n for n in want if n not in stuck] + [n for n in want if n in stuck]
            for name in todo:
                if any(tr in out for tr in want[name]):
                    continue
                paths = alts.get(name) or [("name", None)]
                i = stuck.get(name, 0)
                if i >= len(paths):
                    continue
                src, path = paths[i]
                log.info("sdfetch: GET %s by its %s (%s)", name, src, _shape(path or "", serial))
                data = await t.fetch(name, per_clip_s, path)
                if data:
                    for trace in want[name]:
                        out[trace] = data
                    log.info("sdfetch: pulled %s (%d bytes)", name, len(data))
                elif t.stalled:
                    stuck[name] = i + 1
                    log.warning("sdfetch: %s blocked the session; %s", name,
                                "trying the rest in a new session" if session < SESSIONS else "giving up for now")
                    break
                else:
                    log.warning("sdfetch: no data for %s", name)
                    stuck[name] = len(paths)   # answered but empty: don't ask again this run
            else:
                break   # every recording was tried without blocking the session
        except Exception as err:
            log.warning("sdfetch: session %d failed: %s", session, str(err) or err.__class__.__name__)
            break
        finally:
            await t.close()
    return out

async def _poll(api, serial: str, start: float, stop: float, known, max_fetch: int,
                iface_ip, per_clip_s, log) -> list[dict]:
    t = _Tunnel(api, serial, iface_ip, log)
    try:
        await t.connect()
        await t.prepare()
        seg = [s for s in await t.list_segments(start, stop)
               if s.get("timestamp") and (s.get("period") or 0) >= 3 and not known(s)]
        seg.sort(key=lambda s: s["timestamp"], reverse=True)   # newest first: they matter most
        log.info("sdpoll: camera lists %d new SD recording(s) since the last poll", len(seg))
        for s in seg[:max_fetch]:
            s["data"] = await t.fetch(s["name"], per_clip_s, s.get("path"))
            if not s["data"]:
                log.warning("sdpoll: no data for %s", s["name"])
        return sorted(seg, key=lambda s: s["timestamp"])
    finally:
        await t.close()


def poll_sd_recordings(api, serial: str, start: float, stop: float, known=lambda seg: False,
                       max_fetch: int = 30, iface_ip: str | None = None, per_clip_s: float = 90,
                       log: logging.Logger = LOG) -> list[dict] | None:
    """List the camera's SD recordings in [start, stop] that ``known(segment)`` does not already know.

    Each is a motion recording, so each is a visit. Returns them oldest first as dicts with name,
    timestamp, period and, for the newest ``max_fetch``, the MP4 bytes as "data" (None if the fetch
    failed). One WebRTC session (one camera wake). Returns None if the session could not be opened, so
    the caller can try the same window again.
    """
    try:
        return asyncio.run(_poll(api, serial, start, stop, known, max_fetch, iface_ip, per_clip_s, log))
    except Exception as err:
        log.warning("sdpoll: session failed: %s", str(err) or err.__class__.__name__)
        return None


def fetch_sd_clips(api, serial: str, visits: list[tuple], iface_ip: str | None = None,
                   per_clip_s: float = 90, log: logging.Logger = LOG) -> dict:
    """Pull the full SD recording for each (trace_id, unix_ts) visit. Returns {trace_id: mp4 bytes}.

    One WebRTC session for the batch. Safe to call from a worker thread. Never raises: logs and
    returns what it got (possibly empty) so the cloud path is never disturbed.
    """
    if not visits:
        return {}
    try:
        return asyncio.run(_run(api, serial, list(visits), iface_ip, per_clip_s, log))
    except Exception as err:
        log.warning("sdfetch: session failed: %s", str(err) or err.__class__.__name__)
        return {}
