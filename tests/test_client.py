import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vicohome_mqtt" / "app"))

from vicohome import AuthError, VicohomeClient, VicohomeError  # noqa: E402

DROPPED = requests.ConnectionError(
    "('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))")


class FakeResponse:
    def __init__(self, body: str, status: int = 200) -> None:
        self.text = body
        self.status_code = status

    def json(self):
        import json
        return json.loads(self.text)


class FakeSession:
    """Plays back a list of outcomes: an exception to raise or a response to return."""

    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls = []
        self.headers = {}

    def post(self, url, **kwargs):
        self.calls.append(url)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_client(tmp_path, outcomes, token="test-token"):
    client = VicohomeClient("user@example.com", "not-a-real-password",
                            token_file=str(tmp_path / "token.json"))
    client._session = FakeSession(outcomes)
    client._token = token
    return client


DEVICES_OK = FakeResponse('{"result": 0, "data": {"list": []}}')
LOGIN_OK = FakeResponse('{"result": 0, "data": {"token": {"token": "new-test-token"}}}')


def test_dropped_connection_is_retried_once(tmp_path):
    client = make_client(tmp_path, [DROPPED, DEVICES_OK])
    assert client.devices() == []
    assert len(client._session.calls) == 2


def test_second_drop_in_a_row_still_fails(tmp_path):
    client = make_client(tmp_path, [DROPPED, DROPPED])
    with pytest.raises(VicohomeError, match="ConnectionError"):
        client.devices()
    assert len(client._session.calls) == 2


def test_timeouts_are_not_retried(tmp_path):
    for timeout in (requests.ReadTimeout(), requests.ConnectTimeout()):
        client = make_client(tmp_path, [timeout, DEVICES_OK])
        with pytest.raises(VicohomeError):
            client.devices()
        assert len(client._session.calls) == 1


def test_login_retries_a_dropped_connection(tmp_path):
    client = make_client(tmp_path, [DROPPED, LOGIN_OK, DEVICES_OK], token="")
    assert client.devices() == []
    assert client._token == "new-test-token"
    assert len(client._session.calls) == 3


def test_an_html_error_page_is_an_outage_not_a_login_problem(tmp_path):
    client = make_client(tmp_path, [FakeResponse("<html>502 Bad Gateway</html>", 502)])
    with pytest.raises(VicohomeError, match="non-JSON") as err:
        client.devices()
    assert not isinstance(err.value, AuthError)
    assert len(client._session.calls) == 1   # no second login round


def test_http_401_still_logs_in_again(tmp_path):
    client = make_client(tmp_path, [FakeResponse("", 401), LOGIN_OK, DEVICES_OK])
    assert client.devices() == []
    assert client._token == "new-test-token"


@pytest.mark.parametrize("url, gets_token", [
    ("https://api-us.vicohome.io/x.m3u8", True),
    ("https://media.vicoo.tech/a.jpg", True),
    ("http://api-us.vicohome.io/x.m3u8", False),                 # never over plain HTTP
    ("https://storage.example.com/a.jpg?src=vicohome.io", False),
    ("https://vicohome.io.example.com/a.jpg", False),
    ("https://notvicohome.io/a.jpg", False),
])
def test_the_token_only_goes_to_the_api_hosts(tmp_path, url, gets_token):
    client = make_client(tmp_path, [])
    assert ("Authorization" in client._auth_headers(url)) is gets_token
