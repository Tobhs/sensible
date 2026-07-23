"""Module registry for Sensible.

Each module is a small, self-contained provider: it declares its config fields
(for the config flow) and an async ``fetch`` that returns a dict with the sensor
``state`` and ``attributes`` (plus optional ``unit``, ``icon``, ``picture``).

All data sources here are free and need no API key (NASA's picture of the day
uses a shared demo key by default; users can add their own for higher limits).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo, available_timezones

import aiohttp
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .const import (
    CONF_API_KEY,
    CONF_BASE,
    CONF_COUNTRY,
    CONF_LANGUAGE,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_TARGET,
    CONF_TIMEZONE,
    CONF_URL,
    CONF_VALUE_PATH,
    MAX_TEXT_LEN,
    REQUEST_TIMEOUT,
    TYPE_CURRENCY,
    TYPE_DAILY_IMAGE,
    TYPE_FUN_FACT,
    TYPE_HOLIDAYS,
    TYPE_PAW_SAFETY,
    TYPE_REST,
    TYPE_WORLD_CLOCK,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)


class ModuleError(Exception):
    """A recoverable problem fetching a module's data."""


def _clean(value: Any, max_len: int = MAX_TEXT_LEN) -> str | None:
    if value is None:
        return None
    text = "".join(ch for ch in str(value) if ch.isprintable() and ch not in "<>").strip()
    return text[:max_len] if text else None


def _https(value: Any) -> str | None:
    if isinstance(value, str) and value.startswith("https://") and len(value) <= 500:
        return value
    return None


async def _get_json(
    session: aiohttp.ClientSession, url: str, params: dict | None = None
) -> Any:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        async with asyncio.timeout(REQUEST_TIMEOUT):
            resp = await session.get(url, params=params, headers=headers)
            if resp.status != 200:
                raise ModuleError(f"{url} returned HTTP {resp.status}")
            return await resp.json(content_type=None)
    except (aiohttp.ClientError, asyncio.TimeoutError) as err:
        raise ModuleError(f"Request failed: {err}") from err


def _extract_path(data: Any, path: str) -> Any:
    """Walk a dotted path like ``rates.USD`` or ``data.0.name``."""
    if not path:
        return data
    current = data
    for part in path.split("."):
        if isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


# --------------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------------

async def _fetch_world_clock(hass, session, cfg) -> dict:
    tzname = cfg.get(CONF_TIMEZONE) or hass.config.time_zone or "UTC"
    try:
        tz = ZoneInfo(tzname)
    except Exception as err:  # noqa: BLE001
        raise ModuleError(f"Unknown timezone: {tzname}") from err
    now = datetime.now(tz)
    return {
        "state": now.strftime("%H:%M"),
        "attributes": {
            "timezone": tzname,
            "date": now.strftime("%Y-%m-%d"),
            "weekday": now.strftime("%A"),
            "utc_offset": now.strftime("%z"),
            "iso": now.isoformat(timespec="minutes"),
        },
        "icon": "mdi:clock-time-four-outline",
    }


async def _fetch_paw_safety(hass, session, cfg) -> dict:
    lat = cfg.get(CONF_LATITUDE, hass.config.latitude)
    lon = cfg.get(CONF_LONGITUDE, hass.config.longitude)
    data = await _get_json(
        session,
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": (
                "temperature_2m,apparent_temperature,soil_temperature_0cm,"
                "uv_index,shortwave_radiation,snowfall,is_day"
            ),
            "timezone": "auto",
        },
    )
    cur = data.get("current") if isinstance(data, dict) else None
    if not isinstance(cur, dict):
        raise ModuleError("No weather data")

    air = cur.get("temperature_2m")
    ground = cur.get("soil_temperature_0cm")
    rad = cur.get("shortwave_radiation") or 0
    uv = cur.get("uv_index")
    snow = cur.get("snowfall") or 0
    feels = cur.get("apparent_temperature")

    # Estimate pavement (asphalt) temperature: it runs hotter than air in sun.
    candidates = [v for v in (ground,) if isinstance(v, (int, float))]
    if isinstance(air, (int, float)):
        # Solar bump above air temperature, capped so extreme radiation
        # readings can't produce absurd pavement estimates.
        candidates.append(air + min(0.045 * float(rad), 35.0))
    pavement = round(max(candidates), 1) if candidates else None

    if not isinstance(air, (int, float)):
        verdict, reason = "Unknown", "No temperature data available"
    elif pavement is not None and pavement >= 50:
        verdict = "Too hot for paws"
        reason = f"Pavement about {pavement} C, risk of burnt paws. Walk on grass or wait."
    elif pavement is not None and pavement >= 40:
        verdict = "Warm, take care"
        reason = f"Pavement about {pavement} C. Prefer grass, or go early or late."
    elif air <= -5 or (snow and air <= 0):
        verdict = "Cold, protect paws"
        reason = "Freezing with snow or salt likely. Booties help; rinse paws after."
    elif air <= 0:
        verdict = "Chilly, watch for ice"
        reason = "Around freezing. Watch for ice and road salt."
    else:
        verdict = "Good to go"
        reason = "Comfortable conditions for a walk."

    return {
        "state": verdict,
        "attributes": {
            "air_temp_c": air,
            "ground_temp_c": ground,
            "estimated_pavement_c": pavement,
            "feels_like_c": feels,
            "uv_index": uv,
            "snowfall_cm": snow,
            "reason": reason,
        },
        "icon": "mdi:paw",
    }


