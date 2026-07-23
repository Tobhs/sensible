"""The Sensible integration."""

from __future__ import annotations

import logging
import os

import homeassistant.helpers.config_validation as cv
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import SensibleCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS: list[Platform] = [Platform.SENSOR]

CARD_URL = "/sensible/sensible-card.js"
CARD_FILENAME = "sensible-card.js"


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Serve the bundled Lovelace card and register it as a frontend resource."""
    card_path = os.path.join(os.path.dirname(__file__), "lovelace", CARD_FILENAME)
    try:
        from homeassistant.components.http import StaticPathConfig

        await hass.http.async_register_static_paths(
            [StaticPathConfig(CARD_URL, card_path, False)]
        )
    except (ImportError, AttributeError):  # pragma: no cover - old cores
        hass.http.register_static_path(CARD_URL, card_path, False)

    try:
        lovelace = hass.data.get("lovelace")
        resources = getattr(lovelace, "resources", None)
        if resources is None and isinstance(lovelace, dict):
            resources = lovelace.get("resources")
        if resources is None:
            return True
        if not resources.loaded:
            await resources.async_load()
            resources.loaded = True
        versioned = f"{CARD_URL}?v=1.0.0"
        if not any(
            (item.get("url") or "").split("?")[0] == CARD_URL
            for item in resources.async_items()
        ):
            await resources.async_create_item({"res_type": "module", "url": versioned})
    except Exception as err:  # noqa: BLE001 - never block setup on this
        _LOGGER.debug("Could not auto-register card resource (%s)", err)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up a Sensible sensor from a config entry."""
    coordinator = SensibleCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
