# Changelog

## Unreleased
- A PEER_OUT no longer ends the SD session at all (0.4.3-dev.21). On real hardware dev.20 still ended a
  session on one that named the camera, 4 s before the link came up, and the link then came up fine; the
  listing then gave up at once, as in dev.16 to dev.19. The session now ends when the camera closes the
  data channel, ICE fails, or a request stalls. The log names a PEER_OUT's fields (never their values)
  and which of them hold the camera's serial.
- Fix: another viewer leaving no longer ends the SD session (0.4.3-dev.20). When our session joins, the
  camera signs the phone app out, and the "peer left" message for the app was taken as the camera
  leaving. The bridge then stopped waiting for video and gave up every request at once, which gave the
  empty card listings and instant failures seen in 0.4.3-dev.16 to dev.19. A PEER_OUT now ends the session only
  when it names the camera, or names no one after the link is up. The log names the message's fields.
- Clearer SD log when the card listing is empty (0.4.3-dev.19): it now says whether the camera didn't
  answer the listing or answered with nothing, and when no video frame arrived before the listing, with
  the link statistics for both. The `sd_send_audio` description no longer claims the camera needs the
  audio: with it on, the listing came back empty the same way.
- The SD link sends its silent audio stream again by default (0.4.3-dev.18): without it, the camera never
  answered the card listing. `sd_send_audio` is now on by default.
- SD link no longer sends the camera a silent audio stream (0.4.3-dev.17). It still offers two-way audio
  like the phone app but sends nothing, which cuts our packets to the camera to about a quarter. In every
  stall so far, the camera first stopped answering our checks while still sending its own, and then
  stopped acknowledging our data: what we send stops reaching it. New option `sd_send_audio` (off) brings
  the old behaviour back. The log now says when that happens. A second session no longer waits 35 s for a
  video frame from a camera that is already awake.
- One SD recording no longer costs the rest of the batch (0.4.3-dev.16). Some recordings' requests are
  never acknowledged by the camera, and that blocks the whole data channel. The bridge now closes that
  session and fetches the remaining recordings in a fresh one, trying the stuck recording last there by
  its next path (videoUrl, then one built from the name). The log names the path each GET uses.
- SD clips that stall part-way are asked for again from the byte they stopped at (0.4.3-dev.15), up to 3
  times, after 6 s without data. Through NAT a 4.5 MB clip now came off the card in full at about
  300 kB/s, so the link itself works; the stalls hit single transfers. The log says whether the camera
  answers the new request and whether it honours the range.
- New "Test SD fetch" button (0.4.3-dev.14), under each camera's Diagnostic entities: it fetches the 2
  newest visits from the card now, even if their clips exist, without waiting for `sd_fetch_hours` or a
  restart. An automation can publish a count (1-6) to `<base_topic>/clips/sd_test`.
- SD link statistics now also sort the datagrams each way by kind: STUN, DTLS records (data, handshake,
  alert), RTCP (receiver reports, NACK, PLI) and RTP. In the last NAT run the bridge kept sending about
  50 packets a second after the camera stopped, and this shows what they are.
- SD link without host networking (0.4.3-dev.13): in all three real tests through Home Assistant's NAT, the
  camera stopped sending 10-14 s after the link came up, whatever the window and however much had arrived,
  then went quiet. Our only ICE candidate is the add-on network's private address, which the camera can't
  reach, so its checks to that address keep failing for about that long. The camera now reports the address
  it sees us at, the bridge keeps that LAN address in `/data`, and from the next session it offers the camera
  that address instead. The log says when the link is behind NAT and whether the NAT kept our port. New
  option `sd_nat_candidate` (on) turns this off.
- `sd_rx_window_kb` now shows in the add-on's Configuration tab with its default (1024), instead of being a
  hidden optional setting (0.4.3-dev.12).
- SD link statistics split the ICE checks each way: how many checks we sent and how many the camera answered
  (with the time of the last answer), and how many checks the camera sent and we answered. That tells our
  packets no longer reaching the camera apart from the camera going quiet.
- Debugging the SD clip stall without host networking (0.4.3-dev.11, still without host networking).
  In 0.4.3-dev.9 a clip started through Home Assistant's NAT, then stopped after about 1.3 MB, and the camera
  stopped answering ICE consent checks at about the same moment. While a clip comes off the card, the log now
  shows link statistics every 2 s and when data stops: datagrams each way and how long since the last one,
  STUN messages, socket errors, the receive buffer the kernel granted, the kernel's UDP drop counters, and
  SCTP progress (received TSN, gaps, our acknowledgements). It shows counts only, no addresses or contents. When the link
  comes up, the log names the ICE candidate types in use.
- New troubleshooting option `sd_rx_window_kb`. It sets how much the camera may send before it waits for
  our acknowledgements (aiortc's default is 1 MB). A smaller window, such as 128, sends a clip in smaller
  bursts.
- CI: after ICE through NAT, the fake camera now streams 8 MB, once on a clean link and once with bursty 2%
  loss, so a link that sets up but stalls during a transfer fails the check.
- Trying again without host networking (0.4.3-dev.9), now that SD clips work. The add-on runs on Home
  Assistant's add-on network and opens no ports, which should raise the security rating from 5 to the top.
  The SD card link reaches the camera through that network's NAT, which got through ICE and listed the card in
  0.4.3-dev.3. CI again checks ICE through NAT to a fake camera that has no route back. If a real SD clip
  fails on this build, host networking comes back.
- Confirmed on a real camera (0.4.3-dev.7): the first SD card clip pulled end to end, about 3.9 MB in about
  14 s, which replaced a short cloud clip with the full 20 s recording. The camera answers only the
  `downloadUrl` from its own card listing (`/dc/download/...`). The path the add-on used to build was never
  accepted, which is why no SD clip had arrived before. That also corrects two earlier guesses below: NAT
  (0.4.3-dev.3) and host networking were not the cause, and the ICE consent change (0.4.3-dev.5) kept the
  link open but wasn't what stopped the clip.
- Debug logs no longer mask the `4` in `mp4` path segments.
- SD card clips: the camera never took the clip request. In a debug log from a real camera, the card listing
  came back, but the camera's data channel never acknowledged the GET that followed, while it kept
  answering ICE. The listing names each file by its full card path, in a dated folder, and carries the
  camera's own `downloadUrl` and `videoUrl`. The clip request now uses that URL when it points at the
  camera, and falls back to the old `/dc/video/mp4/<file>` path otherwise. With `log_level: debug` the log
  shows the URL's form (digits and serial masked) and the request sent.
- When the camera stops acknowledging a request for 20 s, the bridge gives up on that session instead of
  waiting out the 90 s clip timeout, and skips the remaining clips in it. The progress line shows how long
  the request has gone unacknowledged.
- More SD card logging. The log says when the camera ends the session or closes the data channel, and
  a clip wait stops there instead of running to its timeout. With `log_level: debug` it also shows each
  signalling message and data frame, and the first entry of the card listing.
- Fixed: an SD card clip that took more than about 30 s to arrive was cut off. The camera appears to stop
  answering ICE consent checks while it streams a clip, and the WebRTC library closed the link after six
  unanswered checks (`Consent to send expired`, then `no data`). The link now stays open, and the log says once when
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
