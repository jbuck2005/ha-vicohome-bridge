"""Fetch a visit's full recording from the camera's microSD card when the cloud clip is short.

Battery/solar Vicohome cameras upload a short cloud clip when the link is poor, but keep the whole
recording on the card. The app plays that over a WebRTC data channel: it opens a live session, then
tunnels HTTP requests to the camera ("SafeRtc": a local proxy wraps each request as a data-channel
message ``{"action":"httpToDevice",...}``; the camera answers with ``AB CD <type> ..`` frames, type
0x12 a JSON status, 0x16 a body chunk, 0x1a end). The recordings are plain MP4 at
``/dc/video/mp4/<local-start>.mp4?sn=<serial>`` and the list comes from ``/library/newselectlibrary``
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
import secrets
import socket as _socket
import string
import struct
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import aioice.ice
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

APP_INFO = {"appName": "VicoHome", "appType": "Android", "bundle": "com.smartaddx.vicohome",
            "channelId": 1000, "tenantId": "vicoo", "version": 406000877, "versionName": "4.6.0"}
LOCAL_ROOT = "https://127.0.0.1:15000"


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
        self.stream = secrets.randbelow(1000)
        self.first_frame = None
        self.done = None

    # ---- framing / reassembly ----
    def _collected(self) -> dict:
        streams: dict[int, dict] = {}
        raw = b"".join(self.rx)
        j = 0
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
            pl = raw[j + 12:j + 12 + ln]
            j += 12 + ln
            d = streams.setdefault(sid, {"status": None, "body": b"", "end": False})
            if typ == 0x1A:
                d["end"] = True
            elif typ == 0x12 and fl == 0:
                d["status"] = pl
            elif typ in (0x12, 0x16):
                d["body"] += pl
        return streams

    @staticmethod
    def _declared(d) -> int:
        try:
            return int(json.loads(d["status"].decode()).get("contentLength") or 0) if d and d["status"] else 0
        except ValueError:
            return 0

    async def _wait(self, sid: int, timeout: float):
        t0 = time.time()
        while time.time() - t0 < timeout:
            d = self._collected().get(sid)
            if d and (d["end"] or (self._declared(d) and len(d["body"]) >= self._declared(d))):
                return d
            await asyncio.sleep(0.3)
        return self._collected().get(sid)

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
        if self.iface_ip:
            orig = aioice.ice.get_host_addresses
            aioice.ice.get_host_addresses = lambda *a, **k: [h for h in orig(*a, **k) if h == self.iface_ip] or [self.iface_ip]
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
        pc.addTrack(AudioStreamTrack())
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
                for line, mid, idx in [(c, m, i) for c, m, i in _local_candidates(pc) if m == "0"]:
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
        await asyncio.wait_for(dc_open, timeout)

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
    async def prepare(self) -> None:
        try:
            await asyncio.wait_for(asyncio.shield(self.first_frame), 35)
        except asyncio.TimeoutError:
            self.log.debug("sdfetch: no live frame; continuing")
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
                for it in (json.loads(d["body"].decode()).get("data") or {}).get("list") or []:
                    tr = it.get("traceId", "")
                    if tr.endswith(".mp4"):
                        out.append({"name": tr.rsplit("/", 1)[-1], "period": it.get("period"), "timestamp": it.get("timestamp")})
            except ValueError:
                pass
        return out

    async def fetch(self, name: str, timeout: float = 90) -> bytes | None:
        gid = self._send_http("GET", f"/dc/video/mp4/{name}?sn={self.serial}",
                              headers=["Accept:*/*", "Connection:close", f"Host:127.0.0.1:15000",
                                       "Icy-MetaData:1", "Range:bytes=0-"])
        d = await self._wait(gid, timeout)
        if not d or not d["status"]:
            return None
        dl = self._declared(d)
        return d["body"][:dl] if dl and len(d["body"]) >= dl else None


def _match(seg: list[dict], vts: float) -> dict | None:
    cover = [s for s in seg if s.get("timestamp") and s["timestamp"] - 5 <= vts <= s["timestamp"] + (s["period"] or 20) + 2]
    pool = cover or [s for s in seg if s.get("timestamp") and abs(s["timestamp"] - vts) <= 30]
    return min(pool, key=lambda s: abs(s["timestamp"] - vts)) if pool else None


async def _run(api, serial: str, visits: list[tuple], iface_ip, per_clip_s, log) -> dict:
    t = _Tunnel(api, serial, iface_ip, log)
    out: dict = {}
    try:
        await t.connect()
        await t.prepare()
        lo = min(v[1] for v in visits) - 60
        hi = max(v[1] for v in visits) + 60
        seg = await t.list_segments(lo, hi)
        log.info("sdfetch: camera lists %d SD segments for the window", len(seg))
        want: dict[str, list] = {}
        for trace, vts in visits:
            m = _match(seg, vts)
            if m and (m.get("period") or 0) >= 3:
                want.setdefault(m["name"], []).append(trace)
        for name, traces in want.items():
            data = await t.fetch(name, per_clip_s)
            if data:
                for trace in traces:
                    out[trace] = data
                log.info("sdfetch: pulled %s (%d bytes) for %d visit(s)", name, len(data), len(traces))
            else:
                log.warning("sdfetch: no data for %s", name)
    finally:
        await t.close()
    return out


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
        log.warning("sdfetch: session failed: %s", err)
        return {}
