"""Async API client for the ista Denmark portal.

MODIFIED from the upstream project by Jeppe Leth (Apache-2.0):
https://github.com/JeppeLeth/hass-ista-online

Changes in this fork:
  - Rewritten on aiohttp instead of blocking `requests`, removing the need for
    executor jobs and the undeclared/unpinned third-party dependency.
  - Errors raise typed exceptions carrying the HTTP status, so callers can tell
    an expired token (401) apart from bad credentials and transport failures.
  - Token expiry is computed once, here, so the coordinator can cache it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from .const import REQUEST_TIMEOUT_SECONDS

# ista's backend intermittently answers with an ASP.NET "Runtime Error"
# page instead of JSON. Observed twice in two days, clearing on its own
# both times, so a couple of retries turns an outage into a hiccup.
RETRY_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = (1.0, 3.0)

_LOGGER = logging.getLogger(__name__)


class IstaApiError(Exception):
    """Any failure talking to the ista API."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class IstaAuthError(IstaApiError):
    """Credentials were rejected, or the token is no longer valid."""


def _summarize_body(text: str, limit: int = 180) -> str:
    """Condense an error body for logging - HTML error pages are enormous."""
    stripped = text.strip()
    if stripped[:200].lstrip().lower().startswith(("<!doctype", "<html")):
        title = re.search(r"<title[^>]*>(.*?)</title>", stripped, re.S | re.I)
        label = title.group(1).strip() if title else "HTML page"
        # Drop style/script contents first, or the summary is mostly CSS.
        body = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", stripped, flags=re.S | re.I)
        body = re.sub(r"<[^>]+>", " ", body)
        body = " ".join(body.split())
        return f"{label}: {body[:limit]}"
    return " ".join(stripped.split())[:limit]


def _parse_utc_z(dt_str: Any) -> datetime | None:
    """Parse ista's `.issued`/`.expires` format, e.g. '2025-08-01 10:00:00Z'."""
    if not dt_str or not isinstance(dt_str, str):
        return None
    try:
        return datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


class TokenSuccess:
    """A bearer token plus the account metadata returned alongside it."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.raw = data
        self.access_token: str = data.get("access_token") or ""
        self.token_type: str = (data.get("token_type") or "bearer").lower()

        expires_in: int | None = None
        raw_expires_in = data.get("expires_in")
        if isinstance(raw_expires_in, int):
            expires_in = raw_expires_in
        elif isinstance(raw_expires_in, str):
            try:
                expires_in = int(raw_expires_in)
            except ValueError:
                expires_in = None
        self.expires_in = expires_in

        self.issued_at = _parse_utc_z(data.get(".issued"))
        # Prefer the server's absolute expiry; fall back to a relative lifetime.
        self.expires_at = _parse_utc_z(data.get(".expires"))
        if self.expires_at is None and expires_in is not None:
            self.expires_at = datetime.now(timezone.utc) + timedelta(
                seconds=expires_in
            )

        self.first_name = data.get("FirstName")
        self.username = data.get("Username")
        self.language = data.get("Language")

    def auth_header(self) -> str:
        return f"{self.token_type} {self.access_token}"

    def is_valid(self, margin_seconds: int = 0) -> bool:
        """True if the token is still usable `margin_seconds` from now."""
        if not self.access_token:
            return False
        if self.expires_at is None:
            # No expiry information: treat as single-use rather than cache it
            # forever and risk a wedged integration.
            return False
        return datetime.now(timezone.utc) + timedelta(
            seconds=margin_seconds
        ) < self.expires_at


async def _request_json(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """Issue a request and return (status, decoded JSON), raising typed errors.

    Retries transport failures, timeouts, 5xx responses and non-JSON bodies.
    A 4xx is definitive and is never retried: 401 means the token is stale and
    the coordinator handles it, 400 means the credentials are wrong.

    Every error names the URL, because "response was not JSON" is useless
    without knowing which of three endpoints produced it.
    """
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    last_error: IstaApiError | None = None

    for attempt in range(RETRY_ATTEMPTS):
        if attempt:
            delay = RETRY_BACKOFF_SECONDS[min(attempt - 1, len(RETRY_BACKOFF_SECONDS) - 1)]
            _LOGGER.debug(
                "Retrying %s in %.0fs (attempt %d/%d): %s",
                url, delay, attempt + 1, RETRY_ATTEMPTS, last_error,
            )
            await asyncio.sleep(delay)

        try:
            async with session.request(
                method, url, headers=headers, data=data, timeout=timeout
            ) as resp:
                status = resp.status
                text = await resp.text()
        except aiohttp.ClientError as err:
            last_error = IstaApiError(f"{url}: request failed: {err}")
            continue
        except TimeoutError:
            last_error = IstaApiError(f"{url}: request timed out")
            continue

        # 401 is definitive - the coordinator refreshes the token and retries.
        if status == 401:
            raise IstaAuthError(f"{url}: unauthorized", status)

        try:
            payload = json.loads(text) if text else None
        except ValueError:
            last_error = IstaApiError(
                f"{url}: HTTP {status} was not JSON ({_summarize_body(text)})", status
            )
            if 400 <= status < 500:
                raise last_error
            continue

        if status >= 500:
            last_error = IstaApiError(
                f"{url}: HTTP {status} ({_summarize_body(text)})", status
            )
            continue

        return status, payload

    assert last_error is not None
    raise last_error


async def async_fetch_token(
    session: aiohttp.ClientSession, base_url: str, username: str, password: str
) -> TokenSuccess:
    """Exchange username/password for a bearer token (OAuth2 password grant)."""
    url = f"{base_url.rstrip('/')}/token"
    status, payload = await _request_json(
        session,
        "POST",
        url,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "password",
            "username": username,
            "password": password,
        },
    )

    if not isinstance(payload, dict):
        raise IstaApiError(f"{url}: unexpected token response shape", status)

    if "error" in payload:
        error = payload.get("error")
        description = payload.get("error_description") or error
        if error == "invalid_grant":
            raise IstaAuthError(f"Authentication failed: {description}", status)
        raise IstaApiError(f"Token error: {description}", status)

    token = TokenSuccess(payload)
    if not token.access_token:
        raise IstaApiError(f"{url}: token response contained no access_token", status)
    return token


async def _async_get(
    session: aiohttp.ClientSession, base_url: str, path: str, bearer: str
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    status, payload = await _request_json(
        session, "GET", url, headers={"Authorization": bearer}
    )

    if status != 200:
        raise IstaApiError(f"HTTP {status} from {path}", status)
    if not isinstance(payload, dict):
        raise IstaApiError(f"Unexpected payload shape from {path}", status)
    return payload


async def async_fetch_user_info(
    session: aiohttp.ClientSession, base_url: str, bearer: str
) -> dict[str, Any]:
    """Fetch the account holder's details (name, address)."""
    return await _async_get(session, base_url, "/api/GetUserInfo", bearer)


async def async_fetch_meters(
    session: aiohttp.ClientSession, base_url: str, bearer: str
) -> dict[str, Any]:
    """Fetch the meter list with the latest readings."""
    data = await _async_get(session, base_url, "/api/Meters", bearer)

    err_msg = data.get("errorMessage") or {}
    if isinstance(err_msg, dict):
        parts = [
            f"{key}: {err_msg[key]}"
            for key in ("ErrorType", "UserMessage", "InternalMessage")
            if err_msg.get(key)
        ]
        if parts:
            raise IstaApiError("; ".join(parts))

    return data
