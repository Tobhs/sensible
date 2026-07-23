"""Constants for the Sensible integration."""

from __future__ import annotations

DOMAIN = "sensible"
DEFAULT_NAME = "Sensible"

# Which module a config entry represents.
CONF_TYPE = "type"

# Module-specific configuration keys.
CONF_TIMEZONE = "timezone"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
CONF_LANGUAGE = "language"
CONF_API_KEY = "api_key"
CONF_BASE = "base"
CONF_TARGET = "target"
CONF_COUNTRY = "country"
CONF_URL = "url"
CONF_VALUE_PATH = "value_path"

# Module type keys.
TYPE_WORLD_CLOCK = "world_clock"
TYPE_PAW_SAFETY = "paw_safety"
TYPE_FUN_FACT = "fun_fact"
TYPE_DAILY_IMAGE = "daily_image"
TYPE_CURRENCY = "currency"
TYPE_HOLIDAYS = "holidays"
TYPE_REST = "rest"

USER_AGENT = "home-assistant-sensible/1.0 (+https://github.com/Tobhs/sensible)"
REQUEST_TIMEOUT = 20
MAX_TEXT_LEN = 255
