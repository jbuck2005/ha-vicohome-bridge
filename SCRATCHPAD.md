# Scratchpad

My working log for ha-vicohome-bridge, newest first. This branch is a scratch pad for sharing progress
between sessions. It is never merged into `main` or `edge`.

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

**Status.** Committed. The push is still refused (403): the Claude GitHub App doesn't cover this repo yet.

**Next.**
- Add this repo to the Claude GitHub App, then push `dev/draft-releases` and open the PR into `edge`.
- Set the `RELEASE_TZ` repository variable to `America/Toronto`.
- Tag 0.4.1 on another day: v0.3.6 and v0.4.0 were both tagged today.
