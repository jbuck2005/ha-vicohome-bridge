# Vicohome MQTT Bridge (Home Assistant add-on)

Brings Vicohome bird-feeder cameras into Home Assistant over MQTT: newest bird, snapshot,
visit events, today's visit counts, battery and Wi-Fi. A ground-up Python rewrite inspired by
[KIWIDUDE564/vicohome-bridge-addon](https://github.com/KIWIDUDE564/vicohome-bridge-addon)
and the API work in [dydx/vico-cli](https://github.com/dydx/vico-cli).

Tested with the Vicohome CG625 bird-feeder camera.

- Add-on: [`vicohome_mqtt/`](vicohome_mqtt/) - docs in [`DOCS.md`](vicohome_mqtt/DOCS.md)
- Example dashboard: [`dashboard/bird_cam.yaml`](dashboard/bird_cam.yaml)
- Tests: `pytest tests` (unit) and `tests/e2e_fake_cloud.py` (fake cloud + real broker)

## Install
Settings -> Add-ons -> Add-on store -> ... -> Repositories -> add this repository's URL, then
install **Vicohome MQTT Bridge**. Or copy `vicohome_mqtt/` into the `/addons` (local add-ons)
folder and reload the store.

This project has no connection with Vicohome. The dashboard assumes a camera named "Bird Cam": replace `bird_cam` with
your camera's name.

## Licence

MIT, see [LICENSE](LICENSE).
