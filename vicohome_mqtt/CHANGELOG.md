# Changelog

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
