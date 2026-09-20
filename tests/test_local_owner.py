from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
import requests

from ashare_screener.local_owner import (
    LocalOwnerProxy,
    OwnerConfigurationError,
    OwnerConnectionError,
    load_owner_credentials,
)


class FakeResponse:
    def __init__(
        self,
        status: int,
        *,
        payload=None,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status
        self._payload = payload
        self.content = body if body is not None else json.dumps(payload or {}).encode()
        self.headers = headers or {}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []
        self.closed = False

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def close(self):
        self.closed = True


def write_vars(path: Path, *, username="owner-user", password="long=password#value"):
    path.write_text(
        "# local owner only\n"
        f"TRADEA_OWNER_USERNAME={username}\n"
        f"TRADEA_OWNER_PASSWORD={password}\n",
        encoding="utf-8",
    )


def admin_login():
    return FakeResponse(
        200,
        payload={"user": {"id": "owner-id", "role": "admin"}},
        headers={"Set-Cookie": "tradea_session=must-not-be-forwarded"},
    )


def test_load_owner_credentials_treats_values_as_literals_and_redacts_repr(tmp_path):
    path = tmp_path / ".owner.vars"
    path.write_text(
        "export TRADEA_OWNER_USERNAME='owner-user'\n"
        'TRADEA_OWNER_PASSWORD="literal$VALUE=with#marks"\n'
        "IGNORED_VALUE=anything\n",
        encoding="utf-8",
    )

    credentials = load_owner_credentials(path)

    assert credentials.username == "owner-user"
    assert credentials.password == "literal$VALUE=with#marks"
    assert "owner-user" not in repr(credentials)
    assert credentials.password not in repr(credentials)


@pytest.mark.parametrize(
    "content",
    [
        "TRADEA_OWNER_USERNAME=owner-user\n",
        "TRADEA_OWNER_USERNAME=owner-user\nTRADEA_OWNER_USERNAME=again\n"
        "TRADEA_OWNER_PASSWORD=password\n",
        "TRADEA_OWNER_USERNAME=owner-user\nTRADEA_OWNER_PASSWORD='unterminated\n",
        "this is not an assignment\n",
    ],
)
def test_load_owner_credentials_rejects_incomplete_or_malformed_files(
    tmp_path, content
):
    path = tmp_path / ".owner.vars"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(OwnerConfigurationError):
        load_owner_credentials(path)


def test_proxy_logs_in_lazily_and_returns_only_allowed_response_metadata(tmp_path):
    path = tmp_path / ".owner.vars"
    write_vars(path)
    session = FakeSession(
        [
            admin_login(),
            FakeResponse(
                200,
                body=b'{"members":[]}',
                headers={
                    "Content-Type": "application/json; charset=utf-8",
                    "Content-Disposition": 'attachment; filename="members.json"',
                    "Set-Cookie": "private-cookie=do-not-forward",
                    "X-Private": "do-not-forward",
                },
            ),
        ]
    )
    proxy = LocalOwnerProxy(path, base_url="https://example.test/", session=session)

    assert proxy.connected is False
    result = proxy.request("get", "/api/members?active=1")

    assert proxy.connected is True
    assert result.status == 200
    assert result.body == b'{"members":[]}'
    assert result.content_type == "application/json; charset=utf-8"
    assert result.content_disposition == 'attachment; filename="members.json"'
    assert not hasattr(result, "headers")
    assert [call[:2] for call in session.calls] == [
        ("POST", "https://example.test/api/auth/login"),
        ("GET", "https://example.test/api/members?active=1"),
    ]
    assert session.calls[1][2]["headers"] == {
        "Accept": "application/json, */*;q=0.8"
    }


def test_proxy_forwards_body_and_content_type(tmp_path):
    path = tmp_path / ".owner.vars"
    write_vars(path)
    session = FakeSession([admin_login(), FakeResponse(201, body=b'{"ok":true}')])
    proxy = LocalOwnerProxy(path, base_url="https://example.test", session=session)

    result = proxy.request(
        "POST",
        "/api/favorites",
        b'{"code":"000001"}',
        "application/json; charset=utf-8",
    )

    assert result.status == 201
    api_call = session.calls[1][2]
    assert api_call["data"] == b'{"code":"000001"}'
    assert api_call["headers"]["Content-Type"] == "application/json; charset=utf-8"


def test_proxy_drops_upstream_headers_with_control_characters(tmp_path):
    path = tmp_path / ".owner.vars"
    write_vars(path)
    session = FakeSession(
        [
            admin_login(),
            FakeResponse(
                200,
                body=b"download",
                headers={
                    "Content-Type": "application/json\r\nX-Injected: yes",
                    "Content-Disposition": "attachment\nX-Injected: yes",
                },
            ),
        ]
    )
    proxy = LocalOwnerProxy(path, base_url="https://example.test", session=session)

    result = proxy.request("GET", "/api/training/export")

    assert result.content_type == "application/octet-stream"
    assert result.content_disposition is None


def test_proxy_reauthenticates_exactly_once_after_401(tmp_path):
    path = tmp_path / ".owner.vars"
    write_vars(path)
    session = FakeSession(
        [
            admin_login(),
            FakeResponse(401, body=b'{"error":"expired"}'),
            admin_login(),
            FakeResponse(200, body=b'{"favorites":[]}'),
        ]
    )
    proxy = LocalOwnerProxy(path, base_url="https://example.test", session=session)

    result = proxy.request("GET", "/api/favorites")

    assert result.status == 200
    assert proxy.connected is True
    assert [call[0] for call in session.calls] == ["POST", "GET", "POST", "GET"]


def test_second_401_is_returned_without_another_login(tmp_path):
    path = tmp_path / ".owner.vars"
    write_vars(path)
    session = FakeSession(
        [admin_login(), FakeResponse(401), admin_login(), FakeResponse(401)]
    )
    proxy = LocalOwnerProxy(path, base_url="https://example.test", session=session)

    result = proxy.request("GET", "/api/favorites")

    assert result.status == 401
    assert proxy.connected is False
    assert len(session.calls) == 4


@pytest.mark.parametrize(
    "path",
    [
        "/api/auth/login",
        "/api/auth/setup?source=local",
        "/api/auth/logout/",
        "/api/auth/%6Cogin",
    ],
)
def test_proxy_rejects_browser_auth_mutations_without_contacting_remote(
    tmp_path, path
):
    vars_path = tmp_path / ".owner.vars"
    write_vars(vars_path)
    session = FakeSession([])
    proxy = LocalOwnerProxy(
        vars_path, base_url="https://example.test", session=session
    )

    result = proxy.request("POST", path, b"{}", "application/json")

    assert result.status == 403
    assert json.loads(result.body) == {
        "error": "本机主管理员会话由本地服务管理，不能执行此认证操作"
    }
    assert proxy.connected is False
    assert session.calls == []


@pytest.mark.parametrize(
    "path",
    ["/health", "https://attacker.test/api/members", "/api/../private"],
)
def test_proxy_rejects_non_api_and_unsafe_paths(tmp_path, path):
    vars_path = tmp_path / ".owner.vars"
    write_vars(vars_path)
    proxy = LocalOwnerProxy(
        vars_path, base_url="https://example.test", session=FakeSession([])
    )

    with pytest.raises(ValueError):
        proxy.request("GET", path)


def test_proxy_requires_admin_role_without_exposing_credentials(tmp_path):
    vars_path = tmp_path / ".owner.vars"
    password = "never-show-this-owner-password"
    write_vars(vars_path, username="private-owner", password=password)
    session = FakeSession(
        [
            FakeResponse(
                200,
                payload={
                    "user": {
                        "role": "member",
                        "username": "private-owner",
                        "debug": password,
                    }
                },
            )
        ]
    )
    proxy = LocalOwnerProxy(
        vars_path, base_url="https://example.test", session=session
    )

    with pytest.raises(OwnerConnectionError) as caught:
        proxy.request("GET", "/api/members")

    message = str(caught.value)
    assert "private-owner" not in message
    assert password not in message
    assert proxy.connected is False


def test_proxy_sanitizes_network_errors_and_close_resets_state(tmp_path):
    vars_path = tmp_path / ".owner.vars"
    password = "network-secret-password"
    write_vars(vars_path, password=password)
    session = FakeSession(
        [requests.ConnectionError(f"upstream rejected password={password}")]
    )
    proxy = LocalOwnerProxy(
        vars_path, base_url="https://example.test", session=session
    )

    with pytest.raises(OwnerConnectionError) as caught:
        proxy.connect()

    assert password not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert proxy.connected is False
    proxy.close()
    assert proxy.closed is True
    assert session.closed is True


def test_proxy_sanitizes_remote_request_exception_graph(tmp_path):
    vars_path = tmp_path / ".owner.vars"
    password = "remote-network-secret"
    write_vars(vars_path, password=password)
    session = FakeSession(
        [
            admin_login(),
            requests.ConnectionError(f"request leaked password={password}"),
        ]
    )
    proxy = LocalOwnerProxy(
        vars_path, base_url="https://example.test", session=session
    )

    with pytest.raises(OwnerConnectionError) as caught:
        proxy.request("GET", "/api/members")

    assert password not in str(caught.value)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert proxy.connected is False


def test_close_logs_out_connected_remote_session_before_closing(tmp_path):
    vars_path = tmp_path / ".owner.vars"
    write_vars(vars_path)
    session = FakeSession([admin_login(), FakeResponse(200, payload={"ok": True})])
    proxy = LocalOwnerProxy(
        vars_path, base_url="https://example.test", session=session
    )
    assert proxy.connect() is True

    proxy.close()

    assert proxy.closed is True
    assert proxy.connected is False
    assert session.closed is True
    assert [call[:2] for call in session.calls] == [
        ("POST", "https://example.test/api/auth/login"),
        ("POST", "https://example.test/api/auth/logout"),
    ]
    logout_options = session.calls[1][2]
    assert logout_options["json"] == {}
    assert "Cookie" not in logout_options["headers"]
    with pytest.raises(OwnerConnectionError, match="已关闭"):
        proxy.connect()

    proxy.close()
    assert len(session.calls) == 2


def test_close_silently_closes_when_remote_logout_fails(tmp_path):
    vars_path = tmp_path / ".owner.vars"
    write_vars(vars_path)
    session = FakeSession([admin_login(), requests.ConnectionError("cookie=private")])
    proxy = LocalOwnerProxy(
        vars_path, base_url="https://example.test", session=session
    )
    proxy.connect()

    proxy.close()

    assert proxy.connected is False
    assert session.closed is True


def test_concurrent_requests_share_one_initial_login(tmp_path):
    vars_path = tmp_path / ".owner.vars"
    write_vars(vars_path)

    class BlockingLoginSession:
        def __init__(self):
            self.calls = []
            self.login_started = threading.Event()
            self.release_login = threading.Event()
            self.closed = False
            self._lock = threading.Lock()

        def request(self, method, url, **kwargs):
            with self._lock:
                self.calls.append((method, url, kwargs))
            if url.endswith("/api/auth/login"):
                self.login_started.set()
                assert self.release_login.wait(2)
                return admin_login()
            return FakeResponse(200, body=b'{"favorites":[]}')

        def close(self):
            self.closed = True

    session = BlockingLoginSession()
    proxy = LocalOwnerProxy(
        vars_path, base_url="https://example.test", session=session
    )
    results = []
    errors = []

    def make_request():
        try:
            results.append(proxy.request("GET", "/api/favorites"))
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    first = threading.Thread(target=make_request)
    second = threading.Thread(target=make_request)
    first.start()
    assert session.login_started.wait(2)
    second.start()
    session.release_login.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert len(results) == 2
    assert [url for _, url, _ in session.calls].count(
        "https://example.test/api/auth/login"
    ) == 1
    assert [url for _, url, _ in session.calls].count(
        "https://example.test/api/favorites"
    ) == 2


def test_close_waiting_on_401_prevents_reauthentication(tmp_path):
    vars_path = tmp_path / ".owner.vars"
    write_vars(vars_path)

    class Blocking401Session:
        def __init__(self):
            self.calls = []
            self.request_started = threading.Event()
            self.release_request = threading.Event()
            self.closed = False

        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if url.endswith("/api/auth/login"):
                return admin_login()
            if url.endswith("/api/favorites"):
                self.request_started.set()
                assert self.release_request.wait(2)
                return FakeResponse(401)
            raise AssertionError(f"unexpected request after close: {method} {url}")

        def close(self):
            self.closed = True

    session = Blocking401Session()
    proxy = LocalOwnerProxy(
        vars_path, base_url="https://example.test", session=session
    )
    errors = []

    def request_favorites():
        try:
            proxy.request("GET", "/api/favorites")
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    request_thread = threading.Thread(target=request_favorites)
    request_thread.start()
    assert session.request_started.wait(2)

    close_thread = threading.Thread(target=proxy.close)
    close_thread.start()
    deadline = time.monotonic() + 2
    while not proxy.closed and time.monotonic() < deadline:
        time.sleep(0.001)
    assert proxy.closed is True
    session.release_request.set()
    request_thread.join(timeout=2)
    close_thread.join(timeout=2)

    assert not request_thread.is_alive()
    assert not close_thread.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], OwnerConnectionError)
    assert "已关闭" in str(errors[0])
    assert [call[0] for call in session.calls] == ["POST", "GET"]
    assert session.closed is True
