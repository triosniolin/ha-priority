"""Constants for the priority command arbitration integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "priority"

# Matches the name a core patch would add to cv.ENTITY_SERVICE_FIELDS.
ATTR_PRIORITY: Final = "priority"

# Lease in seconds; absent or 0 holds until relinquished or rewritten at the same level.
ATTR_PRIORITY_TTL: Final = "priority_ttl"

# Lower wins. Everything, automations included, lands at 5 unless the caller names a level:
# HA automations never relinquish, so a separate automation default would fill and never drain.
PRI_MANUAL_EMERGENCY: Final = 1
PRI_AUTO_EMERGENCY: Final = 2
PRI_MANUAL: Final = 3
PRI_AUTO: Final = 4
PRI_DEFAULT: Final = 5

MIN_PRIORITY: Final = PRI_MANUAL_EMERGENCY
MAX_PRIORITY: Final = PRI_DEFAULT
NUM_SLOTS: Final = MAX_PRIORITY

PRIORITY_NAMES: Final[dict[int, str]] = {
    PRI_MANUAL_EMERGENCY: "Manual Emergency",
    PRI_AUTO_EMERGENCY: "Automatic Emergency",
    PRI_MANUAL: "Manual",
    PRI_AUTO: "Automatic",
    PRI_DEFAULT: "Default",
}

# Slot 5 is not stored: a saved copy would be a claim about a device that may have moved while down.
# Restored slots suppress lower levels but are not re-driven at startup.
PERSISTED_PRIORITIES: Final = (
    PRI_MANUAL_EMERGENCY,
    PRI_AUTO_EMERGENCY,
    PRI_MANUAL,
    PRI_AUTO,
)

STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1

# Installing the integration is the opt-in; the exclude list is the escape hatch.
CONF_SCOPE: Final = "scope"
SCOPE_ALL: Final = "all"
SCOPE_SELECTED: Final = "selected"
DEFAULT_SCOPE: Final = SCOPE_ALL

CONF_EXCLUDED_ENTITIES: Final = "excluded_entities"
CONF_MANAGED_ENTITIES: Final = "managed_entities"
CONF_MANAGED_LABELS: Final = "managed_labels"
CONF_MANAGED_AREAS: Final = "managed_areas"
CONF_DEFAULT_USER_PRIORITY: Final = "default_user_priority"
CONF_DEFAULT_AUTOMATION_PRIORITY: Final = "default_automation_priority"
CONF_TRACK_OUT_OF_BAND: Final = "track_out_of_band"

# Raising the automation default to 4 brings back the lockout described at PRI_* above.
DEFAULT_USER_PRIORITY: Final = PRI_DEFAULT
DEFAULT_AUTOMATION_PRIORITY: Final = PRI_DEFAULT
DEFAULT_TRACK_OUT_OF_BAND: Final = True

SERVICE_RELINQUISH: Final = "relinquish"
SERVICE_RELINQUISH_ALL: Final = "relinquish_all"
SERVICE_SET: Final = "set"
SERVICE_GET: Final = "get"

# Long enough for a device still handshaking after startup to report in.
RECONCILE_DELAY_SECONDS: Final = 30

# How long a dispatch stays attributable; covers a slow cloud round trip (LG ThinQ takes 2-3 min).
CONTEXT_TTL_SECONDS: Final = 300
CONTEXT_MAP_MAX_ENTRIES: Final = 2048

# Services that command a device; anything unlisted passes through. Mirrored in priority-card.js.
ARBITRATED_SERVICES: Final[dict[str, frozenset[str]]] = {
    "light": frozenset({"turn_on", "turn_off", "toggle"}),
    "switch": frozenset({"turn_on", "turn_off", "toggle"}),
    "fan": frozenset(
        {
            "turn_on",
            "turn_off",
            "toggle",
            "set_percentage",
            "set_preset_mode",
            "set_direction",
            "oscillate",
        }
    ),
    "cover": frozenset(
        {
            "open_cover",
            "close_cover",
            "stop_cover",
            "toggle",
            "set_cover_position",
            "set_cover_tilt_position",
            "open_cover_tilt",
            "close_cover_tilt",
            "stop_cover_tilt",
        }
    ),
    "climate": frozenset(
        {
            "turn_on",
            "turn_off",
            "toggle",
            "set_temperature",
            "set_hvac_mode",
            "set_fan_mode",
            "set_preset_mode",
            "set_humidity",
            "set_swing_mode",
        }
    ),
    "water_heater": frozenset(
        {"turn_on", "turn_off", "set_temperature", "set_operation_mode"}
    ),
    "humidifier": frozenset(
        {"turn_on", "turn_off", "toggle", "set_humidity", "set_mode"}
    ),
    "lock": frozenset({"lock", "unlock", "open"}),
    "valve": frozenset(
        {"open_valve", "close_valve", "stop_valve", "toggle", "set_valve_position"}
    ),
    "media_player": frozenset({"turn_on", "turn_off", "toggle", "volume_set"}),
    "input_boolean": frozenset({"turn_on", "turn_off", "toggle"}),
    "input_number": frozenset({"set_value"}),
}
