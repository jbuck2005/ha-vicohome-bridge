# Release log

A narrative history of the Vicohome MQTT Bridge add-on. Each release is the code tagged `v<version>` on
`main`; the short, bullet form of every change is in [`vicohome_mqtt/CHANGELOG.md`](vicohome_mqtt/CHANGELOG.md),
and this file is the readable story behind each version.

How versions move (since 0.4.1):

- Changes merge into the `edge` branch first. Home Assistant can install the **edge add-on** alongside the
  released one from `https://github.com/jbuck2005/ha-vicohome-bridge#edge`; it carries a `-dev.N` version and
  offers an update after each merge, so new work can be tried before it is released.
- A release promotes `edge` into `main`: the version loses its `-dev.N` suffix and the changelog's pending notes
  are renamed to the version. The add-on store (and HA's update button) then offers the new release.
- Merging the release into `main` drafts a GitHub Release for `v<version>` with that version's changelog notes
  (`.github/workflows/release.yml`). Nothing is published automatically: the maintainer reviews the draft and
  presses **Publish**, which creates the tag.
- At most one release a day, counted in the time zone of the `RELEASE_TZ` repository variable (UTC when unset).
  A release's day is when it was published (or an annotated tag's tagger date), not the tag's git date. If a
  release was already made that day, the draft says when it may be published, and a second release or hand-pushed
  tag on the same day fails the Release workflow.

Releasing, step by step:

1. Open a PR from `edge` into `main` that sets the plain version (`0.4.2`) in `vicohome_mqtt/config.yaml` and
   `VERSION` in `bridge.py`, renames `## Unreleased` in the changelog to it, and adds a section here.
2. Merge it. The Release workflow drafts `v0.4.2`.
3. Check the draft under Releases and publish it (on another day if it says to hold).
4. On `edge`, the next change starts the following edge build (`0.4.3-dev.1`).

---

## 0.4.3 — 2026-11-21

This release gets SD card clips working without host networking, which gives the add-on Home Assistant's top
security rating, and fixes the problems found in a review of the code.

**No host networking, and SD clips that arrive.** The add-on now runs on Home Assistant's add-on network, opens no
ports and ships its own AppArmor profile. Pulling clips off the camera's card took a long chase on a real camera.
The camera only answers the download address it gives in its own card listing, not a path the bridge builds.
And when our session joins, the camera signs the phone app out and sends a "peer left" message, which the bridge
took as the camera ending the session. That cut every request short and gave empty card listings. With both
fixed, real cameras hand over full clips through the add-on network's NAT.

**Sturdier card transfers.** A clip that stalls part-way is asked for again from the byte it stopped at. A
recording the camera won't serve no longer holds up the rest of the batch: the bridge finishes the others in a
fresh session and tries that file last. Large clips are no longer cut off after about 30 s.

**Test SD fetch.** A new button under each camera's Diagnostic entities (or MQTT `<base>/clips/sd_test`) pulls the
newest clips from the card on demand and logs how it went, so the card link can be checked without waiting for a
visit.

**Troubleshooting options.** `sd_rx_window_kb`, `sd_nat_candidate` and `sd_send_audio` show in the Configuration
tab with their defaults. The SD log reports link statistics, progress every 10 s, and why a card listing came
back empty.

**Review fixes.** Clips read from the card no longer get guessable names. The API token is only sent to the
Vicohome API's own hosts over HTTPS. SD polling no longer stops after about a thousand camera wakes. Card
downloads use much less CPU. A full disk skips one clip rather than the rest of the poll. A cloud error page
counts as a cloud error.

## 0.4.2 — 2026-11-15

This release lets the camera's microSD card stand in for the cloud's visit list, and tidies up how card
recordings and clip logs line up with visits.

**Visits from the SD card.** A new option, `visit_source`, is `cloud` by default. Set to **`sd_poll`**, the
bridge learns about visits by reading the camera's card every **`sd_poll_minutes`** (15-1440, default 60) instead
of the cloud's event list. Each new recording becomes a visit with its clip and a snapshot frame from the card,
but no species name. Every read wakes the camera, so a short interval costs battery. The cloud is still used to
log in, list devices and open the local link.

**One card recording per visit.** When two visits fell inside one long recording, both were given the same file,
so one recording showed up as two visits with the same video. The recording now goes to the visit that started
closest to it, and the other visit keeps its cloud clip.

