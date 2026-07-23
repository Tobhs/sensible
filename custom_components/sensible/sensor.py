"""Sensor platform for Sensible."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_TYPE, DOMAIN
from .coordinator import SensibleCoordinator
from .modules import MODULES


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: SensibleCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([SensibleSensor(coordinator, entry)])


class SensibleSensor(CoordinatorEntity[SensibleCoordinator], SensorEntity):
    """One sensor whose value comes from the entry's module."""

    _attr_has_entity_name = True
    _attr_name = None  # takes the device (entry) name

    def __init__(
        self, coordinator: SensibleCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._module = MODULES[entry.data[CONF_TYPE]]
        self._attr_unique_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Sensible",
            model=self._module.name,
        )

    @property
    def _data(self) -> dict:
        return self.coordinator.data or {}

    @property
    def native_value(self):
        return self._data.get("state")

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._data.get("unit")

    @property
    def icon(self) -> str | None:
        return self._data.get("icon") or self._module.icon

    @property
    def entity_picture(self) -> str | None:
        return self._data.get("picture")

    @property
    def extra_state_attributes(self) -> dict:
        return self._data.get("attributes") or {}
