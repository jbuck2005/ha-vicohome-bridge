import re
from pathlib import Path

ADDON = Path(__file__).resolve().parent.parent / "vicohome_mqtt"


def test_bridge_version_matches_config():
    # The bridge reports VERSION in MQTT discovery and its status topic; it must match the add-on.
    config = re.search(r'^version:\s*"([^"]+)"', (ADDON / "config.yaml").read_text(), re.M).group(1)
    bridge = re.search(r'^VERSION = "([^"]+)"', (ADDON / "app" / "bridge.py").read_text(), re.M).group(1)
    assert bridge == config


def test_changelog_has_current_version():
    config = re.search(r'^version:\s*"([^"]+)"', (ADDON / "config.yaml").read_text(), re.M).group(1)
    assert f"## {config}\n" in (ADDON / "CHANGELOG.md").read_text()