**Clearer clip logs.** `Saved clip` now names the visit it belongs to, and `Removed old clip(s)` says how many
clips are kept, so the clip limit dropping the oldest file no longer looks like a lost new clip.

**Python 3.15.** The add-on image moves from Python 3.12 to 3.15, and CI tests on 3.15 to match.

## 0.4.1 — 2026-11-14

Today's release bundles three things: a manual complement to the automatic clip recovery, the edge channel for trying changes before release, and the SD card as a first-class clip source.

**Re-fetch clips, on demand.** A **Re-fetch clips** button and a **Re-fetch hours** number (default 2, up to 24) as Home Assistant entities. Pressing the button — or publishing a number of hours to `<base_topic>/clips/refetch` — re-lists the visits of that many hours, compares each clip still on disk with the cloud's current video, and remakes the file (same name) wherever the cloud now has more. The counts are logged and published as `last_refetch` on the Clip storage entity. It is the on-demand companion to the hourly repair added in 0.3.6.

**SD card as a clip source.** `clip_source` now has three settings: `cloud_first` (default — cloud, with the card as a fallback for incomplete clips), **`sd_only`** (every clip comes from the card; the cloud is still used for events and snapshots), and `cloud_only` (never touch the card). New **`sd_fetch_hours`** (1–24, default 1) sets how often the camera may be woken to pull SD clips: each wake costs battery, so a low value wakes a battery camera often and can shorten its run time, while a higher value is gentler but makes SD-sourced clips appear later.

**Edge channel.** Changes now merge into `edge` first and reach `main` as a release; the edge add-on can be installed alongside the released one from `https://github.com/jbuck2005/ha-vicohome-bridge#edge`.

## 0.4.0 — 2026-11-13

**Backfill from the camera's microSD card.** When a visit's cloud video is incomplete (the cloud reports
`receivedAllSlice` false) and the camera has a card, the bridge pulls the full recording straight off the card
over a WebRTC data-channel tunnel on the local network — the same path the Vicohome app uses — and replaces the
short clip. This recovers clips the camera uploaded short because of a poor link. A new option `clip_source`
(`cloud_first` by default; `cloud_only` never touches the card) controls it. Because a card fetch wakes the
battery camera and briefly signs the phone app out (a camera serves one live session at a time), it runs only for
genuinely incomplete clips, at most a few per hourly repair and at most twice per visit. The add-on moved to a
glibc base image (so the WebRTC libraries install from prebuilt wheels instead of compiling on the HA host) and
runs on the host network for the peer-to-peer link. WebRTC signalling follows the MIT project
averagenative/vicolocal, whose compact-SDP codec is vendored.

## 0.3.6 — 2026-11-13

**Clips cut short are made again.** Once an hour (in the first two minutes after a start) the bridge re-lists the
last two days of visits, compares each kept clip's length with the cloud's video, and remakes the MP4 from a
fresh link when the cloud has more — repairing clips saved by 0.3.4 and earlier, and clips the cloud finished
after the 0.3.5 rechecks. A kept visit with no file on disk gets one too. A clip the cloud never offers more for
is tried three times, then left alone. Each clip's length is kept in the state file as `clip_seconds`.

## 0.3.5 — 2026-11-12

**No more two-second clips.** From 12 November 2026 the Vicohome cloud began listing a visit with a
finished-looking playlist while only the first two seconds were uploaded. The bridge now waits until a new
visit's playlist is stable across two polls (and at least as long as the cloud's stated length, for up to five
minutes) before saving, then re-checks at 2, 5, 15 and 30 minutes and remakes the clip if it grew. A visit's
`period` follows the cloud's newer value.

## 0.3.4 — 2026-11-09

Branding: a wifi symbol replaces the dot on the "i" of "-bridge".

## 0.3.3 — 2026-11-09

The bridge reports the correct version in MQTT discovery and its status; option names and descriptions tidied;
every option documented in DOCS.md.

## 0.3.2 and earlier — 2026-11

Early releases: the MQTT bridge itself, camera discovery and entities, clip saving to `/config/www`, the daily
budget/audit, and connection-retry fixes. See [`vicohome_mqtt/CHANGELOG.md`](vicohome_mqtt/CHANGELOG.md) for the
full list (0.1.0 through 0.3.2).

---

Note: releases 0.3.3–0.3.5 are tagged on GitHub; 0.3.6 and 0.4.0 were tagged when this release log was added.
0.4.1 shipped from `main` without a tag. Releases before 0.3.3 were not tagged.
