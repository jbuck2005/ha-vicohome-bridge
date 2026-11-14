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

---

## 0.4.1 — 2026-11-14

Adds a manual complement to the automatic clip recovery, and sets up the edge channel for trying changes before
they are released.

- **Re-fetch clips, on demand.** A **Re-fetch clips** button and a **Re-fetch hours** number (default 2, up to
  24) appear as Home Assistant entities. Pressing the button — or publishing a number of hours to
  `<base_topic>/clips/refetch` from an automation — re-lists the visits of that many hours, compares each clip
  still on disk with the cloud's current video, and remakes the file (same name) wherever the cloud now has more.
  The counts (checked / re-made / unchanged / gone from the cloud) are logged and published as `last_refetch` on
  the Clip storage entity. It is the on-demand companion to the hourly repair added in 0.3.6: the hourly pass
  keeps recent clips whole on its own, and the button recovers a chosen stretch at once.
- **Edge channel.** Changes now merge into `edge` first and reach `main` as a release; the edge add-on can be
  installed alongside the released one to try them early. See the README.

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

Note: releases 0.3.3–0.3.5 are tagged on GitHub; 0.3.6, 0.4.0 and 0.4.1 were tagged when this release log was
added. Releases before 0.3.3 were not tagged.
