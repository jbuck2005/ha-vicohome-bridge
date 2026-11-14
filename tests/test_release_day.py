import importlib.util
from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

_PATH = Path(__file__).resolve().parent.parent / ".github" / "scripts" / "release_day.py"
_spec = importlib.util.spec_from_file_location("release_day", _PATH)
release_day = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_day)

TORONTO = ZoneInfo("America/Toronto")


def test_lightweight_tag_counts_on_its_publish_day_not_its_commit_day():
    # GitHub makes a lightweight tag on Publish; git dates it by its commit (two days earlier here),
    # and for-each-ref shows no tagger date. The release's publishedAt is the day that counts.
    releases = [{"tagName": "v9.1.0", "publishedAt": "2030-03-12T15:00:00Z", "isDraft": False}]
    tags = ["v9.1.0 commit "]
    times = release_day.release_times(releases, tags)
    assert release_day.same_day(times, date(2030, 3, 12), TORONTO) == ["v9.1.0"]
    assert release_day.same_day(times, date(2030, 3, 10), TORONTO) == []


def test_lightweight_tag_without_a_release_is_not_counted():
    times = release_day.release_times([], ["v9.0.9 commit "])
    assert times == {}


def test_annotated_tag_without_a_release_uses_its_tagger_date():
    times = release_day.release_times([], ["v9.0.8 tag 2030-03-12T09:30:00-04:00"])
    assert release_day.same_day(times, date(2030, 3, 12), TORONTO) == ["v9.0.8"]


def test_drafts_are_ignored():
    releases = [{"tagName": "v9.2.0", "publishedAt": "", "isDraft": True}]
    assert release_day.release_times(releases, []) == {}


def test_day_is_counted_in_the_release_time_zone():
    # 02:30 UTC on the 13th is still the 12th in Toronto.
    releases = [{"tagName": "v9.1.0", "publishedAt": "2030-03-13T02:30:00Z", "isDraft": False}]
    times = release_day.release_times(releases, [])
    assert release_day.same_day(times, date(2030, 3, 12), TORONTO) == ["v9.1.0"]
    assert release_day.same_day(times, date(2030, 3, 13), ZoneInfo("UTC")) == ["v9.1.0"]


def test_the_release_being_checked_is_excluded():
    releases = [{"tagName": "v9.1.0", "publishedAt": "2030-03-12T15:00:00Z", "isDraft": False},
                {"tagName": "v9.1.1", "publishedAt": "2030-03-12T18:00:00Z", "isDraft": False}]
    times = release_day.release_times(releases, [])
    assert release_day.same_day(times, date(2030, 3, 12), TORONTO, exclude="v9.1.1") == ["v9.1.0"]


def test_cli_output(tmp_path, capsys):
    (tmp_path / "r.json").write_text('[{"tagName": "v9.1.0", "publishedAt": "2030-03-12T15:00:00Z", "isDraft": false}]')
    (tmp_path / "t.txt").write_text("v9.1.0 commit \n")
    release_day.main([str(tmp_path / "r.json"), str(tmp_path / "t.txt"), "--tz", "America/Toronto",
                      "--now", "2030-03-12T20:00:00Z"])
    assert capsys.readouterr().out.splitlines() == ["today=2030-03-12", "tomorrow=2030-03-13", "same_day=v9.1.0"]


def test_cli_output_with_two_tags_can_be_sourced(tmp_path, capsys):
    import subprocess
    (tmp_path / "r.json").write_text('[{"tagName": "v9.1.0", "publishedAt": "2030-03-12T15:00:00Z", "isDraft": false},'
                                     ' {"tagName": "v9.1.1", "publishedAt": "2030-03-12T16:00:00Z", "isDraft": false}]')
    (tmp_path / "t.txt").write_text("")
    release_day.main([str(tmp_path / "r.json"), str(tmp_path / "t.txt"), "--now", "2030-03-12T20:00:00Z"])
    (tmp_path / "day.txt").write_text(capsys.readouterr().out)
    out = subprocess.run(["sh", "-c", f'. "{tmp_path / "day.txt"}" && printf %s "$same_day"'],
                         capture_output=True, text=True, check=True).stdout
    assert out == "v9.1.0 v9.1.1"
