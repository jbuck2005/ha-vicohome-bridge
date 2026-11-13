# Vicohome MQTT Bridge (Home Assistant add-on)

![vicohome-bridge: Camera in. Wings out.](images/banner.png)

The bridge between your Vicohome camera and [un-subscribird](https://github.com/jbuck2005/unsubscribird).
Brings Vicohome bird-feeder cameras into Home Assistant over MQTT: newest bird, snapshot,
visit events, today's visit counts, battery and Wi-Fi. A ground-up Python rewrite inspired by
[KIWIDUDE564/vicohome-bridge-addon](https://github.com/KIWIDUDE564/vicohome-bridge-addon)
and the API work in [dydx/vico-cli](https://github.com/dydx/vico-cli).

Tested with the Vicohome CG625 bird-feeder camera.

- Add-on: [`vicohome_mqtt/`](vicohome_mqtt/) - docs in [`DOCS.md`](vicohome_mqtt/DOCS.md)
- Example dashboard: [`dashboard/bird_cam.yaml`](dashboard/bird_cam.yaml)
- Tests: `pytest tests` (unit) and `tests/e2e_fake_cloud.py` (fake cloud + real broker)
- Brand: [`BRANDING.md`](BRANDING.md), a sibling of un-subscribird

## Install
[![Open your Home Assistant instance and show the add add-on repository dialog with this repository pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fjbuck2005%2Fha-vicohome-bridge)
[![Open your Home Assistant instance and show the Vicohome MQTT Bridge add-on.](https://my.home-assistant.io/badges/supervisor_addon.svg)](https://my.home-assistant.io/redirect/supervisor_addon/?addon=d4fab3cf_vicohome_mqtt&repository_url=https%3A%2F%2Fgithub.com%2Fjbuck2005%2Fha-vicohome-bridge)

The first button adds this repository to your add-on store; the second opens the add-on, adding
the repository first if needed. Then click **Install**.

Or by hand: Settings -> Add-ons -> Add-on store -> ... -> Repositories -> add this repository's
URL, then install **Vicohome MQTT Bridge**. Or copy `vicohome_mqtt/` into the `/addons` (local
add-ons) folder and reload the store.

This project has no connection with Vicohome. The dashboard assumes a camera named "Bird Cam": replace `bird_cam` with
your camera's name.

## Licence

MIT, see [LICENSE](LICENSE).

## Credits

- WebRTC signalling for the SD-card backfill follows the MIT project [averagenative/vicolocal](https://github.com/averagenative/vicolocal); its compact-SDP codec is vendored as `vico_sdp.py`.
