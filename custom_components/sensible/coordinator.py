"""Data update coordinator for Sensible."""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_TYPE, DOMAIN
from .modules import MODULES, ModuleError

_LOGGER = logging.getLogger(__name__)


class SensibleCoordinator(DataUpdateCoordinator):
    """Runs one module's fetch on its interval."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self._cfg = {**entry.data, **entry.options}
        self.module = MODULES[self._cfg[CONF_TYPE]]
        self._session = async_get_clientsession(hass)
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(seconds=self.module.interval),
        )

    async def _async_update_data(self) -> dict:
        try:
            return await self.module.fetch(self.hass, self._session, self._cfg)
        except ModuleError as err:
            raise UpdateFailed(str(err)) from err