async def _fetch_fun_fact(hass, session, cfg) -> dict:
    lang = cfg.get(CONF_LANGUAGE) or "en"
    data = await _get_json(
        session,
        "https://uselessfacts.jsph.pl/api/v2/facts/random",
        params={"language": lang},
    )
    text = _clean(data.get("text")) if isinstance(data, dict) else None
    if not text:
        raise ModuleError("No fact returned")
    return {
        "state": text,
        "attributes": {
            "language": lang,
            "source": _https(data.get("source_url")),
            "permalink": _https(data.get("permalink")),
        },
        "icon": "mdi:lightbulb-on-outline",
    }


async def _fetch_daily_image(hass, session, cfg) -> dict:
    key = (cfg.get(CONF_API_KEY) or "").strip() or "DEMO_KEY"
    data = await _get_json(
        session,
        "https://api.nasa.gov/planetary/apod",
        params={"api_key": key, "thumbs": "true"},
    )
    if not isinstance(data, dict):
        raise ModuleError("No image data")
    media = data.get("media_type")
    image = _https(data.get("hdurl")) or _https(data.get("url"))
    picture = image if media == "image" else _https(data.get("thumbnail_url"))
    return {
        "state": _clean(data.get("title")),
        "attributes": {
            "explanation": _clean(data.get("explanation"), 1500),
            "date": _clean(data.get("date"), 10),
            "copyright": _clean(data.get("copyright"), 100),
            "url": _https(data.get("url")),
            "media_type": media,
        },
        "picture": picture,
        "icon": "mdi:telescope",
    }


async def _fetch_currency(hass, session, cfg) -> dict:
    base = (cfg.get(CONF_BASE) or "EUR").upper()[:3]
    target = (cfg.get(CONF_TARGET) or "USD").upper()[:3]
    data = await _get_json(
        session, "https://api.frankfurter.dev/v1/latest",
        params={"base": base, "symbols": target},
    )
    rate = (data.get("rates") or {}).get(target) if isinstance(data, dict) else None
    if not isinstance(rate, (int, float)):
        raise ModuleError(f"No rate for {base} to {target}")
    return {
        "state": round(rate, 4),
        "unit": target,
        "attributes": {
            "base_currency": base,
            "target_currency": target,
            "date": _clean(data.get("date"), 10),
        },
        "icon": "mdi:cash-multiple",
    }


async def _fetch_holidays(hass, session, cfg) -> dict:
    country = (cfg.get(CONF_COUNTRY) or hass.config.country or "US").upper()[:2]
    data = await _get_json(
        session, f"https://date.nager.at/api/v3/NextPublicHolidays/{country}"
    )
    if not isinstance(data, list) or not data:
        raise ModuleError(f"No holidays for {country}")
    nxt = data[0]
    d = _clean(nxt.get("date"), 10)
    days_until = None
    try:
        days_until = (date.fromisoformat(d) - date.today()).days
    except (TypeError, ValueError):
        pass
    return {
        "state": _clean(nxt.get("localName") or nxt.get("name")),
        "attributes": {
            "date": d,
            "days_until": days_until,
            "english_name": _clean(nxt.get("name")),
            "country": country,
            "upcoming": [
                {"date": _clean(h.get("date"), 10), "name": _clean(h.get("localName"))}
                for h in data[:5]
                if isinstance(h, dict)
            ],
        },
        "icon": "mdi:calendar-star",
    }


