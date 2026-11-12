import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vicohome_mqtt" / "app"))

from bridge import hours_from  # noqa: E402


def test_refetch_hours_from_payload():
    assert hours_from(b"PRESS", 2) == 2  # the button: use the hours entity's value
    assert hours_from(b"6", 2) == 6
    assert hours_from(b" 3.4 ", 2) == 3
    assert hours_from(b"0", 2) == 1 and hours_from(b"-5", 2) == 1
    assert hours_from(b"100", 2) == 24
    assert hours_from(b"inf", 2) == 2 and hours_from(b"nan", 2) == 2 and hours_from(b"\xff", 2) == 2
