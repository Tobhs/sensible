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
import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo, available_timezones

import aiohttp
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import selector

from .const import (
    CONF_API_KEY,
    CONF_BASE,
    CONF_BEDTIME,
    CONF_COUNTRY,
    CONF_LANGUAGE,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_TARGET,
    CONF_TIMEZONE,
    CONF_WAKE,
    CONF_WORK,
    MAX_TEXT_LEN,
    REQUEST_TIMEOUT,
    TYPE_AIR_QUALITY,
    TYPE_CURRENCY,
    TYPE_DAILY_IMAGE,
    TYPE_FUN_FACT,
    TYPE_HOLIDAYS,
    TYPE_MOON,
    TYPE_PAW_SAFETY,
    TYPE_SUN,
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


def _lvl_air(air) -> str | None:
    if not isinstance(air, (int, float)):
        return None
    if air < 0 or air > 32:
        return "bad"
    if air < 5 or air > 27:
        return "warn"
    return "good"


def _lvl_pavement(p) -> str | None:
    if p is None:
        return None
    if p >= 50:
        return "bad"
    if p >= 40:
        return "warn"
    return "good"


def _lvl_uv(uv) -> str | None:
    if not isinstance(uv, (int, float)):
        return None
    if uv >= 8:
        return "bad"
    if uv >= 3:
        return "warn"
    return "good"


def _until(now_dt: datetime, target_t: time) -> str:
    target = now_dt.replace(
        hour=target_t.hour, minute=target_t.minute, second=0, microsecond=0
    )
    if target <= now_dt:
        target += timedelta(days=1)
    mins = int((target - now_dt).total_seconds() // 60)
    return f"{mins // 60}h {mins % 60}m"


# --------------------------------------------------------------------------
# Fetchers
# --------------------------------------------------------------------------

def _parse_hhmm(value) -> time | None:
    if not value:
        return None
    try:
        parts = str(value).split(":")
        return time(int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None


def _in_sleep_window(now_t: time, bed: time, wake: time) -> bool:
    if bed == wake:
        return False
    if bed < wake:
        return bed <= now_t < wake
    return now_t >= bed or now_t < wake


async def _fetch_world_clock(hass, session, cfg) -> dict:
    tzname = cfg.get(CONF_TIMEZONE) or hass.config.time_zone or "UTC"
    try:
        tz = ZoneInfo(tzname)
    except Exception as err:  # noqa: BLE001
        raise ModuleError(f"Unknown timezone: {tzname}") from err
    now = datetime.now(tz)
    off = now.strftime("%z")
    offset = f"UTC{off[:3]}:{off[3:]}" if off else "UTC"

    facts = [
        {"text": now.strftime("%a %d %b"), "level": None},
        {"text": offset, "level": None},
    ]
    detail = None
    is_awake = None
    category = "World clock"
    cat_level = None
    icon = "mdi:clock-time-four-outline"

    bed = _parse_hhmm(cfg.get(CONF_BEDTIME))
    wake = _parse_hhmm(cfg.get(CONF_WAKE))
    if bed and wake:
        asleep = _in_sleep_window(now.time(), bed, wake)
        is_awake = not asleep
        if asleep:
            category, cat_level, icon = "Asleep", "info", "mdi:weather-night"
            facts.append({"text": f"Wakes in {_until(now, wake)}", "level": "info"})
            detail = (
                f"It is {now.strftime('%H:%M')} there and they are probably "
                f"asleep. Maybe send a text instead of calling."
            )
        else:
            category, cat_level, icon = "Awake", "good", "mdi:white-balance-sunny"
            facts.append({"text": f"Bedtime in {_until(now, bed)}", "level": None})
            detail = f"It is {now.strftime('%H:%M')} there and they should be awake."

    work = _parse_hhmm(cfg.get(CONF_WORK))
    if work:
        facts.append({"text": f"Work in {_until(now, work)}", "level": None})

    return {
        "state": now.strftime("%H:%M"),
        "attributes": {
            "timezone": tzname,
            "date": now.strftime("%Y-%m-%d"),
            "weekday": now.strftime("%A"),
            "utc_offset": off,
            "is_awake": is_awake,
            "category": category,
            "category_level": cat_level,
            "detail": detail,
            "facts": facts,
        },
        "icon": icon,
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

    tip = None
    if not isinstance(air, (int, float)):
        verdict, reason = "Unknown", "No temperature data available."
    elif pavement is not None and pavement >= 50:
        verdict = "Too hot for paws"
        reason = f"Pavement is about {round(pavement)} C, hot enough to burn paws."
        tip = "Wait for cooler hours or walk on grass. Bring water for your dog."
    elif pavement is not None and pavement >= 40:
        verdict = "Warm, take care"
        reason = f"Pavement is about {round(pavement)} C."
        tip = "Prefer grass, go early or late, and bring water for your dog."
    elif air <= -5 or (snow and air <= 0):
        verdict = "Cold, protect paws"
        reason = "Freezing with snow, so roads are likely salted."
        tip = "Booties help. Wipe and check paws for salt and ice after the walk."
    elif air <= 0:
        verdict = "Chilly, watch for ice"
        reason = "Temperatures are around freezing."
        tip = "Watch for ice and road salt, and wipe paws afterwards."
    else:
        verdict = "Good to go"
        reason = "Comfortable conditions for a walk."
        tip = "Nice weather, enjoy the walk."

    salt_risk = "High" if (snow and isinstance(air, (int, float)) and air <= 2) else "Low"

    facts = []
    if isinstance(air, (int, float)):
        facts.append({"text": f"{round(air)}°C air", "level": _lvl_air(air)})
    if pavement is not None:
        facts.append({"text": f"{round(pavement)}°C pavement", "level": _lvl_pavement(pavement)})
    if isinstance(uv, (int, float)):
        facts.append({"text": f"UV {round(uv)}", "level": _lvl_uv(uv)})
    facts.append({"text": f"Salt risk {salt_risk}", "level": "good" if salt_risk == "Low" else "bad"})

    cat_level = {
        "Good to go": "good",
        "Warm, take care": "warn",
        "Chilly, watch for ice": "warn",
        "Too hot for paws": "bad",
        "Cold, protect paws": "bad",
    }.get(verdict)

    # Short, plain headline for the big state; the specific verdict goes in the
    # coloured chip so the two are not the same red text twice.
    headline = {
        "Good to go": "Good to go",
        "Warm, take care": "Warm out",
        "Too hot for paws": "Hot, be careful",
        "Cold, protect paws": "Cold, take care",
        "Chilly, watch for ice": "Chilly out",
    }.get(verdict, verdict)

    detail = reason + (" " + tip if tip else "")

    return {
        "state": headline,
        "attributes": {
            "verdict": verdict,
            "air_temp_c": air,
            "ground_temp_c": ground,
            "estimated_pavement_c": pavement,
            "feels_like_c": feels,
            "uv_index": uv,
            "snowfall_cm": snow,
            "salt_risk": salt_risk,
            "reason": reason,
            "tip": tip,
            "category": verdict,
            "category_level": cat_level,
            "detail": detail,
            "facts": facts,
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
    copyright_ = _clean(data.get("copyright"), 100)
    facts = [_clean(data.get("date"), 10)]
    if copyright_:
        facts.append(f"© {copyright_}")
    return {
        "state": _clean(data.get("title")),
        "attributes": {
            "explanation": _clean(data.get("explanation"), 1500),
            "date": _clean(data.get("date"), 10),
            "copyright": copyright_,
            "url": _https(data.get("url")),
            "media_type": media,
            "category": "NASA image of the day",
            "detail": _clean(data.get("explanation"), 240),
            "facts": [f for f in facts if f],
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


async def _fetch_air_quality(hass, session, cfg) -> dict:
    lat = cfg.get(CONF_LATITUDE, hass.config.latitude)
    lon = cfg.get(CONF_LONGITUDE, hass.config.longitude)
    data = await _get_json(
        session,
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "european_aqi,us_aqi,pm2_5,pm10,uv_index",
        },
    )
    cur = data.get("current") if isinstance(data, dict) else None
    if not isinstance(cur, dict):
        raise ModuleError("No air-quality data")
    aqi = cur.get("european_aqi")
    pm25 = cur.get("pm2_5")
    uv = cur.get("uv_index")
    rating, advice = "Unknown", None
    if isinstance(aqi, (int, float)):
        levels = [
            (20, "Good", "Air is clean. Great for outdoor activity."),
            (40, "Fair", "Air is acceptable for most people."),
            (60, "Moderate", "Sensitive groups should take it easier outdoors."),
            (80, "Poor", "Consider shorter or lighter outdoor activity."),
            (100, "Very poor", "Limit time outdoors, especially if sensitive."),
        ]
        rating, advice = "Extremely poor", "Avoid outdoor exertion."
        for limit, label, tip in levels:
            if aqi <= limit:
                rating, advice = label, tip
                break
    def _lvl_aqi(v):
        if not isinstance(v, (int, float)):
            return None
        return "good" if v <= 40 else "warn" if v <= 60 else "bad"

    def _lvl_pm(v):
        if not isinstance(v, (int, float)):
            return None
        return "good" if v <= 10 else "warn" if v <= 25 else "bad"

    cat_level = {
        "Good": "good", "Fair": "good", "Moderate": "warn",
        "Poor": "bad", "Very poor": "bad", "Extremely poor": "bad",
    }.get(rating)

    facts = []
    if isinstance(aqi, (int, float)):
        facts.append({"text": f"EAQI {round(aqi)}", "level": _lvl_aqi(aqi)})
    if isinstance(pm25, (int, float)):
        facts.append({"text": f"PM2.5 {pm25}", "level": _lvl_pm(pm25)})
    if isinstance(uv, (int, float)):
        facts.append({"text": f"UV {round(uv)}", "level": _lvl_uv(uv)})
    return {
        "state": rating,
        "attributes": {
            "european_aqi": aqi,
            "us_aqi": cur.get("us_aqi"),
            "pm2_5": pm25,
            "pm10": cur.get("pm10"),
            "uv_index": uv,
            "category": "Air quality",
            "category_level": cat_level,
            "detail": advice,
            "facts": facts,
        },
        "icon": "mdi:air-filter",
    }


async def _fetch_sun_times(hass, session, cfg) -> dict:
    lat = cfg.get(CONF_LATITUDE, hass.config.latitude)
    lon = cfg.get(CONF_LONGITUDE, hass.config.longitude)
    data = await _get_json(
        session,
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "daily": "sunrise,sunset,uv_index_max",
            "timezone": "auto",
        },
    )
    daily = data.get("daily") if isinstance(data, dict) else None
    if not isinstance(daily, dict):
        raise ModuleError("No sun data")
    sunrise = (daily.get("sunrise") or [None])[0]
    sunset = (daily.get("sunset") or [None])[0]
    uv_max = (daily.get("uv_index_max") or [None])[0]
    day_length = None
    try:
        delta = datetime.fromisoformat(sunset) - datetime.fromisoformat(sunrise)
        mins = int(delta.total_seconds() // 60)
        day_length = f"{mins // 60}h {mins % 60}m"
    except (TypeError, ValueError):
        pass
    return {
        "state": day_length,
        "attributes": {
            "sunrise": sunrise[11:16] if isinstance(sunrise, str) else None,
            "sunset": sunset[11:16] if isinstance(sunset, str) else None,
            "sunrise_iso": sunrise,
            "sunset_iso": sunset,
            "uv_index_max": uv_max,
        },
        "icon": "mdi:weather-sunny",
    }


_MOON_NAMES = [
    "New moon", "Waxing crescent", "First quarter", "Waxing gibbous",
    "Full moon", "Waning gibbous", "Last quarter", "Waning crescent",
]
_MOON_ICONS = [
    "mdi:moon-new", "mdi:moon-waxing-crescent", "mdi:moon-first-quarter",
    "mdi:moon-waxing-gibbous", "mdi:moon-full", "mdi:moon-waning-gibbous",
    "mdi:moon-last-quarter", "mdi:moon-waning-crescent",
]


async def _fetch_moon_phase(hass, session, cfg) -> dict:
    # Computed locally from the synodic month, no network needed.
    synodic = 29.530588853
    ref = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)
    age = ((datetime.now(timezone.utc) - ref).total_seconds() / 86400) % synodic
    phase = age / synodic
    illumination = round((1 - math.cos(2 * math.pi * phase)) / 2 * 100)
    idx = int(phase * 8 + 0.5) % 8
    return {
        "state": _MOON_NAMES[idx],
        "attributes": {
            "illumination_percent": illumination,
            "moon_age_days": round(age, 1),
            "phase_fraction": round(phase, 3),
            "category": "Moon phase",
            "detail": f"The moon is {illumination}% illuminated.",
            "facts": [f"{illumination}% lit", f"Age {round(age, 1)} days"],
        },
        "icon": _MOON_ICONS[idx],
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
        ),
        vol.Optional(CONF_BEDTIME, default=d.get(CONF_BEDTIME, "")): str,
        vol.Optional(CONF_WAKE, default=d.get(CONF_WAKE, "")): str,
        vol.Optional(CONF_WORK, default=d.get(CONF_WORK, "")): str,
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


def _schema_none(hass, d) -> dict:
    return {}


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
    TYPE_AIR_QUALITY: Module(
        TYPE_AIR_QUALITY, "Air quality", "mdi:air-filter", 1800,
        _schema_location, _fetch_air_quality,
    ),
    TYPE_SUN: Module(
        TYPE_SUN, "Sunrise and sunset", "mdi:weather-sunny", 3600,
        _schema_location, _fetch_sun_times,
    ),
    TYPE_MOON: Module(
        TYPE_MOON, "Moon phase", "mdi:moon-waning-crescent", 3600,
        _schema_none, _fetch_moon_phase,
    ),
}


def module_options() -> list[dict[str, str]]:
    """Options for the type selector, in a stable order."""
    return [{"value": key, "label": mod.name} for key, mod in MODULES.items()]
