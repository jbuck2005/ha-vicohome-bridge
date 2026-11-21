# Changelog

## Unreleased
- Fixed: an SD card clip that took more than about 30 s to arrive was cut off. The camera appears to stop answering
  ICE consent checks while it streams a clip, and the WebRTC library closed the link after six unanswered
  checks (`Consent to send expired`, then `no data`). The link now stays open, and the log says once when
  the camera stops answering. The bridge's own timeouts still end a session that has really died.
- While a clip comes off the card, the log shows its progress every 10 s, e.g. `sdfetch: <file>: 640 of
  1957 kB after 20 s`.
- Tried and reverted: running without host networking (0.4.3-dev.3). Dropping `host_network` would raise
  the security rating from 5 to the top of the scale. On a real camera, the SD card link got through ICE
  over the add-on network's NAT and the card listing came back. But the link went silent as soon as the
  first clip download started, and ICE consent expired about 30 s later. 0.4.3-dev.4 brought host
  networking back, and the same thing happened there, so NAT wasn't the cause (see the consent fix above).
  Running without host networking can be tried again on top of that fix. See "Security rating" in the README.
- A failed SD session names the error's type when the error has no message, instead of logging
  `session failed:` with nothing after it.
- Fixed: SD polling and SD backfill stopped working after about a thousand camera wakes (about ten days with
  `sd_poll` every 15 minutes) until the add-on was restarted. Each wake added another layer around the
  network-interface lookup, until it hit Python's recursion limit.
- Privacy: clips of visits read from the SD card got names that could be guessed (`sd-<camera hash>-<time>`).
  Home Assistant serves `/local/` without a login, so anyone who had seen one link could find the others by
  trying times. New SD visits get an id keyed with a random secret kept in the add-on's `/data`. Clips saved
  under the old names keep them until the clip limit removes them.
- The API token is only sent to the Vicohome API's own hosts over HTTPS. Before, it went to any URL that
  just contained `vicohome.io` or `vicoo.tech`.
- An HTML error page from the cloud (a 502 or 503) is reported as a cloud error. Before, it counted as a
  rejected token, so the bridge logged in again during every outage.
- Downloading a clip from the SD card uses much less CPU: the received data is read once, not again every
  0.3 s.
- A full disk during an SD backfill skips that clip, rather than stopping the rest of the poll.
- The add-on ships its own AppArmor profile, which raises its Home Assistant security rating from 4 to 5.
  The container may now only run Python, ffmpeg and ffprobe, write to `/data`, `/tmp`, `/media` and
  `<config>/www`, and open IP sockets. The rest of the Home Assistant config is no longer readable from
  inside it. Host networking stays, because the SD card link reaches the camera on the LAN. If a
  feature stops working, look for `apparmor="DENIED"` lines with `profile="vicohome_mqtt"` in the host's
  kernel log (Settings > System > Logs > Host).

## 0.4.2
- The add-on now runs on Python 3.15 (was 3.12), and CI tests on 3.15 to match.
- New option **`visit_source`**: `cloud` (default) or **`sd_poll`**. With `sd_poll` the bridge learns about
  visits by reading the camera's microSD card every **`sd_poll_minutes`** (15-1440, default 60) instead of the
  cloud's event list. Each new recording becomes a visit with its clip and a snapshot frame from the card, but
  no species. Each read wakes the camera, so short intervals cost battery. The cloud is still used to log in,
  list devices and open the local link.
- SD backfill gives each recording on the card to one visit only. When two visits fell inside one long
  recording, both got the same file, so one recording showed up as two visits with the same video. The
  recording now goes to the visit that started closest to it, and the other visit keeps its cloud clip.
- Clearer clip log lines. A new visit's clip is saved a poll or two after its event line, so in the log it
  often sits under the next visit's lines and looked like it belonged to that one. `Saved clip` now names
  the visit it is for (`... for the visit at 09:04:00`), and `Removed old clip(s)` says how many clips are
  kept, which shows it is the clip limit dropping the oldest file, not the new visit's clip.

## 0.4.1
- A **Re-fetch clips** button and a **Re-fetch hours** number (default 2, up to 24) as Home Assistant
  entities. Pressing the button, or publishing a number of hours to `<base_topic>/clips/refetch`,
  re-lists the visits of that many hours, compares each saved clip with the cloud's current video and
  makes the file again, under the same name, where the cloud now has more. Only clips still on disk are
  touched, so the clip limit holds. The counts (checked, re-made, unchanged, gone from the cloud) are
  logged and published as `last_refetch` on the Clip storage entity. This is the on-demand companion to
  the hourly repair added in 0.3.6.
- An **edge channel**: changes now merge into the `edge` branch first and reach `main` as a release.
  Home Assistant can install the edge add-on alongside the released one from
  `https://github.com/jbuck2005/ha-vicohome-bridge#edge`; it carries a `-dev.N` version and offers an
  update after each merge. See the README.
