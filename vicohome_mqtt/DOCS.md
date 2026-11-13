# Vicohome MQTT Bridge

Polls the Vicohome cloud for your bird-feeder cameras and publishes them to Home Assistant
over MQTT (with discovery). Needs the Mosquitto broker add-on and the MQTT integration.

## Setup
1. Use a **separate Vicohome account** shared from the main one (the Vicohome app lets you
   share a camera). Logging in here can sign that account out of the phone app.
2. Fill in `email` and `password`, start the add-on, and watch the log for `MQTT connected`
   and `new event(s) (initial history load)`.
3. Only run ONE bridge per Vicohome account (stop any other Vicohome add-on).

## Entities (per camera, e.g. "Bird Cam")
| Entity | What it is |
|---|---|
| `sensor.<cam>_last_bird` | Name of the newest visitor ("Unidentified" if the AI didn't name it). Attributes: latin, confidence, time, period, image/keyshot/video URLs, saved_image |
| `sensor.<cam>_last_visit` | Time of the newest visit (timestamp) |
| `sensor.<cam>_visits_today` | Visits since local midnight. Attributes: identified, species, by_bird, first, last, recent (last 10) |
| `image.<cam>_snapshot` | The newest snapshot, stored in HA (works remotely; no expiring cloud link) |
| `event.<cam>_visit` | Fires once per NEW visit (`bird` / `unidentified`) - use it to trigger automations |
| `binary_sensor.<cam>_motion` | On for `motion_seconds` after each new visit |
| `sensor.<cam>_battery`, `sensor.<cam>_wifi_signal`, `binary_sensor.<cam>_online`, `binary_sensor.<cam>_charging`, `sensor.<cam>_ip_address` | Camera health |
| `sensor.<cam>_cloud_status`, `sensor.<cam>_last_cloud_poll` | Bridge health (`ok`, `error`, `login error`) |

## Options
| Option | Default | |
|---|---|---|
| `email`, `password` | | The Vicohome account (see Setup). Never logged. |
| `region` | us | `us` or `eu`: the Vicohome cloud region of the account. |
| `api_base_override` | | Leave empty. Only for testing against another API server. |
| `poll_interval` | 60 | Seconds between cloud polls (30-3600). |
| `bootstrap_hours` | 24 | History loaded on the very first start (no events fired for it). |
| `image_source` | keyshot | `keyshot` (the frame the AI picked) or `image` (clip cover); falls back to the other. |
| `save_images` | true | Keep snapshots in `/media/<media_folder>/<camera>/<date>/` (Media panel). |
| `media_folder` | vicohome | Folder under `/media`. |
| `retention_days` | 30 | Delete saved snapshots older than this (0 = keep forever). |
| `motion_seconds` | 60 | How long the motion sensor stays on. |
| `save_clips` | true | Turn each visit's clip into an MP4 in `/config/www/<clip_folder>/`, linked as `clip_url` (`/local/...`). |
| `clip_source` | cloud_first | `cloud_first`: if a cloud clip is incomplete and the camera has a microSD card, pull the full recording off the card over WebRTC on the LAN. `cloud_only`: never use the card. |
| `clips_to_keep` | 60 | Starting AND minimum clip limit (newest N clips kept, about 3 MB each). |
| `clips_max` | 600 | The limit never grows past this. |
| `clip_folder` | vicohome | Folder under `/config/www`. |
| `base_topic` | vicohome_mqtt | MQTT topic root. |
| `discovery_prefix` | homeassistant | Home Assistant's MQTT discovery prefix. |
| `log_level` | info | `debug`, `info`, `warning` or `error`. |
| `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` | (Supervisor) | Only needed for an external broker. |

## Clips
Vicohome serves clips as HLS playlists (`.m3u8`) whose segments are signed cloud links that
expire; most players can't open a downloaded playlist. With `save_clips` on, the bridge
remuxes each clip with ffmpeg (no re-encoding) into an MP4 and sets `clip_url` on the visit.
The cloud can list a visit before its whole video is uploaded, so the bridge waits until the
playlist is the same on two polls in a row (at most 5 minutes after the visit), then checks it
again 2, 5, 15 and 30 minutes later. If it grew, the MP4 is made again under the same name and
`clip_url` stays the same. Once an hour the bridge also lists the last two days of visits again
and makes any kept clip that is still shorter than the cloud's video (or missing) again from a
fresh link, three tries at most; so raising `clips_to_keep` brings back clips of recent visits.
Note: Home Assistant serves `/config/www` at `/local/` **without a login**. File names are the
visit's random trace id, so they can't be guessed, but anyone given a link can open it.
Clips are also included in Home Assistant backups (another reason only the newest N are kept).

### SD-card backfill
Battery cameras sometimes upload only the first seconds of a clip when the link is poor, but keep the whole
recording on their microSD card. With `clip_source: cloud_first` (the default), when the cloud marks a visit
incomplete and the camera has a card, the bridge fetches the full recording from the card over a WebRTC
data-channel link on the local network (the same path the app uses) and replaces the short clip. Each fetch
wakes the camera and briefly signs the phone app out (a camera serves one live session at a time), so it runs
only for incomplete clips, a few per hour at most. Set `clip_source: cloud_only` to turn it off. The add-on
runs on the host network for the peer-to-peer link, so the Home Assistant host must be on the camera's LAN.

### Self-sizing clip limit (daily audit)
Just after local midnight the bridge audits the previous day (only days it saw completely):
- A day **fills the space** when its visits alone reach the limit (the limit couldn't hold one day).
- **2 filled days in a row** -> the limit grows to max(limit + 30, 1.25 x the busier day), rounded up to 10,
  capped at `clips_max`, and only if more than 2 GB is free.
- **7 days in a row using at most half** of the limit -> it shrinks to 1.5 x the busiest of those days
  (never below `clips_to_keep`).
`sensor.<cam>_clip_storage` (MB) carries count, limit, streaks, free space and the last 14 audits;
`sensor.<cam>_clip_limit` is the current limit.

## How it differs from the original vicohome-bridge add-on
- Only **new** events are published, oldest first, so "last" is always the newest visit
  (the old add-on re-sent the whole day newest-first, leaving the oldest one as "last").
- Times come from the API's unix timestamps (no timezone guessing).
- The snapshot is published as image bytes; HA keeps it even after the cloud link expires.
- A proper MQTT last-will marks entities unavailable if the bridge dies.
- The add-on never logs the password, token or MQTT credentials, at any log level.
