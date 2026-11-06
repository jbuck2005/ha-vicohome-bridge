"""Vicohome -> MQTT bridge for Home Assistant.

Polls the Vicohome cloud, publishes each NEW event once (oldest first), keeps the newest
event as the camera's "last" state, publishes the snapshot as image bytes, and creates
the Home Assistant entities through MQTT discovery.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
import sys
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import paho.mqtt.client as mqtt
import requests

from clips import ClipBudget, ClipMaker
from tracker import Tracker
from vicohome import AuthError, Device, Event, VicohomeClient, VicohomeError

VERSION = "0.3.0"
LOG = logging.getLogger("vicohome")
OPTIONS_FILE = "/data/options.json"
STATE_FILE = "/data/state.json"
MEDIA_ROOT = Path("/media")
RECENT_IN_ATTRIBUTES = 10
# HA serves <config>/www at /local/. New-style map "homeassistant_config" mounts it at /homeassistant.
HA_CONFIG_DIRS = (Path("/homeassistant"), Path("/config"))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "x"


def load_options() -> dict[str, Any]:
    defaults = {
        "email": "", "password": "", "region": "us", "api_base_override": "",
        "poll_interval": 60, "bootstrap_hours": 24, "base_topic": "vicohome_mqtt",
        "discovery_prefix": "homeassistant", "image_source": "keyshot",
        "save_images": True, "media_folder": "vicohome", "retention_days": 30,
        "motion_seconds": 60, "log_level": "info",
        "save_clips": True, "clips_to_keep": 60, "clips_max": 600, "clip_folder": "vicohome",
        "mqtt_host": "", "mqtt_port": 1883, "mqtt_username": "", "mqtt_password": "",
    }
    try:
        with open(OPTIONS_FILE, encoding="utf-8") as fh:
            defaults.update({k: v for k, v in json.load(fh).items() if v is not None})
    except FileNotFoundError:
        pass
    return defaults


def mqtt_settings(opts: dict[str, Any]) -> dict[str, Any]:
    if opts.get("mqtt_host"):
        return {"host": opts["mqtt_host"], "port": int(opts.get("mqtt_port") or 1883),
                "username": opts.get("mqtt_username") or None,
                "password": opts.get("mqtt_password") or None}
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise SystemExit("No mqtt_host option and no SUPERVISOR_TOKEN: cannot find the MQTT broker")
    resp = requests.get("http://supervisor/services/mqtt", timeout=10,
                        headers={"Authorization": f"Bearer {token}"})
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    return {"host": data["host"], "port": int(data.get("port", 1883)),
            "username": data.get("username"), "password": data.get("password")}


class Bridge:
    def __init__(self, opts: dict[str, Any]) -> None:
        self.opts = opts
        self.base = opts["base_topic"].strip("/")
        self.prefix = opts["discovery_prefix"].strip("/")
        try:
            self.tz = ZoneInfo(os.environ.get("TZ") or "UTC")
        except ZoneInfoNotFoundError:
            self.tz = ZoneInfo("UTC")
        self.status_topic = f"{self.base}/status"
        self.stop = threading.Event()
        self.discovered: set[str] = set()
        self.device_info: dict[str, Device] = {}
        self.last_error = ""
        self.last_ok: float | None = None
        self.tracker = Tracker(STATE_FILE, self.tz)
        self.api = VicohomeClient(opts["email"], opts["password"], opts["region"],
                                  opts.get("api_base_override") or "")
        self.mqtt = self._mqtt_client()
        self._last_cleanup = 0.0
        self.clips = self._clip_maker()
        self.budget = ClipBudget(self.tracker.clip_budget, int(self.opts["clips_to_keep"]),
                                 int(self.opts["clips_max"]))
        self._last_save = 0.0

    # ---- MQTT -----------------------------------------------------------
    def _mqtt_client(self) -> mqtt.Client:
        cfg = mqtt_settings(self.opts)
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"vicohome-mqtt-{os.getpid()}")
        if cfg["username"]:
            client.username_pw_set(cfg["username"], cfg["password"])
        client.will_set(self.status_topic, "offline", qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = lambda *a: LOG.warning("MQTT disconnected, will reconnect")
        client.reconnect_delay_set(1, 60)
        LOG.info("Connecting to MQTT %s:%s", cfg["host"], cfg["port"])
        client.connect_async(cfg["host"], cfg["port"], keepalive=60)
        client.loop_start()
        return client

    def _on_connect(self, client, userdata, flags, reason, properties=None) -> None:
        if reason.is_failure:
            LOG.error("MQTT connect failed: %s", reason)
            return
        LOG.info("MQTT connected")
        client.publish(self.status_topic, "online", qos=1, retain=True)
        client.subscribe(f"{self.prefix}/status")
        client.message_callback_add(f"{self.prefix}/status", self._on_ha_status)
        # Re-send discovery after a reconnect (the broker may have restarted without persistence).
        for serial in list(self.discovered):
            self._publish_discovery(serial)

    def _on_ha_status(self, client, userdata, msg) -> None:
        if msg.payload == b"online":
            LOG.info("Home Assistant came online, re-sending discovery")
            for serial in list(self.discovered):
                self._publish_discovery(serial)

    def pub(self, topic: str, payload: Any, retain: bool = True) -> None:
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload)
        self.mqtt.publish(topic, payload, qos=1, retain=retain)

    # ---- discovery ------------------------------------------------------
    def _device(self, serial: str) -> dict[str, Any]:
        dev = self.device_info.get(serial)
        name = dev.name if dev else (self.tracker.cameras.get(serial) or {}).get("name") or f"Camera {serial}"
        info = {"identifiers": [f"vicohome_mqtt_{serial}"], "name": name,
                "manufacturer": "Vicohome", "model": dev.model if dev else "Camera",
                "sw_version": f"bridge {VERSION}"}
        if dev and dev.mac:
            info["connections"] = [["mac", dev.mac.lower()]]
        return info

    def _publish_discovery(self, serial: str) -> None:
        t = f"{self.base}/{serial}"
        avail = {"availability_topic": self.status_topic}
        dev = self._device(serial)
        uid = f"vicohome_mqtt_{serial}"

        def cfg(component: str, key: str, body: dict[str, Any]) -> None:
            body = {**body, **avail, "unique_id": f"{uid}_{key}", "device": dev}
            body.setdefault("has_entity_name", True)
            body.setdefault("origin", {"name": "ha-vicohome-mqtt", "sw_version": VERSION})
            self.pub(f"{self.prefix}/{component}/{uid}/{key}/config", body)

        cfg("sensor", "last_bird", {
            "name": "Last bird", "icon": "mdi:bird", "state_topic": f"{t}/last",
            "value_template": "{{ value_json.bird }}", "json_attributes_topic": f"{t}/last"})
        cfg("sensor", "last_visit", {
            "name": "Last visit", "device_class": "timestamp", "state_topic": f"{t}/last",
            "value_template": "{{ value_json.time }}"})
        cfg("sensor", "visits_today", {
            "name": "Visits today", "icon": "mdi:counter", "state_topic": f"{t}/today",
            "value_template": "{{ value_json.count }}", "unit_of_measurement": "visits",
            "state_class": "measurement", "json_attributes_topic": f"{t}/today"})
        cfg("image", "snapshot", {
            "name": "Snapshot", "image_topic": f"{t}/image", "content_type": "image/jpeg"})
        cfg("event", "visit", {
            "name": "Visit", "state_topic": f"{t}/event",
            "event_types": ["bird", "unidentified"]})
        cfg("binary_sensor", "motion", {
            "name": "Motion", "device_class": "motion", "state_topic": f"{t}/motion",
            "off_delay": int(self.opts["motion_seconds"])})
        cfg("sensor", "battery", {
            "name": "Battery", "device_class": "battery", "unit_of_measurement": "%",
            "state_class": "measurement", "state_topic": f"{t}/telemetry",
            "value_template": "{{ value_json.battery }}"})
        cfg("sensor", "wifi", {
            "name": "WiFi signal", "device_class": "signal_strength", "unit_of_measurement": "dBm",
            "state_class": "measurement", "entity_category": "diagnostic",
            "state_topic": f"{t}/telemetry", "value_template": "{{ value_json.signal }}"})
        cfg("binary_sensor", "online", {
            "name": "Online", "device_class": "connectivity", "entity_category": "diagnostic",
            "state_topic": f"{t}/telemetry",
            "value_template": "{{ 'ON' if value_json.online else 'OFF' }}"})
        cfg("binary_sensor", "charging", {
            "name": "Charging", "device_class": "battery_charging", "entity_category": "diagnostic",
            "state_topic": f"{t}/telemetry",
            "value_template": "{{ 'ON' if value_json.charging else 'OFF' }}"})
        cfg("sensor", "ip", {
            "name": "IP address", "icon": "mdi:ip-network", "entity_category": "diagnostic",
            "state_topic": f"{t}/telemetry", "value_template": "{{ value_json.ip }}"})
        cfg("sensor", "cloud_status", {
            "name": "Cloud status", "icon": "mdi:cloud-check", "entity_category": "diagnostic",
            "state_topic": f"{self.base}/bridge", "value_template": "{{ value_json.status }}",
            "json_attributes_topic": f"{self.base}/bridge"})
        cfg("sensor", "last_poll", {
            "name": "Last cloud poll", "device_class": "timestamp", "entity_category": "diagnostic",
            "state_topic": f"{self.base}/bridge", "value_template": "{{ value_json.last_ok }}"})
        if self.clips:
            cfg("sensor", "clip_storage", {
                "name": "Clip storage", "icon": "mdi:filmstrip-box-multiple", "unit_of_measurement": "MB",
                "device_class": "data_size", "state_class": "measurement", "entity_category": "diagnostic",
                "state_topic": f"{self.base}/clips", "value_template": "{{ value_json.size_mb }}",
                "json_attributes_topic": f"{self.base}/clips"})
            cfg("sensor", "clip_limit", {
                "name": "Clip limit", "icon": "mdi:arrow-expand-vertical", "unit_of_measurement": "clips",
                "entity_category": "diagnostic", "state_topic": f"{self.base}/clips",
                "value_template": "{{ value_json.limit }}"})
        self.discovered.add(serial)

    def ensure_discovery(self, serial: str) -> None:
        if serial not in self.discovered:
            LOG.info("Publishing Home Assistant discovery for camera %s", serial)
            self._publish_discovery(serial)

    # ---- clips ----------------------------------------------------------
    def _clip_maker(self) -> ClipMaker | None:
        if not self.opts["save_clips"]:
            return None
        root = next((d for d in HA_CONFIG_DIRS if (d / "configuration.yaml").exists()), None)
        if root is None:
            LOG.warning("Home Assistant config folder not mapped; clips disabled")
            return None
        sub = slug(self.opts["clip_folder"])
        return ClipMaker(root / "www" / sub, f"/local/{sub}", self.api.fetch_text)

    def handle_clips(self) -> None:
        if not self.clips:
            return
        changed = set()
        for serial, trace_id, url in self.clips.process():
            self.tracker.set_field(serial, trace_id, "clip_url", url)
            changed.add(serial)
        keep = [r["trace_id"] for r in self.tracker.newest(self.budget.cap)]
        removed = self.clips.prune(keep)
        if removed:
            LOG.info("Removed %d old clip(s)", removed)
            keep_set = set(keep)
            for cam in self.tracker.cameras.values():
                for rec in cam.get("history", []):
                    if rec.get("clip_url") and rec["trace_id"] not in keep_set:
                        rec.pop("clip_url", None)
        if changed:
            now = time.time()
            for serial in changed:
                self.publish_camera_state(serial, now)
        if changed or removed:
            self.publish_clip_storage()

    def clip_audit(self, now: float) -> None:
        """Once per day, after midnight: size the clip limit from the previous complete day(s)."""
        if not self.clips:
            return
        yesterday = datetime.fromtimestamp(now, self.tz).date() - timedelta(days=1)
        last = self.budget.last_audit_day
        first = date.fromisoformat(last) + timedelta(days=1) if last else yesterday
        if first > yesterday:
            return
        days = [(first + timedelta(days=i)).isoformat() for i in range((yesterday - first).days + 1)][-14:]
        free = shutil.disk_usage(self.clips.folder.parent).free if self.clips.folder.parent.exists() else None
        for day in days:
            if self.tracker.day_complete(day):
                self.budget.audit(day, self.tracker.daily.get(day, 0), free)
            else:
                self.budget.state["last_audit_day"] = day
                LOG.info("Clip audit %s skipped: the bridge did not see the whole day", day)
        self.tracker.dirty = True
        self.publish_clip_storage()

    def publish_clip_storage(self) -> None:
        if not self.clips:
            return
        files = list(self.clips.folder.glob("*.mp4")) if self.clips.folder.is_dir() else []
        size = sum(f.stat().st_size for f in files)
        try:
            free = shutil.disk_usage(self.clips.folder.parent).free
        except OSError:
            free = None
        st = self.budget.state
        self.pub(f"{self.base}/clips", {
            "size_mb": round(size / 1024 ** 2, 1), "count": len(files), "limit": self.budget.cap,
            "minimum": self.budget.minimum, "maximum": self.budget.maximum,
            "full_streak": st.get("full_streak", 0), "light_streak": st.get("light_streak", 0),
            "today_visits": self.tracker.daily.get(self.tracker.day_of(time.time()), 0),
            "free_gb": round(free / 1024 ** 3, 1) if free is not None else None,
            "last_audit": (st.get("audits") or [None])[-1], "audits": (st.get("audits") or [])[-14:]})

    # ---- images ---------------------------------------------------------
    def _fetch_image(self, rec: dict[str, Any]) -> tuple[bytes, str] | None:
        urls = [rec.get("keyshot_url"), rec.get("image_url")]
        if self.opts["image_source"] == "image":
            urls.reverse()
        for url in urls:
            if url:
                got = self.api.download(url)
                if got:
                    return got
        return None

    def _save_image(self, rec: dict[str, Any], data: bytes, ctype: str) -> str | None:
        if not self.opts["save_images"]:
            return None
        when = datetime.fromtimestamp(rec["timestamp"], self.tz)
        ext = ".png" if ctype == "image/png" else ".jpg"
        cam = slug((self.tracker.cameras.get(rec["serial"]) or {}).get("name") or rec["serial"])
        rel = Path(self.opts["media_folder"]) / cam / when.strftime("%Y-%m-%d") / \
            f"{when.strftime('%H%M%S')}_{slug(rec['bird'])}{ext}"
        path = MEDIA_ROOT / rel
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        except OSError as err:
            LOG.warning("Could not save image to %s: %s", path, err)
            return None
        return str(rel)

    def handle_image(self, rec: dict[str, Any], publish: bool) -> None:
        got = self._fetch_image(rec)
        if not got:
            return
        data, ctype = got
        rel = self._save_image(rec, data, ctype)
        if rel:
            self.tracker.set_field(rec["serial"], rec["trace_id"], "saved_image", rel)
        if publish:
            self.mqtt.publish(f"{self.base}/{rec['serial']}/image", data, qos=1, retain=True)

    def cleanup_media(self, now: float) -> None:
        days = int(self.opts["retention_days"])
        if not self.opts["save_images"] or days <= 0 or now - self._last_cleanup < 6 * 3600:
            return
        self._last_cleanup = now
        root = MEDIA_ROOT / self.opts["media_folder"]
        cutoff = datetime.fromtimestamp(now - days * 86400, self.tz).strftime("%Y-%m-%d")
        removed = 0
        for day_dir in root.glob("*/????-??-??"):
            if day_dir.is_dir() and day_dir.name < cutoff:
                for f in day_dir.iterdir():
                    f.unlink(missing_ok=True)
                    removed += 1
                day_dir.rmdir()
        if removed:
            LOG.info("Removed %d saved images older than %d days", removed, days)

    # ---- publishing -----------------------------------------------------
    def publish_telemetry(self, dev: Device) -> None:
        self.pub(f"{self.base}/{dev.serial}/telemetry", {
            "battery": dev.battery, "signal": dev.signal, "online": bool(dev.ip),
            "charging": dev.charging, "ip": dev.ip, "wifi_channel": dev.wifi_channel,
            "location": dev.location, "network": dev.network})

    def publish_camera_state(self, serial: str, now: float) -> None:
        last = self.tracker.last(serial)
        if last:
            self.pub(f"{self.base}/{serial}/last", last)
        today = self.tracker.today(serial, now)
        # Keep attributes small (HA's recorder skips attributes over 16 kB): no cloud URLs here.
        today["recent"] = [
            {k: r.get(k) for k in ("time", "bird", "latin", "confidence", "saved_image", "clip_url", "trace_id")}
            for r in self.tracker.recent(serial, RECENT_IN_ATTRIBUTES)]
        self.pub(f"{self.base}/{serial}/today", today)

    def publish_live_event(self, rec: dict[str, Any]) -> None:
        t = f"{self.base}/{rec['serial']}"
        self.pub(f"{t}/event", {
            "event_type": "bird" if rec["identified"] else "unidentified",
            **{k: rec.get(k) for k in ("bird", "latin", "confidence", "time", "trace_id",
                                        "period", "saved_image")}}, retain=False)
        self.pub(f"{t}/motion", "ON", retain=False)

    def publish_bridge(self, status: str) -> None:
        self.pub(f"{self.base}/bridge", {
            "status": status, "last_error": self.last_error, "version": VERSION,
            "last_ok": datetime.fromtimestamp(self.last_ok, self.tz).isoformat(timespec="seconds")
            if self.last_ok else None,
            "cameras": len(self.discovered)})

    # ---- main loop ------------------------------------------------------
    def poll(self) -> None:
        now = time.time()
        for dev in self.api.devices():
            self.device_info[dev.serial] = dev
            self.ensure_discovery(dev.serial)
            self.publish_telemetry(dev)

        bootstrap = not self.tracker.bootstrapped
        start = self.tracker.window_start(now, float(self.opts["bootstrap_hours"]))
        events = self.api.events(start, now)
        new: list[Event] = self.tracker.ingest(events)
        if new:
            LOG.info("%d new event(s)%s", len(new), " (initial history load)" if bootstrap else "")

        newest: dict[str, dict[str, Any]] = {}
        for ev in new:
            self.ensure_discovery(ev.serial)
            rec = next(r for r in self.tracker.recent(ev.serial, 500) if r["trace_id"] == ev.trace_id)
            newest[ev.serial] = rec
            if bootstrap:
                self.handle_image(rec, publish=False)
            else:
                LOG.info("%s: %s%s at %s", ev.camera_name, ev.bird,
                         f" ({ev.confidence:.0f}%)" if ev.confidence else "", rec["time"])
                self.handle_image(rec, publish=True)
                self.publish_live_event(rec)
                if self.clips:
                    self.clips.queue(ev.serial, ev.trace_id, rec.get("video_url", ""))  # made at the end of the poll
        if bootstrap:
            for serial, rec in newest.items():  # show the newest picture once, no event/motion
                self.handle_image(rec, publish=True)
        if self.clips:  # make sure the recent visits have clips (first start, or after an upgrade)
            for serial in self.tracker.cameras:
                for rec in self.tracker.recent(serial, min(RECENT_IN_ATTRIBUTES, self.budget.cap)):
                    if not rec.get("clip_url"):
                        url = self.clips.queue(serial, rec["trace_id"], rec.get("video_url", ""))
                        if url:
                            self.tracker.set_field(serial, rec["trace_id"], "clip_url", url)

        for serial in set(self.tracker.cameras) | set(self.device_info):
            self.ensure_discovery(serial)
            self.publish_camera_state(serial, now)

        self.handle_clips()
        self.clip_audit(now)
        self.tracker.last_poll = now
        self.tracker.prune(now)
        # Only write the state file when something changed (it sits on the HA box's SD card).
        if self.tracker.dirty or now - self._last_save > 3600:
            self.tracker.save()
            self._last_save = now
        self.cleanup_media(now)

    def run(self) -> None:
        interval = max(30, int(self.opts["poll_interval"]))
        LOG.info("Vicohome MQTT bridge %s: polling every %ss, base topic %s", VERSION, interval, self.base)
        # Re-publish what we already know so entities come back right away after a restart.
        for serial in self.tracker.cameras:
            self.ensure_discovery(serial)
            self.publish_camera_state(serial, time.time())
        self.publish_clip_storage()
        failures = 0
        while not self.stop.is_set():
            wait = interval
            try:
                self.poll()
                failures = 0
                self.last_error = ""
                self.last_ok = time.time()
                self.publish_bridge("ok")
            except AuthError as err:
                failures += 1
                self.last_error = str(err)
                LOG.error("Vicohome login problem: %s", err)
                self.publish_bridge("login error")
                wait = max(interval, 900)  # don't hammer the login endpoint
            except (VicohomeError, requests.RequestException, ValueError) as err:
                failures += 1
                self.last_error = str(err)
                LOG.warning("Poll failed (%d in a row): %s", failures, err)
                self.publish_bridge("error")
                wait = min(interval * (2 ** min(failures - 1, 4)), 1800)
            except Exception:  # keep the bridge alive; log the bug
                failures += 1
                LOG.exception("Unexpected error during poll")
                self.last_error = "internal error (see add-on log)"
                self.publish_bridge("error")
            self.stop.wait(wait)
        LOG.info("Stopping")
        try:
            self.mqtt.publish(self.status_topic, "offline", qos=1, retain=True).wait_for_publish(5)
        except (RuntimeError, ValueError):
            pass
        self.mqtt.loop_stop()
        self.mqtt.disconnect()


def main() -> None:
    opts = load_options()
    logging.basicConfig(level=getattr(logging, str(opts["log_level"]).upper(), logging.INFO),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        datefmt="%Y-%m-%d %H:%M:%S", stream=sys.stdout)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    if not opts["email"] or not opts["password"]:
        LOG.error("Set 'email' and 'password' in the add-on configuration")
        sys.exit(1)
    bridge = Bridge(opts)
    signal.signal(signal.SIGTERM, lambda *a: bridge.stop.set())
    signal.signal(signal.SIGINT, lambda *a: bridge.stop.set())
    bridge.run()


if __name__ == "__main__":
    main()
