# Changelog

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
