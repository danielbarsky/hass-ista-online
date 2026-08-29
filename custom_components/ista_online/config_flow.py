"""Config flow for the ista Online integration.

MODIFIED from the upstream project by Jeppe Leth (Apache-2.0):
https://github.com/JeppeLeth/hass-ista-online

Changes in this fork:
  - Reauthentication works. Upstream read the entry from `context["entry"]`,
    which current Home Assistant does not set, so the flow always aborted with
    `no_entry` and the integration had to be deleted and re-added.
  - `async_get_options_flow` is a proper static method taking the config entry,
    instead of an instance method that Home Assistant called with the entry
    bound to `self`.
  - The config entry gets a unique ID, so the same account cannot be added twice.
  - The API base URL is overridable, and auth errors are distinguished from
    connection errors in the UI.
"""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import IstaApiError, IstaAuthError, async_fetch_token
from .const import (
    CONF_BASE_URL,
    CONF_COUNTRY,
    COUNTRY_OPTIONS,
    DEFAULT_COUNTRY,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def _async_validate(hass, base_url: str, username: str, password: str) -> str | None:
    """Return None if the credentials work, else an error key for the form."""
    session = async_get_clientsession(hass)
    try:
        await async_fetch_token(session, base_url, username, password)
    except IstaAuthError:
        return "invalid_auth"
    except IstaApiError as err:
        _LOGGER.debug("ista connection check failed: %s", err)
        return "cannot_connect"
    return None


def _credentials_schema(
    *, country: str | None = None, username: str = "", base_url: str | None = None,
    include_country: bool = True,
) -> vol.Schema:
    fields: dict[Any, Any] = {}
    if include_country:
        fields[
            vol.Required(CONF_COUNTRY, default=country or DEFAULT_COUNTRY)
        ] = vol.In(list(COUNTRY_OPTIONS))
    fields[vol.Required("username", default=username)] = str
    fields[vol.Required("password")] = str
    if include_country:
        fields[
            vol.Optional(
                CONF_BASE_URL,
                default=base_url or COUNTRY_OPTIONS[country or DEFAULT_COUNTRY],
            )
        ] = str
    return vol.Schema(fields)


class ISTAConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial setup and reauthentication."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}

        if user_input is not None:
            country = user_input[CONF_COUNTRY]
            username = user_input["username"]
            password = user_input["password"]
            base_url = (user_input.get(CONF_BASE_URL) or "").strip() or COUNTRY_OPTIONS[
                country
            ]

            await self.async_set_unique_id(username.strip().lower())
            self._abort_if_unique_id_configured()

            error = await _async_validate(self.hass, base_url, username, password)
            if error:
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=f"ISTA {username}",
                    data={
                        CONF_COUNTRY: country,
                        CONF_BASE_URL: base_url,
                        "username": username,
                        "password": password,
                    },
                )

        return self.async_show_form(
            step_id="user", data_schema=_credentials_schema(), errors=errors
        )

    def _reauth_entry(self) -> ConfigEntry | None:
        """Resolve the entry being reauthenticated across HA versions."""
        entry_id = self.context.get("entry_id")
        if entry_id:
            return self.hass.config_entries.async_get_entry(entry_id)
        return self.context.get("entry")

    async def async_step_reauth(self, entry_data: dict[str, Any] | None = None) -> Any:
        """Entry point when the coordinator raises ConfigEntryAuthFailed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        entry = self._reauth_entry()
        if entry is None:
            return self.async_abort(reason="reauth_entry_missing")

        errors: dict[str, str] = {}
        stored = dict(entry.data)

        if user_input is not None:
            username = user_input["username"]
            password = user_input["password"]
            country = stored.get(CONF_COUNTRY, DEFAULT_COUNTRY)
            base_url = stored.get(CONF_BASE_URL) or COUNTRY_OPTIONS.get(
                country, COUNTRY_OPTIONS[DEFAULT_COUNTRY]
            )

            error = await _async_validate(self.hass, base_url, username, password)
            if error:
                errors["base"] = error
            else:
                # The update listener registered in async_setup_entry reloads
                # the entry; reloading here as well would race with it.
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **stored,
                        "username": username,
                        "password": password,
                    },
                )
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_credentials_schema(
                username=stored.get("username", ""), include_country=False
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return OptionsFlowHandler()


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Let the user update credentials or the API endpoint after setup."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        entry = self.config_entry
        errors: dict[str, str] = {}
        stored = dict(entry.data)

        if user_input is not None:
            country = user_input[CONF_COUNTRY]
            username = user_input["username"]
            password = user_input["password"]
            base_url = (user_input.get(CONF_BASE_URL) or "").strip() or COUNTRY_OPTIONS[
                country
            ]

            error = await _async_validate(self.hass, base_url, username, password)
            if error:
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        CONF_COUNTRY: country,
                        CONF_BASE_URL: base_url,
                        "username": username,
                        "password": password,
                    },
                )
                return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=_credentials_schema(
                country=stored.get(CONF_COUNTRY),
                username=stored.get("username", ""),
                base_url=stored.get(CONF_BASE_URL),
            ),
            errors=errors,
        )
