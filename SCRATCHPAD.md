# Scratchpad

My working log for ha-vicohome-bridge, newest first. This branch is a scratch pad for sharing progress
between sessions. It is never merged into `main` or `edge`.

---

## 2026-11-14: Daily release check fixed for lightweight tags

**What I found.** My Zeus review spotted that PR #20's one-a-day check read tag creation dates. When I
press Publish, GitHub makes a lightweight tag, and git dates that by its commit, which can be days older
than the release. The check could miss a second release on the same day, or hold one that was fine.

**What I changed** (PR #20, `dev/draft-releases`):
- A release's day now comes from its publish time in GitHub's release list, or an annotated tag's
  tagger date, counted in `RELEASE_TZ`. The logic is in `.github/scripts/release_day.py`, with tests in
  `tests/test_release_day.py`, including the lightweight-tag case.
- The check also runs when I publish a release, not only on a tag push.
- `pytest tests`: 61 passed.

**Status.** PR #20 merged into `edge`. `RELEASE_TZ` is set to America/Toronto, and the ruleset now protects `main` and `edge`.

**Next.**
- The draft workflow starts working with the next release into `main`. 0.4.1 still needs its tag, on another day.

---

## 2026-11-14: Local checkout on my PC checked

**What I found**
- My local clone is on `main`, level with `origin/main` (0.4.1). Tracked files look untouched since my pull.
- No local `edge` branch yet; `origin/edge` is one PR ahead of `main` (#19).
- Two old desktop worktrees under `.claude/worktrees/` and their local branches are already merged.

**Status:** Nothing lost. Cleanup is optional.

**Next**
- Make a local `edge` that tracks `origin/edge` and work from it.

---

## 2026-11-14: Local-only work paused until the LAN probe runs at home

**Where it stands.** I'm sticking with the camera's stock firmware; the modified-firmware route is dropped.
The probe script (`cam_probe.sh`, kept in my project files, not this repo) scans the camera asleep and
awake, records its broadcasts, and walks me through trying the app with the internet blocked. It prints a
summary with the camera's IP and MAC removed. PR #21 (`visit_source: sd_poll`) is green and open for my review.

**Status.** Paused until I set up the probe on my Docker host.

**Next.**
- Run `sudo ./cam_probe.sh <camera-ip>` on the Docker host and paste the summary in the thread.
- If my phone allows it, capture the app's traffic with PCAPdroid while the internet is blocked.
- Review and merge #21 into `edge`, then try `sd_poll` and watch the battery.

---

## 2026-11-14: Visits from the SD card (sd_poll), and local-only leads

**What I changed** (branch `dev/sd-poll-visits`, PR #21 into `edge`, edge build 0.4.2-dev.2):
- New `visit_source: cloud | sd_poll` and `sd_poll_minutes` (15-1440, default 60). With `sd_poll` the bridge
  reads the camera's SD card on that interval and each new recording is a visit, with its clip and a
  snapshot frame from the card, no species. Clips always come from the card in this mode.
- Recordings under 90 s old wait for the next read; windows overlap by 15 min; a failed read is retried
  next time; a restart doesn't wake the camera early. Cloud-listed visits aren't counted twice.
- `pytest tests`: 64 passed. `e2e_fake_cloud.py`: OK.

**What I found (reverse engineering).** Stock cameras show no open ports and no LAN signalling in what
others have published. The only local method reported is modified vendor firmware (encrypted `.fw`
update zip opened with bkcrack, SD-card bootstrap, then root SSH, local RTSP and a local events API).
It's unpublished and has no licence, so I'd have to redo the steps myself. UART pads (LOG_TX/LOG_RX) are
reported on the board.

**Status.** PR #21 waiting on CI and my review. Reverse engineering waiting on LAN probes from me.

**Next.**
- Review #21; try `sd_poll` on the edge add-on and watch battery for a few days.
- Run the LAN probes (in the findings doc), or open a Remote Control session at home.
- Capture one firmware update download to get the `.fw` file.

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

**Status.** Merged into `edge` (#19). CI was green.

**Next.**
- Check the edge add-on's log reads clearly on the next few visits.

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
