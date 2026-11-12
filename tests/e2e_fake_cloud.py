"""End-to-end check: fake Vicohome API + real MQTT broker + the bridge.

Run (needs a broker at $MQTT_HOST, anonymous):  python tests/e2e_fake_cloud.py
Poll 1 = initial history (no events fired), poll 2 adds a new bird that must fire. As the real
cloud does since Nov 2026, its clip is first listed with only one segment and grows by poll 3;
the bridge must wait and save the whole clip on poll 4.
"""

import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import paho.mqtt.client as mqtt

APP = Path(__file__).resolve().parent.parent / "vicohome_mqtt" / "app"
sys.path.insert(0, str(APP))

NOW = int(time.time())
CALLS = {"login": 0, "events": 0}
JPEG = b"\xff\xd8\xff\xe0fakejpeg\xff\xd9"
HLS_DIR = Path("/tmp/hls")


def make_hls():
    import subprocess
    HLS_DIR.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10",
                    "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000", "-t", "4",
                    "-c:v", "libx264", "-c:a", "aac", "-f", "hls", "-hls_time", "2",
                    "-hls_list_size", "0", str(HLS_DIR / "p.m3u8")], check=True)
    lines = [(f"http://127.0.0.1:8765/seg/{l}?X-Amz-Signature=abc" if l.endswith(".ts") else l)
             for l in (HLS_DIR / "p.m3u8").read_text().splitlines()]
    return "\n".join(lines) + "\n"


def first_segment(playlist):
    lines = playlist.splitlines()
    first = next(i for i, l in enumerate(lines) if not l.startswith("#"))
    return "\n".join(lines[:first + 1] + ["#EXT-X-ENDLIST"]) + "\n"


PLAYLIST = ""


def live_grown():
    return CALLS["events"] >= 3


