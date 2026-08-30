"""The ista Online integration.

MODIFIED from the upstream project by Jeppe Leth (Apache-2.0):
https://github.com/JeppeLeth/hass-ista-online

Changes in this fork: the base URL may be overridden per config entry, and
entries reload automatically when their options change.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_BASE_URL, CONF_COUNTRY, COUNTRY_OPTIONS, DOMAIN, PLATFORMS
from .coordinator import ISTACoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up ista Online from a config entry."""
    country = entry.data.get(CONF_COUNTRY)
    username = entry.data.get("username")
    password = entry.data.get("password")

    # An explicit base URL wins, so a moved endpoint needs no code change.
    base_url = entry.data.get(CONF_BASE_URL) or COUNTRY_OPTIONS.get(country)
    if not base_url:
        _LOGGER.error("Unknown country selection: %s", country)
        return False

    coordinator = ISTACoordinator(hass, base_url, username, password)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its credentials or endpoint change."""
    await hass.config_entries.async_reload(entry.entry_id)
