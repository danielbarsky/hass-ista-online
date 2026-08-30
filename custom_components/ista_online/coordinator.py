"""Update coordinator for the ista Online integration.

MODIFIED from the upstream project by Jeppe Leth (Apache-2.0):
https://github.com/JeppeLeth/hass-ista-online

Changes in this fork:
  - The bearer token is cached and reused until shortly before it expires.
    Upstream re-sent the username and password on every single poll; this
    reduces credential transmission to roughly once per token lifetime.
  - A 401 mid-poll invalidates the cached token and retries once, so an
    early server-side expiry self-heals instead of failing the update.
  - Fully async; no executor jobs.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import (
    IstaApiError,
    IstaAuthError,
    TokenSuccess,
    async_fetch_meters,
    async_fetch_token,
    async_fetch_user_info,
)
from .const import TOKEN_EXPIRY_MARGIN_SECONDS, UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class ISTACoordinator(DataUpdateCoordinator):
    """Polls the ista portal and caches the bearer token between updates."""

    def __init__(
        self, hass: HomeAssistant, base_url: str, username: str, password: str
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="ISTA Online",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.base_url = base_url
        self.username = username
        self.password = password
        self.user_info: dict[str, Any] = {}
        self.meters: dict[str, Any] = {}
        self._token: TokenSuccess | None = None

    async def _async_get_bearer(self, force_refresh: bool = False) -> str:
        """Return a valid Authorization header, refreshing the token if needed."""
        if (
            not force_refresh
            and self._token is not None
            and self._token.is_valid(TOKEN_EXPIRY_MARGIN_SECONDS)
        ):
            return self._token.auth_header()

        session = async_get_clientsession(self.hass)
        self._token = await async_fetch_token(
            session, self.base_url, self.username, self.password
        )
        _LOGGER.debug(
            "Obtained new ista token, expires at %s", self._token.expires_at
        )
        return self._token.auth_header()

    async def _async_update_data(self) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)

        try:
            bearer = await self._async_get_bearer()

            try:
                user_info = await async_fetch_user_info(session, self.base_url, bearer)
                meters = await async_fetch_meters(session, self.base_url, bearer)
            except IstaAuthError:
                # Token rejected earlier than advertised. Refresh once and retry;
                # if the credentials themselves are bad, async_fetch_token raises
                # IstaAuthError again and we surface it as a reauth prompt.
                _LOGGER.debug("ista token rejected mid-poll, refreshing and retrying")
                self._token = None
                bearer = await self._async_get_bearer(force_refresh=True)
                user_info = await async_fetch_user_info(session, self.base_url, bearer)
                meters = await async_fetch_meters(session, self.base_url, bearer)

        except IstaAuthError as err:
            self._token = None
            raise ConfigEntryAuthFailed(str(err)) from err
        except IstaApiError as err:
            raise UpdateFailed(str(err)) from err
        except Exception as err:  # noqa: BLE001 - coordinator must not leak
            raise UpdateFailed(f"Unexpected error fetching ISTA data: {err}") from err

        self.user_info = user_info
        self.meters = meters
        return {
            "token": self._token,
            "user_info": user_info,
            "meters": meters,
        }
