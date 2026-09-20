from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Final
from urllib.parse import unquote, urlsplit

import requests


DEFAULT_OWNER_BASE_URL: Final = "https://tradea-3al.pages.dev"
_OWNER_USERNAME_KEY: Final = "TRADEA_OWNER_USERNAME"
_OWNER_PASSWORD_KEY: Final = "TRADEA_OWNER_PASSWORD"
_REQUIRED_KEYS: Final = frozenset({_OWNER_USERNAME_KEY, _OWNER_PASSWORD_KEY})
_BLOCKED_AUTH_PATHS: Final = frozenset(
    {
        "/api/auth/login",
        "/api/auth/setup",
        "/api/auth/logout",
    }
)
_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HTTP_METHOD = re.compile(r"^[A-Z]+$")
_MAX_VARS_FILE_BYTES: Final = 64 * 1024
_MAX_UPSTREAM_HEADER_LENGTH: Final = 4096


class LocalOwnerError(RuntimeError):
    """A safe-to-display local owner proxy error."""


class OwnerConfigurationError(LocalOwnerError):
    """The local owner credential file is missing or malformed."""


class OwnerConnectionError(LocalOwnerError):
    """The remote owner session could not be established or used."""


@dataclass(frozen=True, repr=False)
class OwnerCredentials:
    username: str
    password: str

    def __repr__(self) -> str:
        return "OwnerCredentials(username=<redacted>, password=<redacted>)"


@dataclass(frozen=True)
class LocalOwnerResponse:
    status: int
    body: bytes
    content_type: str
    content_disposition: str | None = None


def _parse_vars_value(raw_value: str, *, line_number: int) -> str:
    value = raw_value.strip()
    if not value:
        return ""
    if value[0] not in {'"', "'"}:
        return value
    if len(value) < 2 or value[-1] != value[0]:
        raise OwnerConfigurationError(
            f"主管理员变量文件第 {line_number} 行的引号不完整"
        )
    # Values are deliberately treated as literals. In particular, this parser never
    # performs shell expansion, interpolation, or escape-sequence evaluation.
    return value[1:-1]


def load_owner_credentials(path: str | Path) -> OwnerCredentials:
    """Read the two owner variables without evaluating the file as shell code."""

    vars_path = Path(path)
    try:
        if not vars_path.is_file():
            raise OwnerConfigurationError("未找到本机主管理员变量文件")
        if vars_path.stat().st_size > _MAX_VARS_FILE_BYTES:
            raise OwnerConfigurationError("本机主管理员变量文件过大")
        text = vars_path.read_text(encoding="utf-8-sig")
    except OwnerConfigurationError:
        raise
    except (OSError, UnicodeError) as exc:
        raise OwnerConfigurationError("无法读取本机主管理员变量文件") from exc

    values: dict[str, str] = {}
    for line_number, original_line in enumerate(text.splitlines(), start=1):
        line = original_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise OwnerConfigurationError(
                f"主管理员变量文件第 {line_number} 行格式无效"
            )
        raw_key, raw_value = line.split("=", 1)
        key = raw_key.strip()
        if not _ENV_KEY.fullmatch(key):
            raise OwnerConfigurationError(
                f"主管理员变量文件第 {line_number} 行变量名无效"
            )
        if key not in _REQUIRED_KEYS:
            continue
        if key in values:
            raise OwnerConfigurationError(f"主管理员变量 {key} 重复定义")
        values[key] = _parse_vars_value(raw_value, line_number=line_number)

    missing = sorted(key for key in _REQUIRED_KEYS if not values.get(key))
    if missing:
        raise OwnerConfigurationError(
            f"本机主管理员变量缺失或为空: {', '.join(missing)}"
        )
    return OwnerCredentials(
        username=values[_OWNER_USERNAME_KEY],
        password=values[_OWNER_PASSWORD_KEY],
    )


