# Scratchpad

My working log for ha-vicohome-bridge, newest first. This branch is a scratch pad for sharing progress
between sessions. It is never merged into `main` or `edge`.

---

## 2026-11-14: Can the bridge run local only?

**What I found.** Not yet. `clip_source: sd_only` already pulls the video off the SD card peer to peer on
my LAN, but the bridge still needs Vicohome's servers for login, the event list (how it learns a visit
happened, plus species), the snapshot, the card check, and the WebRTC ticket and signalling that wake the
camera and set up each SD session. The `api-local.safemo.com` / `127.0.0.1:15000` names in `sdfetch.py`
are the phone app's own proxy, not a service on the camera.

**Plan.**
1. I run LAN probes on the camera (full port scan asleep and awake, mDNS/SSDP, ARP while asleep, a router
   capture across one visit) to see if it has any local service or a clear wake signal.
2. SD polling as a visit source: `visit_source: cloud | sd_poll`, `sd_poll_minutes` 15-1440 (default 60).
   New SD segments become visits, snapshot is a frame from the clip, species left blank. Each poll wakes
   the camera and signs the app out of live view for ~30 s, so battery sets the floor, not the code.
3. Local wake watcher, if the probes show one: poll the card only after the camera is seen waking.
4. Species from un-subscribird instead of the cloud.
5. Local stand-in for the signalling servers, only if the camera doesn't pin TLS certificates.

**Status.** Findings written up; nothing in code yet.

**Next.**
- Run the probes and share redacted output, or open a Remote Control session on my home machine.
- Decide whether to start step 2 now.

---

## 2026-11-14: The "missing" 09:04 clip wasn't missing

**What I found.** In my log, the 09:04 visit got "Removed 1 old clip(s)" and no "Saved clip" after it,
and 09:09:21 saved a clip with no new event before it. No clip was lost. The trace ids carry the visit
time, and they line up one-to-one: the 09:04 visit's clip is the one saved at 09:06:15, the 09:05 visit's
is 09:07:17, the 09:07 visit's is 09:09:21 (it took one more poll to stop growing), and 09:09's is 09:11:25.
A fresh clip is only saved once two fetches a poll apart agree, so its "Saved clip" line lands under the
next visit's lines. "Removed 1 old clip(s)" is the clip limit dropping the oldest file; it never touches
a clip that is still being made.

**What I changed** (branch `dev/clip-log-visit`, PR into `edge`, edge build 0.4.2-dev.1):
- "Saved clip" now ends with "for the visit at HH:MM:SS".
- "Removed N old clip(s)" now says how many clips it keeps.
- A test that retention running between a visit's event and its clip leaves that clip alone, and one for
  the new log line. `pytest tests`: 55 passed. `e2e_fake_cloud.py`: OK.

**Status.** PR #19 into `edge` is open, waiting on CI and my review.

**Next.**
- Review and merge #19, then check the edge add-on's log reads clearly on the next few visits.

---

## 2026-11-14: Draft-only releases and a once-a-day tag limit

**What I found.** `main` and `edge` both exist and sit on the same commit (0.4.1). The README already
explains installing the edge add-on with `#edge`. CI builds amd64 and aarch64, runs the unit and
end-to-end tests, and fails any PR into `edge` that changes the add-on without a new version.
Dependabot targets `edge`. Releases were drafted and published by hand, and nothing stopped two in a day.

**What I changed** (branch `dev/draft-releases`, PR into `edge`):
- New `.github/workflows/release.yml`. A version change landing on `main` drafts a GitHub Release with
  that version's changelog notes. Nothing publishes on its own; I press Publish, which creates the tag.
- One tagged release a day, counted in the time zone set by the `RELEASE_TZ` repository variable
  (UTC when unset; I want America/Toronto). If a `v*` tag already exists that day, the draft says when it can go
  out, and a second tag pushed by hand the same day turns the Release run red.
- Release steps written out in `RELEASES.md`, and its note about which versions are tagged corrected.
- `pytest tests`: 53 passed.

**Status.** In review as PR #20 into `edge`.

**Next.**
- Set the `RELEASE_TZ` repository variable to `America/Toronto`.
- Tag 0.4.1 on another day: v0.3.6 and v0.4.0 were both tagged today.