- New clip source **`sd_only`**: take every visit's clip from the camera's microSD card rather than the cloud
  (the cloud is still used for the bird events and snapshots). `clip_source` is now `cloud_first` (default),
  `sd_only`, or `cloud_only`.
- New option **`sd_fetch_hours`** (1-24, default 1): how often the camera may be woken to pull clips from its
  card, for both `cloud_first` and `sd_only`. A low value wakes a battery camera often and can shorten its run
  time; a higher value is gentler but makes SD-sourced clips appear later. `cloud_only` never wakes the camera.

## 0.4.0
- Clips the cloud only has part of are backfilled from the camera's microSD card. When a visit's
  cloud video is incomplete (the cloud reports `receivedAllSlice` false) and the camera has a card,
  the bridge pulls the full recording straight off the card over a WebRTC data-channel tunnel on the
  local network, the same path the Vicohome app uses, and replaces the short clip. This recovers
  clips the camera uploaded short because of a poor link. New option `clip_source`: `cloud_first`
  (default) does this backfill; `cloud_only` never touches the card.
- A card fetch wakes the battery camera and briefly signs the phone app out of the account (a camera
  serves one live session at a time), so it only runs for genuinely incomplete clips, at most a few
  per hourly repair, and at most twice per visit.
- The add-on now uses a glibc base image so the WebRTC libraries install from prebuilt wheels instead
  of compiling on the Home Assistant host, and runs on the host network (needed for the peer-to-peer
  link to the camera).
- WebRTC signalling follows the MIT project averagenative/vicolocal; its compact-SDP codec is vendored.

## 0.3.6
- Clips cut short are made again. Once an hour (first 2 minutes after a start) the bridge lists
  the last two days of visits again, compares each kept clip's length with the cloud's video
  length, and makes the MP4 again from a fresh link when the cloud has more, under the same name.
  This repairs clips saved by 0.3.4 and earlier, and clips whose video the cloud finished after
  the 0.3.5 rechecks. A kept visit with no file (after `clips_to_keep` was raised, say) gets one
  too. A clip the cloud never offers more for is tried 3 times, then left alone. The clip's
  length is kept in the state file as `clip_seconds`.
- Clips made for a visit older than 45 minutes are not rechecked afterwards.

## 0.3.5
- Clips are no longer cut short. Since 12 November 2026 the Vicohome cloud lists a visit, with a
  playlist that looks finished, when only the first 2 seconds are uploaded, and adds the rest
  later. The bridge now waits until a new visit's playlist is the same on two polls in a row (and
  as long as the cloud's video length, for at most 5 minutes), so `clip_url` arrives about one poll
  later than before. It then checks the playlist again 2, 5, 15 and 30 minutes after saving and
  makes the MP4 again under the same name if it grew.
- A visit's `period` (video length) in the `last` topic and the state file follows the cloud's
  newer value instead of keeping the one from when the visit was first listed. The `event` topic
  still carries the first value.
## 0.3.4
- Logo: a wifi symbol replaces the dot on the "i" of "-bridge", as on the "i" of un-subscribird's "bird".

## 0.3.3
- The bridge now reports the right version in MQTT discovery and its status (it said 0.3.1 in 0.3.2).
- Option names and descriptions in the add-on's Configuration tab.
- Every option is documented in DOCS.md.
- Icon and logo in the un-subscribird sibling brand (see BRANDING.md).
- Python 3.13 base image; paho-mqtt 2.2.0, requests 2.34.2, tzdata 2026.6.

## 0.3.2
- Removed `init`, `startup` and `boot` from the add-on config. They were set to the
  Supervisor defaults, so the add-on behaves the same.

## 0.3.1
- A poll no longer fails when the Vicohome cloud has just closed the connection: the request is
  sent once more on a new connection. The cloud closes idle connections after less than a minute,
  about as often as the bridge polls, so Cloud status used to show "error" for a minute many times
  a day. Timeouts are still not retried.

## 0.3.0
- Self-sizing clip limit with a daily audit (grow after 2 filled days in a row, shrink after 7
  light days); default minimum 60, maximum 600. New sensors: Clip storage (MB) and Clip limit.
- Clip pruning keeps the newest N across all cameras; history holds up to 1000 visits.
- The state file is only rewritten when something changed (fewer SD-card writes).

## 0.2.0
- Clips: each visit's HLS clip is remuxed to an MP4 in /config/www/vicohome and linked as
  `clip_url` (plays in any browser or app; the raw .m3u8 didn't play in VLC). Newest 30 kept.
- Dashboard: "play clip" uses the MP4; the recent-visits table has a ▶ link per visit.

## 0.1.0
- First release: Python rewrite talking to the Vicohome API directly (no vico-cli/bash),
  new-events-only publishing, image entity, visit event entity, today summary, saved snapshots.
