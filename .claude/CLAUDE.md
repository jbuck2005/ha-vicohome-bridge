# CLAUDE.md

Standing rules for AI assistants working in this repository.

## Commits and pull requests
- Never credit Claude or any AI assistant: no `Co-Authored-By` or session trailers, no "Generated with" lines, no AI author or committer. Commits show only the maintainer.
- Exception: Dependabot's dependency PRs may be merged with `dependabot[bot]` as author. Don't flag them, and don't redo their commits under the maintainer's name.
- Author and committer email: `59450931+jbuck2005@users.noreply.github.com`.
- Name branches `dev/<topic>`. Never put "claude" in a branch name: merging copies it into the history of `main`.
- PRs go into `edge`, not `main`. `edge` merges into `main` only as a release, when the maintainer says so. Never force-push `edge`.
- Keep "claude" and "anthropic" out of commit messages and PR titles, including for changes to this file (write "project rules").
- Stage files by name. Never `git add -A` or `git add .`.
- Never force-push `main`.

## Privacy
- The repo is public. Nothing home-specific belongs in it: no IPs, hostnames, paths, names or credentials.
- Tests use made-up data. Never paste real camera serials, clip or trace ids, IP addresses, account emails or API responses into tests, docs, commits, PRs or issues. A clip id can identify an account.
- Never log the Vicohome password, API token or MQTT credentials, at any log level.
- The Vicohome CG625 model name is fine to mention.

## Code
- This is a ground-up rewrite. Don't copy code from `KIWIDUDE564/vicohome-bridge-addon`: it has no licence. `dydx/vico-cli` is MIT. Keep both credits in the README.
- This project has no connection with Vicohome, and the docs must not suggest one.
- For changes to the add-on, bump the edge build in `vicohome_mqtt/config.yaml` and `VERSION` in `bridge.py` (`0.3.5` -> `0.3.6-dev.1` -> `0.3.6-dev.2`), and add to `## Unreleased` in `vicohome_mqtt/CHANGELOG.md`. CI fails a PR into `edge` that changes `vicohome_mqtt/` without a new version.
- A release (a PR from `edge` into `main`) sets the plain version (`0.3.6`) and renames `## Unreleased` to it.

## Checks
- Run `pytest tests` before committing.
- For changes to the bridge, also run `python tests/e2e_fake_cloud.py`. It needs ffmpeg and an MQTT broker at `$MQTT_HOST` that allows anonymous clients.
