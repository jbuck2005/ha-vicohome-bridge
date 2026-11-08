"""Small client for the Vicohome cloud API (the same endpoints the vico-cli tool uses).

Nothing in here logs the password or the token.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

LOG = logging.getLogger("vicohome.api")

API_BASES = {
    "us": "https://api-us.vicohome.io",
    "eu": "https://api-eu.vicoo.tech",
}

# Error codes the API uses when the session is no longer valid.
AUTH_ERROR_CODES = {-1024, -1025, -1026, -1027}


class VicohomeError(Exception):
    """API call failed."""


class AuthError(VicohomeError):
    """Login failed or the token was rejected."""


@dataclass
class Event:
    trace_id: str
    serial: str
    camera_name: str
    timestamp: float  # unix seconds
    bird: str
    latin: str
    confidence: float | None  # 0-100, None if the API gave nothing
    period: float | None
    image_url: str
    keyshot_url: str
    video_url: str
    raw_keys: list[str] = field(default_factory=list)

    @property
    def identified(self) -> bool:
        return self.bird.lower() not in ("", "unidentified", "unknown")


@dataclass
class Device:
    serial: str
    name: str
    model: str
    ip: str
    battery: int | None
    signal: int | None
    charging: bool | None
    wifi_channel: int | None
    location: str
    network: str
    mac: str


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def parse_timestamp(value: Any) -> float | None:
    """The API sends unix seconds; accept milliseconds and numeric strings too."""
    ts = _num(value)
    if ts is None:
        return None
    if ts > 1e12:  # milliseconds
        ts /= 1000.0
    return ts


def normalise_confidence(value: Any) -> float | None:
    conf = _num(value)
    if conf is None:
        return None
    if 0 < conf <= 1:
        conf *= 100.0
    return round(conf, 1)


def parse_event(raw: dict[str, Any]) -> Event | None:
    trace_id = raw.get("traceId")
    serial = raw.get("serialNumber")
    ts = parse_timestamp(raw.get("timestamp"))
    if not trace_id or not serial or ts is None:
        return None

    bird, latin, confidence = "Unidentified", "", None
    for info in raw.get("subcategoryInfoList") or []:
        if isinstance(info, dict) and info.get("objectType") == "bird":
            bird = info.get("objectName") or bird
            latin = info.get("birdStdName") or ""
            confidence = normalise_confidence(info.get("confidence"))
            break

    keyshot_url = ""
    for shot in raw.get("keyshots") or []:
        if isinstance(shot, dict) and shot.get("imageUrl"):
            keyshot_url = shot["imageUrl"]
            break

    return Event(
        trace_id=str(trace_id),
        serial=str(serial),
        camera_name=raw.get("deviceName") or f"Camera {serial}",
        timestamp=ts,
        bird=bird,
        latin=latin,
        confidence=confidence,
        period=_num(raw.get("period")),
        image_url=raw.get("imageUrl") or "",
        keyshot_url=keyshot_url,
        video_url=raw.get("videoUrl") or "",
        raw_keys=sorted(raw.keys()),
    )


def parse_device(raw: dict[str, Any]) -> Device | None:
    serial = raw.get("serialNumber")
    if not serial:
        return None

    def as_int(key: str) -> int | None:
        val = _num(raw.get(key))
        return None if val is None else int(val)

    charging = raw.get("isCharging")
    return Device(
        serial=str(serial),
        name=raw.get("deviceName") or f"Camera {serial}",
        model=raw.get("modelNo") or "Camera",
        ip=raw.get("ip") or "",
        battery=as_int("batteryLevel"),
        signal=as_int("signalStrength"),
        charging=None if charging is None else bool(_num(charging)),
        wifi_channel=as_int("wifiChannel"),
        location=raw.get("locationName") or "",
        network=raw.get("networkName") or "",
        mac=raw.get("macAddress") or "",
    )


class VicohomeClient:
    def __init__(self, email: str, password: str, region: str = "us",
                 api_base: str = "", token_file: str = "/data/token.json",
                 timeout: float = 20.0) -> None:
        self._email = email
        self._password = password
        self.base = (api_base or API_BASES.get(region, API_BASES["us"])).rstrip("/")
        self.country = "EU" if ("-eu" in self.base or "vicoo.tech" in self.base) else "US"
        self._token_file = token_file
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json",
                                      "Content-Type": "application/json",
                                      "User-Agent": "ha-vicohome-mqtt"})
        self._token = self._load_token()

    # ---- token handling -------------------------------------------------
    def _load_token(self) -> str:
        try:
            with open(self._token_file, encoding="utf-8") as fh:
                data = json.load(fh)
            if data.get("email") == self._email and data.get("expires", 0) > time.time():
                return data.get("token", "")
        except (OSError, ValueError):
            pass
        return ""

    def _save_token(self) -> None:
        try:
            tmp = self._token_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"email": self._email, "token": self._token,
                           "expires": time.time() + 20 * 3600}, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._token_file)
        except OSError as err:
            LOG.warning("Could not cache the API token: %s", err)

    def _post_http(self, url: str, **kwargs: Any) -> requests.Response:
        """POST, retrying once on a fresh connection if the server dropped the old one.

        The API closes idle keep-alive connections after less than a minute, about as
        often as the bridge polls, so now and then a request goes out on a connection
        the server is closing and fails with RemoteDisconnected. The calls are reads
        or a login, so sending one again is safe. Timeouts aren't retried, so a cloud
        that is really down doesn't double the wait.
        """
        try:
            return self._session.post(url, timeout=self._timeout, **kwargs)
        except requests.ConnectionError as err:
            if isinstance(err, requests.Timeout):
                raise
            LOG.debug("Connection dropped (%s), retrying once", err.__class__.__name__)
            return self._session.post(url, timeout=self._timeout, **kwargs)

    def login(self) -> None:
        LOG.info("Logging in to Vicohome (%s)", self.base)
        try:
            resp = self._post_http(f"{self.base}/account/login",
                                   json={"email": self._email, "password": self._password,
                                         "loginType": 0})
            body = resp.json()
        except (requests.RequestException, ValueError) as err:
            raise VicohomeError(f"login request failed: {err.__class__.__name__}") from None
        if body.get("result", 0) != 0:
            raise AuthError(f"login refused: {body.get('msg', '')} (code {body.get('result')})")
        token = ((body.get("data") or {}).get("token") or {}).get("token")
        if not token:
            raise AuthError("login response had no token")
        self._token = token
        self._save_token()

    # ---- requests -------------------------------------------------------
    def _post_once(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._token:
            self.login()
        try:
            resp = self._post_http(f"{self.base}{path}", json=payload,
                                   headers={"Authorization": self._token})
        except requests.RequestException as err:
            raise VicohomeError(f"{path}: {err.__class__.__name__}") from None
        text = resp.text.lstrip()
        if not text.startswith("{"):
            raise AuthError(f"{path}: non-JSON response (HTTP {resp.status_code})")
        body = resp.json()
        code = body.get("code", body.get("result", 0))
        if code not in (0, None):
            msg = str(body.get("msg", ""))
            if code in AUTH_ERROR_CODES or any(w in msg.lower() for w in ("token", "login", "auth")):
                raise AuthError(f"{path}: {msg} (code {code})")
            raise VicohomeError(f"{path}: {msg} (code {code})")
        return body

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return self._post_once(path, payload)
        except AuthError:
            LOG.info("Token rejected, logging in again")
            self._token = ""
            return self._post_once(path, payload)

    def events(self, start: float, end: float) -> list[Event]:
        body = self._post("/library/newselectlibrary", {
            "startTimestamp": str(int(start)), "endTimestamp": str(int(end)),
            "language": "en", "countryNo": self.country})
        items = ((body.get("data") or {}).get("list")) or []
        events = [e for e in (parse_event(i) for i in items if isinstance(i, dict)) if e]
        if len(items) != len(events):
            LOG.debug("Skipped %d unparseable events", len(items) - len(events))
        return events

    def devices(self) -> list[Device]:
        body = self._post("/device/listuserdevices", {"language": "en", "countryNo": self.country})
        items = ((body.get("data") or {}).get("list")) or []
        return [d for d in (parse_device(i) for i in items if isinstance(i, dict)) if d]

    def fetch_text(self, url: str, max_bytes: int = 1_000_000) -> str | None:
        """Fetch a small text resource such as an HLS playlist (vicohome.io URLs get the token)."""
        headers = {"Authorization": self._token} if "vicohome.io" in url or "vicoo.tech" in url else {}
        try:
            resp = self._session.get(url, timeout=self._timeout, headers=headers)
        except requests.RequestException as err:
            LOG.debug("Fetch failed: %s", err.__class__.__name__)
            return None
        if resp.status_code != 200 or len(resp.content) > max_bytes:
            LOG.debug("Fetch failed: HTTP %s", resp.status_code)
            return None
        return resp.text

    def download(self, url: str, max_bytes: int = 8_000_000) -> tuple[bytes, str] | None:
        """Fetch an image. Signed cloud URLs need no auth; vicohome.io ones get the token."""
        headers = {"Authorization": self._token} if "vicohome.io" in url or "vicoo.tech" in url else {}
        try:
            resp = self._session.get(url, timeout=self._timeout, headers=headers, stream=True)
            if resp.status_code != 200:
                LOG.warning("Image download failed: HTTP %s", resp.status_code)
                return None
            data = resp.raw.read(max_bytes + 1, decode_content=True)
        except requests.RequestException as err:
            LOG.warning("Image download failed: %s", err.__class__.__name__)
            return None
        if len(data) > max_bytes:
            LOG.warning("Image larger than %d bytes, skipped", max_bytes)
            return None
        ctype = resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        if not ctype.startswith("image/"):
            ctype = "image/jpeg"
        return data, ctype
