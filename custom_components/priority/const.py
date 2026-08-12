"""Constants for the priority command arbitration integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "priority"

# Service-call field carrying the priority. Deliberately matches the name the
# core patch would add to cv.ENTITY_SERVICE_FIELDS, so automations written
# against the custom integration keep working if this ever lands upstream.
ATTR_PRIORITY: Final = "priority"

# Optional lease on a command, in seconds. Absent or 0 means the slot holds
# until something relinquishes it or rewrites it at the same level. An override
# with no end is easy to issue and easy to forget, and until it is released
# every automation below it is dead - so a bounded override is usually what the
# caller actually wanted.
ATTR_PRIORITY_TTL: Final = "priority_ttl"

# Priority levels. Lower number wins. Five levels rather than BACnet's sixteen.
#
# Level 5 is where *everything* lands unless a caller asks for otherwise -
# automations, the UI, the app, voice, and wall switches alike. Writes at the
# same level simply replace each other, so a house that never mentions priority
# behaves exactly like stock Home Assistant: last command wins.
#
# That is deliberate. An earlier draft defaulted automations to 4 and people to
# 5, which is faithful to the BACnet model but wrong for Home Assistant: an
# automation would take a slot, never relinquish it (HA automations fire and
# forget - there is no relinquish idiom), and the entity would stop responding
# to the UI forever. Defaulting everything to 5 means arbitration is something
# you opt into per command, not something that accumulates behind your back.
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

# Every override level survives a restart. Only slot 5 does not: it is the
# ordinary last-wins traffic of the house, it re-establishes itself the moment
# anything is commanded, and a stored copy of it would be a claim about
# physical reality that may have moved while Home Assistant was down.
#
# Slot 4 was originally excluded on the reasoning that automations re-assert on
# their own triggers, so a restored automation command would just be stale. That
# was wrong, and the mistake was inherited from an earlier draft in which 4 was
# the *default* level for automations - somewhere commands would pile up without
# anyone asking for them, and not worth preserving. Since everything defaults to
# 5, nothing reaches slot 4 unless a caller explicitly wrote `priority: 4`. That
# is a deliberate statement that this command should outrank ordinary traffic,
# and it is no more disposable than the same statement made at level 3. An
# automation that only re-asserts on an edge (peak start, a door opening, a
# threshold crossing) has no trigger to re-fire after a reboot, so dropping its
# slot silently handed control back to whatever it was overriding.
#
# A restored slot suppresses lower levels but is not re-driven at startup, the
# same as levels 1-3; see the note at the foot of store.py about why nothing is
# dispatched on load. Same-level writes replace each other, so an automation
# that *does* re-assert simply overwrites what was restored.
PERSISTED_PRIORITIES: Final = (
    PRI_MANUAL_EMERGENCY,
    PRI_AUTO_EMERGENCY,
    PRI_MANUAL,
    PRI_AUTO,
)

STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1

# Config entry options
#
# Scope is "all" by default: adding the integration puts every entity in an
# arbitrated domain under priority control. The opt-in boundary is the
# integration itself, not a per-entity list - installing nothing changes
# nothing, installing it changes everything, and an escape hatch is provided by
# the exclude list rather than by an include list nobody wants to maintain.
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

# Both default to 5: a call that does not mention priority behaves exactly as it
# does without this integration installed. Raising the automation default to 4
# is available for anyone who wants BACnet-style separation, but it is a
# deliberate choice with the lockout caveat above attached to it.
DEFAULT_USER_PRIORITY: Final = PRI_DEFAULT
DEFAULT_AUTOMATION_PRIORITY: Final = PRI_DEFAULT
DEFAULT_TRACK_OUT_OF_BAND: Final = True

# Services offered by this integration
SERVICE_RELINQUISH: Final = "relinquish"
SERVICE_RELINQUISH_ALL: Final = "relinquish_all"
SERVICE_SET: Final = "set"
SERVICE_GET: Final = "get"

# How long a dispatched context id stays attributable before the observer
# treats a resulting state change as out-of-band. Generous enough to cover a
# slow cloud round trip (LG ThinQ takes 2-3 minutes) without growing unbounded.
# How long after startup completes before emergency holds are reconciled. Long
# enough for a device that is still handshaking to report in, short enough that
# a genuinely stale emergency override is not left standing. Anything still
# unavailable when it fires is skipped and handled by the observer instead.
RECONCILE_DELAY_SECONDS: Final = 30

CONTEXT_TTL_SECONDS: Final = 300
CONTEXT_MAP_MAX_ENTRIES: Final = 2048

# Domain services this integration arbitrates. Each entry is the set of
# services whose effect is a *command* to a device, as opposed to a query or a
# configuration change. Services not listed pass through untouched.
#
# Slots hold whole service calls, so on/off and attribute-setting services can
# share one array per entity.
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

# Services that have no stable meaning inside a priority array, because their
# result depends on the state at the moment of the call. These are resolved to
# a concrete service before the slot is written.
TOGGLE_SERVICES: Final[dict[str, tuple[str, str]]] = {
    # domain service -> (service when currently "on", service when not)
    "toggle": ("turn_off", "turn_on"),
}
