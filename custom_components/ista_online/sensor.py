"""Sensor platform for the ista Online integration.

MODIFIED from the upstream project by Jeppe Leth (Apache-2.0):
https://github.com/JeppeLeth/hass-ista-online

Changes in this fork:
  - Meter values are coerced to float, including Danish decimal commas
    ("1.234,5"). Upstream passed the raw API string straight through, which
    Home Assistant rejects for a numeric sensor.
  - Device class is inferred from the unit when the meter-type string is not
    one of the four literals upstream recognised, so heat meters reporting
    e.g. "VARME" or "HEAT" still land in the Energy dashboard.
  - State classes corrected. The meter register is `total_increasing` and is
    the correct Energy dashboard source; the period consumption figure is a
    delta, so it carries no state class rather than being mislabelled
    `total_increasing` (which would treat every new period as a meter reset
    and corrupt long-term statistics).
  - Shared behaviour lifted into a base class instead of repeated per sensor.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DIAGNOSTIC_FIELDS = {
    "Activation date": "Activation_date",
    "Deactivation date": "Deactivation_date",
    "Message": "Message",
    "Headline": "Headline",
    "Meter type": "MeterType",
    "Meter code": "METTYPE_CODE",
    "Meter text": "MeterText",
    "Reading date": "Reading_date",
}

USER_INFO_DIAGNOSTIC_FIELDS = {
    "Address Street": "Address",
    "Address Zip": "ZipCity",
}

WATER_METER_TYPES = {"CW", "HW", "KV", "VV", "WATER", "VAND", "KOLDTVAND", "VARMTVAND"}
ENERGY_METER_TYPES = {
    "ENERGY", "ELECTRICITY", "HEAT", "HEATING", "VARME", "FJERNVARME", "EL",
}

WATER_UNITS = {"m³", "L", "ft³", "gal"}
ENERGY_UNITS = {"Wh", "kWh", "MWh", "GJ"}

UNIT_ALIASES = {
    "m3": "m³",
    "m^3": "m³",
    "kwh": "kWh",
    "mwh": "MWh",
    "wh": "Wh",
    "gj": "GJ",
    "l": "L",
}


def _normalize_unit(unit: Any) -> str | None:
    """Map the API's unit spelling onto Home Assistant's."""
    if not isinstance(unit, str):
        return None
    cleaned = unit.strip()
    return UNIT_ALIASES.get(cleaned.lower(), cleaned) or None


def _to_float(value: Any) -> float | None:
    """Coerce an API value to float, tolerating Danish number formatting."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None

    text = value.strip().replace(" ", "").replace(" ", "")
    if not text:
        return None

    if "," in text and "." in text:
        # "1.234,5" -> dot is the thousands separator.
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")

    try:
        return float(text)
    except ValueError:
        _LOGGER.debug("Could not parse %r as a number", value)
        return None


def _map_device_class(meter_type: Any, unit: str | None) -> SensorDeviceClass | None:
    """Infer device class from the meter type, falling back to the unit."""
    normalized = (str(meter_type) if meter_type else "").strip().upper()
    if normalized in WATER_METER_TYPES:
        return SensorDeviceClass.WATER
    if normalized in ENERGY_METER_TYPES:
        return SensorDeviceClass.ENERGY

    # The meter-type vocabulary is not documented, so let the unit decide when
    # the string is unfamiliar. Logged at debug so unknown types can be added.
    if unit in WATER_UNITS:
        _LOGGER.debug("Meter type %r unknown; using unit %s -> water", meter_type, unit)
        return SensorDeviceClass.WATER
    if unit in ENERGY_UNITS:
        _LOGGER.debug("Meter type %r unknown; using unit %s -> energy", meter_type, unit)
        return SensorDeviceClass.ENERGY

    _LOGGER.debug("No device class for meter type %r with unit %r", meter_type, unit)
    return None


