# Scratchpad

My working log for ha-vicohome-bridge, newest first. This branch is a scratch pad for sharing progress
between sessions. It is never merged into `main` or `edge`.

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
