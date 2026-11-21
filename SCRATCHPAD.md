# Scratchpad

My working log for ha-vicohome-bridge, newest first. This branch is a scratch pad for sharing progress
between sessions. It is never merged into `main` or `edge`.

---

## 2026-11-21: Why some GitHub Actions runs failed

**Changed:** Nothing in code. I went through every failed run in both repos. All of them were on PR branches
while the work was in progress, and a later push on the same PR went green each time. Today's three were the new
enforced-AppArmor job doing its job: #27's first push had ffmpeg/ffprobe blocked from reading
`/sys/devices/system/node/` (allowed in the profile before merge), and #28's first two pushes had the NAT ICE
test write to its `/ice` test mount, which the profile blocked (fixed in the PR). unsubscribird's last failure
(#61, 2026-11-17) was a new test that couldn't import `app`, fixed on the next push.
**Status:** `edge` and `main` are green in both repos. GitHub's failure emails come from those intermediate PR runs.
**Next:** nothing. Heads-up: GitHub says `ubuntu-latest` moves to Ubuntu 26; if the AppArmor job breaks on that,
pin the runner image.

---

## 2026-11-21: First SD card clip from a real camera (dev.7 works; PR #33 records it, 0.4.3-dev.8)

**Result:** on 0.4.3-dev.7 my camera served the clip. The request went to the listing's `downloadUrl` (`/dc/download/mp4/<file>?sn=`; `videoUrl` is `/dc/video/mp4/...`). About 3.9 MB arrived in about 14 s, and the visit went from a short cloud clip to the full 20 s from the card. This is the first SD clip we've seen arrive on real hardware.
**So:** the old built path was never accepted. NAT and host networking weren't the cause, and the consent fix wasn't either.
**Changed (PR #33):** the CHANGELOG and the README security-rating section now say so. Debug logs no longer mask `mp4`.
**Next:** retry without host networking on top of dev.7 (rating 5 to 6), with one real SD test. If it fails, revert as before.

## 2026-11-21: Camera never accepts the SD clip request (PR #32 into edge, 0.4.3-dev.7)

**Found:** in my dev.6 debug log, ICE stayed up and the card listing came back. Then the camera never acknowledged the clip GET at the data-channel (SCTP) level. aiortc resent it every second for 90 s. Every earlier message was acknowledged. So the camera didn't refuse the request, it never accepted it. The listing entries give the full card path (in a dated folder) and their own `downloadUrl` / `videoUrl`, which we had ignored.
**Changed:** the GET now uses the listing's local `downloadUrl` (else `videoUrl`, else the old path). After 20 s with no acknowledgement, the session gives up instead of waiting 90 s per clip. Debug logs show the URL's form with digits and the serial masked.
**Status:** squash-merged into edge (0.4.3-dev.7), CI green.
**Next:** merge, update, repeat the debug SD test. Look for `sdfetch list: downloadUrl looks like` and `sdfetch: GET` lines, and whether the camera sends a status frame.

## 2026-11-21: Camera sends nothing for an SD clip (PR #31 into edge, 0.4.3-dev.6)

**Changed:** on dev.5 the link stayed up, but the camera answered the card listing and then sent 0 kB for the
clip request in 90 s, and stopped answering STUN about 8 s after it. So consent was a symptom: the camera
goes quiet on the clip request itself. #31 logs hang-ups and data-channel close (and stops waiting there), and
at debug level every signalling message, data frame and the first listing entry.
**Status:** squash-merged into edge (0.4.3-dev.6). No record found of an SD clip ever arriving on real hardware; treating it as the request never having worked. Best guess: the camera ends the session ~10-15 s after stopLive (sleep or missing heartbeat).
**Next:** merge, set log_level debug, repeat the test, read the sdfetch lines.

---

## 2026-11-21: SD fetch cut off by ICE consent (PR #30 into edge, 0.4.3-dev.5)

**Changed:** dev.4 (host networking) failed the same way as dev.3, so NAT wasn't the cause. ICE completes, the
card lists, a clip starts, then aioice logs `Consent to send expired` about 40 s after ICE and closes the link.
I reproduced that exactly with a fake camera that stops answering STUN checks mid-stream. #30 keeps the link
open when consent checks go unanswered (logs once), and logs clip progress every 10 s. README and changelog
no longer blame NAT.
**Status:** squash-merged into edge (0.4.3-dev.5), CI green.
**Next:** merge, update, repeat the SD test (sd_only, delete one recent clip, restart). Watch the progress lines.
If it works, bridged networking is worth retrying.

---

## 2026-11-21: Host networking back (PR #29 into edge, 0.4.3-dev.4)

**Changed:** my real SD test on 0.4.3-dev.3 failed. Through NAT, ICE completed and the camera listed its card,
but the link went silent as soon as the first clip download started (no data, consent checks unanswered,
consent expired about 30 s later). The aiortc fake camera never did that. #29 reverts #28 and makes an empty
`session failed:` log name the error type.
**Status:** squash-merged into edge, CI green. README now has a "Security rating" section. Note SD fetch hasn't been proven on host networking + AppArmor + the #26 changes either.
**Next:** update to dev.4 and repeat the same SD test (sd_only, delete one recent clip, restart). If it works,
NAT is the cause and bridged mode stays parked; if it fails too, the problem is in dev.1/dev.2.

---

## 2026-11-21: No host networking (PR #28 into edge, 0.4.3-dev.3)

**Changed:** I removed `host_network` so the add-on runs on Supervisor's add-on network with no ports, which
should take the rating from 5 to the top of the scale. Only the SD card WebRTC link ever needed the host
network. Our ICE checks now reach the camera through the host's NAT and the camera answers the NAT'd address
(peer-reflexive). A new CI check puts a fake camera behind a namespace with no route back to the container
network and proves the data channel opens through NAT (and fails without it). A failed link now logs
`no link to the camera` instead of a bare timeout.
**Status:** squash-merged into edge (0.4.3-dev.3) with CI green, including the NAT check under the enforced
profile. Still unproven against the real camera's ICE stack.
**Next:** merge, update the edge add-on on HA (take the backup it offers), then check the rating, an SD fetch
or `sd_poll` read, and the log. If SD breaks, restore the backup or revert the PR.

---

## 2026-11-21: Signed images research (no PR)

**Changed:** nothing in the code. I checked whether signed prebuilt images would earn the last rating point. They
don't. Supervisor 2025.11.1 removed the Codenotary check, the `codenotary:` field is ignored with a deprecation
warning, and `signed` is hard-coded to False in the current supervisor source, so no add-on gets the signing point
today, with Codenotary or cosign. The HA builder can still sign with cosign keyless, but nothing reads it yet.
Our 5 comes from base 5, +1 AppArmor, -1 host network. The only lever left is dropping host networking (+1, and
with no ports or ingress another +2), which needs the SD card link to work from a bridged container.
**If I do prebuilt images anyway, this goes public:** a public GHCR package (download count shown); OCI labels
(source repo, version, build time); with cosign, a permanent Rekor entry naming the repo, owner id, workflow path,
branch or tag, commit and run (no email or personal name, since keyless signing uses the workflow's OIDC identity);
possibly a buildx provenance attestation, which can be turned off.
**Status:** research only, waiting on my decision.
**Next:** decide whether to test the bridge without host networking, and whether prebuilt images are worth it for
install speed alone.

---

## 2026-11-21: AppArmor profile (PR #27 into edge, 0.4.3-dev.2)

**Changed:** I added `vicohome_mqtt/apparmor.txt` so the add-on's security rating goes from 4 to 5. Host networking
stays. The profile only lets the container run Python, ffmpeg and ffprobe, write to `/data`, `/tmp`, `/media` and
`<config>/www`, and open IP, netlink and Unix sockets. The rest of the HA config isn't readable from inside it.
A new CI job loads the profile in enforce mode, runs the fake-cloud e2e test and a WebRTC/ICE check inside the
image under it, and fails on any denial.
**Status:** #26 and #27 are both squash-merged into edge (0.4.3-dev.2). The first enforced CI run passed every test but
flagged ffmpeg reading the NUMA node list in `/sys`; I allowed that and the rerun was clean.
**Next:** after the edge build lands on HA, check the rating shows 5 and the host log has no `apparmor="DENIED"`
lines for `vicohome_mqtt`, especially after an SD card fetch, which CI can't test.

---

## 2026-11-18: Review fixes (PR #26 into edge, 0.4.3-dev.1)

**Changed:** the ICE interface filter is patched once, so SD polls no longer die of a RecursionError after about
a thousand wakes. SD visit ids are keyed with a random secret in `/data/sd_key`, so clip names under `/local/`
can't be guessed (old ids are still recognised). The token only goes to the API hosts over HTTPS. A 502 page
no longer forces a re-login. SD downloads parse incrementally, and a full disk skips one clip. 89 tests and the
e2e test pass, and CI is green.

**Status:** #26 open and green, waiting on my merge.

**Next:** merge #26, let the edge add-on update on HA, and watch one SD poll in the log.

---

## 2026-11-18: Health review of both repos

**Checked:** branches, edge vs main, open PRs and issues, CI runs, and the code. Tests pass (77). CI is green.
`main` = `edge` = 0.4.2. No open PRs or issues.

**Found:**
- High: `sdfetch` wraps `aioice.ice.get_host_addresses` on every camera connect and never puts it back.
  The wrappers stack up, and after about 1,000 connects ICE gathering hits a RecursionError, so SD polls and
  backfills start failing until the add-on restarts. With `sd_poll` every 15 min that is about 10 days.
  I reproduced the RecursionError in a quick script.
- High (privacy): SD-poll clips are named `sd-<6 hex>-<epoch>.mp4` under `/local/`, which needs no login. The
  6 hex never change for a camera, so anyone who has seen one link can guess the rest by trying timestamps.
  DOCS.md says the names can't be guessed. Cloud trace ids also contain the visit's epoch, so check how
  much randomness they really have.
- Medium: the API token goes on any URL that merely contains `vicohome.io` or `vicoo.tech`, not only on those
  hosts.
- Medium: `sdfetch._collected()` re-joins and re-parses the whole receive buffer every 0.3 s while a clip
  downloads (quadratic CPU on the HA box).
- Low: a non-JSON reply (an HTML 502 page) counts as a login error and triggers a re-login.
- Low: `sd_backfill` writes the clip without catching OSError (a full disk aborts the rest of the poll).
- A stray `claude/project-thread-7ted09` branch is on the remote again. I need to delete it by hand.

**Status:** report only, no code changed. Waiting on me to pick what to fix.

**Next:** fix PRs into `edge` (0.4.3-dev.1) for whatever I pick, starting with the two High items.

---

## 2026-11-15: 0.4.2 prepared

**What changed.** I merged everything that was waiting into `edge` and set it to 0.4.2:
- #22: each SD card recording goes to one visit only.
- #21: visits from the SD card (`visit_source: sd_poll`). I merged `edge` into it, fixed the
  version and changelog conflicts, and bumped it to 0.4.2-dev.3.
- #23: Python 3.15, replacing Dependabot's #18, which was built on old history and conflicted.
  The requirements install and the tests pass on 3.15, and the image builds on both
  architectures. Dependabot then closed #18 itself.
- #24: version 0.4.2, with the changelog and RELEASES.md notes.

**Status.** #25 is merged into `main` with a merge commit, so `main` has the same files as `edge`, and 0.4.2 is
in the add-on store. The Release workflow drafted `v0.4.2` with no hold note.

**Next.**
- Review the draft under Releases and press Publish. That creates the tag, and today is still free.
- Fix the v0.3.5 release notes, which show a raw `sed` command instead of the changelog.

---

## 2026-11-14: SD backfill could give one recording to two visits (PR #22)

**What I checked.** I was worried the SD card path was double counting. It wasn't, in the cases I saw: every
clip on the station is unique, and those visits came from the cloud.

**What I found anyway.** `sdfetch._run` gave a matched SD recording to every visit that fell inside it. Two visits
inside one long recording would both get the same file.

**What I changed** (PR #22, `dev/sd-one-clip-per-visit`, edge 0.4.2-dev.2):
- `assign_segments()` gives each recording to the visit that started closest to it. The others keep their cloud
  clip, and the log says how many were left out.
- `pytest tests`: 66 passed.

**Status.** Merged into `edge` on 2026-11-15.

**Next.** #21 (sd_poll) now needs a rebase and the next dev number, because this took 0.4.2-dev.2.
`_sd_known` in #21 already skips recordings that a cloud visit covers, so cloud plus SD poll doesn't double up.

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