def _normalise_base_url(base_url: str) -> str:
    parsed = urlsplit(str(base_url).strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("主管理员服务地址无效")
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def _normalise_api_path(path: str) -> tuple[str, str]:
    raw_path = str(path)
    parsed = urlsplit(raw_path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise ValueError("只允许代理本服务的 /api/* 路径")
    decoded_path = unquote(parsed.path)
    if (
        not decoded_path.startswith("/api/")
        or "\\" in decoded_path
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded_path)
        or any(part in {".", ".."} for part in decoded_path.split("/"))
    ):
        raise ValueError("只允许代理本服务的 /api/* 路径")
    canonical_path = decoded_path.rstrip("/") or "/"
    remote_path = parsed.path
    if parsed.query:
        remote_path = f"{remote_path}?{parsed.query}"
    return canonical_path, remote_path


def _blocked_auth_response() -> LocalOwnerResponse:
    body = json.dumps(
        {"error": "本机主管理员会话由本地服务管理，不能执行此认证操作"},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return LocalOwnerResponse(
        status=403,
        body=body,
        content_type="application/json; charset=utf-8",
    )


def _safe_upstream_header(value: object, *, fallback: str | None) -> str | None:
    if not isinstance(value, str) or not value:
        return fallback
    if len(value) > _MAX_UPSTREAM_HEADER_LENGTH or any(
        ord(character) < 32 or ord(character) == 127 for character in value
    ):
        return fallback
    return value


class LocalOwnerProxy:
    """Authenticate the local report server as the configured cloud administrator.

    ``requests.Session`` and all authentication state are kept behind one re-entrant
    lock. Credentials and cookies are never included in returned values or errors.
    """

    def __init__(
        self,
        vars_path: str | Path,
        *,
        base_url: str = DEFAULT_OWNER_BASE_URL,
        session: requests.Session | None = None,
        timeout: float = 15.0,
    ) -> None:
        if timeout <= 0:
            raise ValueError("主管理员服务超时时间必须大于零")
        self._credentials = load_owner_credentials(vars_path)
        self._base_url = _normalise_base_url(base_url)
        self._session = session or requests.Session()
        self._timeout = float(timeout)
        self._lock = threading.RLock()
        self._closed = threading.Event()
        self._session_closed = False
        self._connected = False

    @property
    def connected(self) -> bool:
        if self._closed.is_set():
            return False
        with self._lock:
            return self._connected and not self._closed.is_set()

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    def _ensure_open(self) -> None:
        if self._closed.is_set():
            raise OwnerConnectionError("本机主管理员代理已关闭")

    def connect(self) -> bool:
        """Establish and validate the owner session immediately."""

        self._ensure_open()
        with self._lock:
            self._ensure_open()
            self._login_locked(force=False)
            return self._connected

    def close(self) -> None:
        # Publish the terminal state before waiting for an in-flight request. This
        # prevents a request that receives HTTP 401 while close() is waiting from
        # starting a fresh login.
        self._closed.set()
        with self._lock:
            if self._session_closed:
                return
            try:
                if self._connected:
                    try:
                        self._session.request(
                            "POST",
                            f"{self._base_url}/api/auth/logout",
                            json={},
                            headers={"Accept": "application/json"},
                            timeout=self._timeout,
                            allow_redirects=False,
                        )
                    except Exception:
                        # Shutdown must continue and must not log exception details,
                        # which can include request or cookie information.
                        pass
            finally:
                self._connected = False
                self._session.close()
                self._session_closed = True

    def _login_locked(self, *, force: bool) -> None:
        self._ensure_open()
        if self._connected and not force:
            return
        self._connected = False
        response: requests.Response | None = None
        try:
            response = self._session.request(
                "POST",
                f"{self._base_url}/api/auth/login",
                json={
                    "username": self._credentials.username,
                    "password": self._credentials.password,
                },
                headers={"Accept": "application/json"},
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            # Raise after leaving the except block so neither __context__ nor
            # __cause__ retains a third-party exception that may contain request
            # details or credentials.
            pass
        if response is None:
            raise OwnerConnectionError("无法连接主管理员服务")
        self._ensure_open()
        if response.status_code != 200:
            raise OwnerConnectionError(
                f"主管理员登录失败（HTTP {int(response.status_code)}）"
            )
        try:
            payload = response.json()
        except (TypeError, ValueError):
            raise OwnerConnectionError("主管理员登录响应格式无效") from None
        user = payload.get("user") if isinstance(payload, dict) else None
        if not isinstance(user, dict) or user.get("role") != "admin":
            raise OwnerConnectionError("远端账号未通过管理员身份验证")
        self._connected = True

    def _remote_request_locked(
        self,
        method: str,
        remote_path: str,
        body: bytes | str | None,
        content_type: str | None,
    ) -> requests.Response:
        self._ensure_open()
        headers = {"Accept": "application/json, */*;q=0.8"}
        if content_type:
            if "\r" in content_type or "\n" in content_type:
                raise ValueError("请求内容类型无效")
            headers["Content-Type"] = content_type
        response: requests.Response | None = None
        try:
            response = self._session.request(
                method,
                f"{self._base_url}{remote_path}",
                data=body,
                headers=headers,
                timeout=self._timeout,
                allow_redirects=False,
            )
        except requests.RequestException:
            self._connected = False
        if response is None:
            # As in login, conversion happens outside the except block so the
            # original requests exception cannot remain reachable from the safe
            # application error.
            raise OwnerConnectionError("主管理员服务请求失败")
        return response

    def request(
        self,
        method: str,
        path: str,
        body: bytes | str | None = None,
        content_type: str | None = None,
    ) -> LocalOwnerResponse:
        """Proxy one API request, retrying authentication once after HTTP 401."""

        self._ensure_open()
        normalized_method = str(method).strip().upper()
        if not _HTTP_METHOD.fullmatch(normalized_method):
            raise ValueError("HTTP 请求方法无效")
        canonical_path, remote_path = _normalise_api_path(path)
        if canonical_path in _BLOCKED_AUTH_PATHS:
            return _blocked_auth_response()

        with self._lock:
            self._ensure_open()
            self._login_locked(force=False)
            response = self._remote_request_locked(
                normalized_method,
                remote_path,
                body,
                content_type,
            )
            if response.status_code == 401:
                self._connected = False
                self._login_locked(force=True)
                response = self._remote_request_locked(
                    normalized_method,
                    remote_path,
                    body,
                    content_type,
                )
            if response.status_code == 401:
                self._connected = False
            content_type = _safe_upstream_header(
                response.headers.get("Content-Type"),
                fallback="application/octet-stream",
            )
            content_disposition = _safe_upstream_header(
                response.headers.get("Content-Disposition"),
                fallback=None,
            )
            return LocalOwnerResponse(
                status=int(response.status_code),
                body=bytes(response.content),
                content_type=content_type or "application/octet-stream",
                content_disposition=content_disposition,
            )