async def _fetch_rest(hass, session, cfg) -> dict:
    url = cfg.get(CONF_URL) or ""
    path = cfg.get(CONF_VALUE_PATH) or ""
    if not url.startswith(("http://", "https://")):
        raise ModuleError("URL must start with http:// or https://")
    data = await _get_json(session, url)
    value = _extract_path(data, path)
    if isinstance(value, (dict, list)):
        raise ModuleError("Path did not point at a single value")
    return {
        "state": _clean(value) if value is not None else None,
        "attributes": {"source": _clean(url, 500), "value_path": _clean(path, 200)},
        "icon": "mdi:api",
    }


# --------------------------------------------------------------------------
# Schema builders (fields shown on the configure step for each module)
# --------------------------------------------------------------------------

def _schema_world_clock(hass, d) -> dict:
    zones = sorted(available_timezones())
    return {
        vol.Required(
            CONF_TIMEZONE,
            default=d.get(CONF_TIMEZONE) or hass.config.time_zone or "UTC",
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=zones, mode=selector.SelectSelectorMode.DROPDOWN,
                custom_value=True,
            )
        )
    }


def _schema_location(hass, d) -> dict:
    return {
        vol.Required(
            CONF_LATITUDE, default=d.get(CONF_LATITUDE, hass.config.latitude)
        ): vol.Coerce(float),
        vol.Required(
            CONF_LONGITUDE, default=d.get(CONF_LONGITUDE, hass.config.longitude)
        ): vol.Coerce(float),
    }


def _schema_fun_fact(hass, d) -> dict:
    return {
        vol.Required(CONF_LANGUAGE, default=d.get(CONF_LANGUAGE, "en")): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=["en", "de"], mode=selector.SelectSelectorMode.DROPDOWN
            )
        )
    }


def _schema_daily_image(hass, d) -> dict:
    return {vol.Optional(CONF_API_KEY, default=d.get(CONF_API_KEY, "")): str}


def _schema_currency(hass, d) -> dict:
    return {
        vol.Required(CONF_BASE, default=d.get(CONF_BASE, "EUR")): str,
        vol.Required(CONF_TARGET, default=d.get(CONF_TARGET, "USD")): str,
    }


def _schema_holidays(hass, d) -> dict:
    default = d.get(CONF_COUNTRY) or (hass.config.country or "US")
    return {vol.Required(CONF_COUNTRY, default=default): str}


def _schema_rest(hass, d) -> dict:
    return {
        vol.Required(CONF_URL, default=d.get(CONF_URL, "")): str,
        vol.Optional(CONF_VALUE_PATH, default=d.get(CONF_VALUE_PATH, "")): str,
    }


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

@dataclass
class Module:
    key: str
    name: str
    icon: str
    interval: int  # seconds between updates
    build_schema: Callable[[HomeAssistant, dict], dict]
    fetch: Callable[..., Awaitable[dict]]


MODULES: dict[str, Module] = {
    TYPE_WORLD_CLOCK: Module(
        TYPE_WORLD_CLOCK, "World clock", "mdi:clock-time-four-outline", 60,
        _schema_world_clock, _fetch_world_clock,
    ),
    TYPE_PAW_SAFETY: Module(
        TYPE_PAW_SAFETY, "Dog paw safety (weather index)", "mdi:paw", 1800,
        _schema_location, _fetch_paw_safety,
    ),
    TYPE_FUN_FACT: Module(
        TYPE_FUN_FACT, "Fun fact", "mdi:lightbulb-on-outline", 21600,
        _schema_fun_fact, _fetch_fun_fact,
    ),
    TYPE_DAILY_IMAGE: Module(
        TYPE_DAILY_IMAGE, "Daily image (NASA)", "mdi:telescope", 3600,
        _schema_daily_image, _fetch_daily_image,
    ),
    TYPE_CURRENCY: Module(
        TYPE_CURRENCY, "Currency exchange rate", "mdi:cash-multiple", 3600,
        _schema_currency, _fetch_currency,
    ),
    TYPE_HOLIDAYS: Module(
        TYPE_HOLIDAYS, "Next public holiday", "mdi:calendar-star", 43200,
        _schema_holidays, _fetch_holidays,
    ),
    TYPE_REST: Module(
        TYPE_REST, "Bring your own API (JSON)", "mdi:api", 1800,
        _schema_rest, _fetch_rest,
    ),
}


def module_options() -> list[dict[str, str]]:
    """Options for the type selector, in a stable order."""
    return [{"value": key, "label": mod.name} for key, mod in MODULES.items()]