def ev(trace, ts, bird=None, period=20.0):
    e = {"traceId": trace, "timestamp": ts, "serialNumber": "cam0001", "deviceName": "Bird Cam",
         "period": period, "imageUrl": f"http://127.0.0.1:8765/img/{trace}",
         "keyshots": [{"imageUrl": f"http://127.0.0.1:8765/img/{trace}"}],
         "videoUrl": f"http://127.0.0.1:8765/vid/{trace}"}
    if bird:
        e["subcategoryInfoList"] = [{"objectType": "bird", "objectName": bird, "confidence": 0.9}]
    return e


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, body):
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        if self.path == "/account/login":
            CALLS["login"] += 1
            return self._json({"result": 0, "data": {"token": {"token": "tok"}}})
        if self.headers.get("Authorization") != "tok":
            return self._json({"code": -1026, "msg": "token invalid"})
        if self.path == "/device/listuserdevices":
            return self._json({"code": 0, "data": {"list": [
                {"serialNumber": "cam0001", "deviceName": "Bird Cam", "modelNo": "CG625",
                 "ip": "192.168.1.50", "batteryLevel": 96, "signalStrength": -60, "isCharging": 0}]}})
        if self.path == "/library/newselectlibrary":
            CALLS["events"] += 1
            items = [ev("old2", NOW - 3600, "Robin"), ev("old1", NOW - 7200)]  # newest first
            if CALLS["events"] >= 2:
                items.insert(0, ev("live1", NOW - 5, "Blue Jay", 4.0 if live_grown() else 2.0))
            return self._json({"code": 0, "data": {"list": items}})
        self._json({"code": 404, "msg": "nope"})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path.startswith("/vid/"):
            text = PLAYLIST if path != "/vid/live1" or live_grown() else first_segment(PLAYLIST)
            body, ctype = text.encode(), "application/vnd.apple.mpegurl"
        elif path.startswith("/seg/"):
            body, ctype = (HLS_DIR / path[5:]).read_bytes(), "application/octet-stream"
        else:
            body, ctype = JPEG, "image/jpeg"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    global PLAYLIST
    PLAYLIST = make_hls()
    os.makedirs("/homeassistant/www", exist_ok=True)
    Path("/homeassistant/configuration.yaml").write_text("# test\n")
    host = os.environ.get("MQTT_HOST", "127.0.0.1")
    srv = ThreadingHTTPServer(("127.0.0.1", 8765), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    got = []
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    sub.on_message = lambda c, u, m: got.append((m.topic, m.retain, m.payload))
    sub.connect(host, 1883)
    sub.subscribe("#")
    sub.loop_start()

    os.makedirs("/data", exist_ok=True)
    for f in ("/data/state.json", "/data/token.json"):
        if os.path.exists(f):
            os.remove(f)
    with open("/data/options.json", "w") as fh:
        json.dump({"email": "x@example.com", "password": "pw", "api_base_override": "http://127.0.0.1:8765",
                   "poll_interval": 30, "mqtt_host": host, "log_level": "debug"}, fh)

    import bridge
    b = bridge.Bridge(bridge.load_options())
    t = threading.Thread(target=b.run, daemon=True)
    t.start()
    time.sleep(96)  # four polls
    b.stop.set()
    t.join(10)
    time.sleep(1)
    sub.loop_stop()

    topics = {}
    for topic, retain, payload in got:
        topics.setdefault(topic, []).append(payload)
    fails = []

    def check(cond, msg):
        print(("PASS " if cond else "FAIL ") + msg)
        if not cond:
            fails.append(msg)

    disc = [t for t in topics if t.startswith("homeassistant/") and t.endswith("/config")]
    check(len(disc) == 15, f"15 discovery configs ({len(disc)})")
    for d in disc:
        json.loads(topics[d][-1])
    last = json.loads(topics["vicohome_mqtt/cam0001/last"][-1])
    check(last["trace_id"] == "live1" and last["bird"] == "Blue Jay", f"last = newest ({last['trace_id']})")
    evs = [json.loads(p) for p in topics.get("vicohome_mqtt/cam0001/event", [])]
    check([e["trace_id"] for e in evs] == ["live1"], f"only the live event fired ({[e['trace_id'] for e in evs]})")
    check(evs and evs[0]["event_type"] == "bird", "event_type bird")
    check(topics.get("vicohome_mqtt/cam0001/motion") == [b"ON"], "one motion pulse")
    check(topics["vicohome_mqtt/cam0001/image"][-1] == JPEG, "image bytes published")
    today = json.loads(topics["vicohome_mqtt/cam0001/today"][-1])
    check(today["recent"][0]["trace_id"] == "live1" and len(today["recent"]) == 3, "recent list newest first")
    check(len(json.dumps(today)) < 16000, f"today attributes small ({len(json.dumps(today))} bytes)")
    tel = json.loads(topics["vicohome_mqtt/cam0001/telemetry"][-1])
    check(tel["battery"] == 96 and tel["online"] is True, "telemetry")
    check(topics["vicohome_mqtt/status"][-1] == b"offline" and b"online" in topics["vicohome_mqtt/status"],
          "status online, then offline on stop")
    bridge_state = json.loads(topics["vicohome_mqtt/bridge"][-1])
    check(bridge_state["status"] == "ok", "bridge status ok")
    saved = list(Path("/media/vicohome").rglob("*.jpg"))
    check(len(saved) == 3, f"3 images saved to /media ({len(saved)})")
    check(CALLS["login"] == 1, f"logged in once ({CALLS['login']})")
    clips = sorted(p.name for p in Path("/homeassistant/www/vicohome").glob("*.mp4"))
    check(clips == ["live1.mp4", "old1.mp4", "old2.mp4"], f"3 MP4 clips made ({clips})")
    import subprocess
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            "/homeassistant/www/vicohome/live1.mp4"], capture_output=True, text=True)
    check(probe.returncode == 0 and float(probe.stdout or 0) > 3,
          f"clip is a valid MP4 with every segment, not just the first ({probe.stdout.strip()} s)")
    check(last.get("period") == 4.0, f"last has the cloud's newer period ({last.get('period')})")
    check(last.get("clip_url") == "/local/vicohome/live1.mp4", f"last has clip_url ({last.get('clip_url')})")
    check(today["recent"][0].get("clip_url") == "/local/vicohome/live1.mp4", "recent rows have clip_url")
    cs = json.loads(topics["vicohome_mqtt/clips"][-1])
    check(cs["count"] == 3 and cs["limit"] == 60 and cs["size_mb"] >= 0, f"clip storage published ({cs['count']} clips, limit {cs['limit']})")
    from datetime import date, timedelta
    yday = (date.today() - timedelta(days=1)).isoformat()
    check(b.budget.last_audit_day == yday and not b.budget.state["audits"],
          f"first start: yesterday ({yday}) marked, not audited (incomplete day): {b.budget.last_audit_day}")
    check(cs["today_visits"] >= 1, f"today's visits counted ({cs['today_visits']})")
    print("RESULT:", "OK" if not fails else f"{len(fails)} failed")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