def _parse_date_string(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    try:
        if re.match(r"\d{2}-\d{2}-\d{4}$", text):
            return datetime.strptime(text, "%d-%m-%Y").replace(tzinfo=timezone.utc)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        return None


class ISTAMeterEntity(CoordinatorEntity, SensorEntity):
    """Common plumbing: device grouping and refreshing this meter's payload."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, meter: dict, user_info: dict) -> None:
        super().__init__(coordinator)
        self._meter = meter or {}
        self._user_info = user_info or {}
        self._meter_id = self._meter.get("METER_ID")

    @property
    def _serial(self) -> Any:
        return self._meter.get("METER_NO") or self._meter.get("METER_ID")

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._serial))},
            manufacturer="ISTA",
            serial_number=str(self._serial),
            name=f"Meter {self._serial}",
            model=self._meter.get("METCAT_LABEL") or "",
        )

    def _handle_coordinator_update(self) -> None:
        meters = (self.coordinator.data or {}).get("meters") or {}
        for meter in (meters.get("Meters") or {}).get("Value") or []:
            if str(meter.get("METER_ID")) == str(self._meter_id):
                self._meter = meter
                break
        self._user_info = (self.coordinator.data or {}).get("user_info") or {}
        self.async_write_ha_state()


class MeterReadingSensor(ISTAMeterEntity):
    """The meter register. This is the entity to use in the Energy dashboard."""

    _attr_name = "Last meter reading"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, coordinator, meter: dict, user_info: dict) -> None:
        super().__init__(coordinator, meter, user_info)
        self._attr_unique_id = f"ista_meter_{self._serial}_last_meter_reading"

    @property
    def native_value(self) -> float | None:
        return _to_float(self._meter.get("Last_Meter_Reading"))

    @property
    def native_unit_of_measurement(self) -> str | None:
        return _normalize_unit(self._meter.get("Unit"))

    @property
    def device_class(self) -> SensorDeviceClass | None:
        return _map_device_class(
            self._meter.get("MeterType"), self.native_unit_of_measurement
        )

    @property
    def suggested_display_precision(self) -> int | None:
        return 3 if self.native_unit_of_measurement == "m³" else None

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {
            "address": self._user_info.get("Address"),
            "city": self._user_info.get("ZipCity"),
            "room_description": self._meter.get("ROOM_DESCR"),
            "reading_date": self._meter.get("Reading_date"),
        }
        return {key: value for key, value in attrs.items() if value is not None}


class MeterConsumptionSensor(ISTAMeterEntity):
    """Consumption for the last billing period.

    Deliberately carries no state class: this is a per-period delta, not a
    cumulative total, so `total_increasing` would read every new period as a
    meter reset. Use the reading sensor for the Energy dashboard.
    """

    _attr_name = "Last meter consumption"

    def __init__(self, coordinator, meter: dict, user_info: dict) -> None:
        super().__init__(coordinator, meter, user_info)
        self._attr_unique_id = f"ista_meter_{self._serial}_last_meter_consumption"

    @property
    def native_value(self) -> float | None:
        return _to_float(self._meter.get("Last_Meter_Consumption"))

    @property
    def native_unit_of_measurement(self) -> str | None:
        return _normalize_unit(self._meter.get("Unit"))

    @property
    def suggested_display_precision(self) -> int | None:
        return 3 if self.native_unit_of_measurement == "m³" else None

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {
            "address": self._user_info.get("Address"),
            "city": self._user_info.get("ZipCity"),
        }
        return {key: value for key, value in attrs.items() if value is not None}


class MeterDiagnosticSensor(ISTAMeterEntity):
    """A raw field off the meter payload, for troubleshooting."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator, meter: dict, user_info: dict, display_name: str, field_key: str
    ) -> None:
        super().__init__(coordinator, meter, user_info)
        self._field_key = field_key
        self._attr_name = display_name
        self._attr_unique_id = f"ista_meter_{self._serial}_{field_key}"

    @property
    def native_value(self) -> Any:
        value = self._meter.get(self._field_key)
        if self._field_key in ("Reading_date", "Activation_date", "Deactivation_date"):
            parsed = _parse_date_string(value)
            if parsed:
                return parsed.isoformat()
        return value


class UserInfoDiagnosticSensor(ISTAMeterEntity):
    """An account-level field, attached to each meter's device."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator, meter: dict, user_info: dict, display_name: str, field_key: str
    ) -> None:
        super().__init__(coordinator, meter, user_info)
        self._field_key = field_key
        self._attr_name = display_name
        key = field_key.lower().replace(" ", "_")
        self._attr_unique_id = f"ista_meter_{self._serial}_{key}"

    @property
    def native_value(self) -> Any:
        return self._user_info.get(self._field_key)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    """Set up sensors for each meter on the account."""
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not coordinator:
        return

    data = coordinator.data or {}
    meters = ((data.get("meters") or {}).get("Meters") or {}).get("Value") or []
    user_info = data.get("user_info") or {}

    if not meters:
        _LOGGER.warning("ista returned no meters for this account")
        return

    entities: list[SensorEntity] = []
    for meter in meters:
        _LOGGER.debug(
            "Adding ista meter %s (type=%r, unit=%r)",
            meter.get("METER_NO"),
            meter.get("MeterType"),
            meter.get("Unit"),
        )
        entities.append(MeterReadingSensor(coordinator, meter, user_info))
        entities.append(MeterConsumptionSensor(coordinator, meter, user_info))
        for display_name, key in DIAGNOSTIC_FIELDS.items():
            entities.append(
                MeterDiagnosticSensor(coordinator, meter, user_info, display_name, key)
            )
        for display_name, key in USER_INFO_DIAGNOSTIC_FIELDS.items():
            entities.append(
                UserInfoDiagnosticSensor(coordinator, meter, user_info, display_name, key)
            )

    async_add_entities(entities)
