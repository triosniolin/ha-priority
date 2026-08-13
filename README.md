# Priority Command Arbitration for Home Assistant

Give every command a level of authority, so a manual action and an automation can disagree
without one silently clobbering the other.

Home Assistant has no concept of *who* commanded an entity or *with what authority*. Every
service call is a naked write: the last caller wins. There is no way to say "an automation may
override this" versus "only an emergency may override this". The usual workarounds (guard
booleans, capture-and-restore, mutually-aware automations) all break in the same place: if an
automation runs *during* an override window, the state you captured is already stale.

Building controls solved this decades ago with the BACnet priority array (ASHRAE 135). This is
that idea, trimmed from sixteen levels to five, and made to fit Home Assistant.

## The levels

| Level | Name | Typical writer |
|---|---|---|
| 1 | Manual Emergency | A person, overriding everything |
| 2 | Automatic Emergency | Life-safety automations (smoke, freeze, leak) |
| 3 | Manual | A person who does not want ordinary automations interfering |
| 4 | Automatic | An automation that wants to hold against ordinary traffic |
| 5 | **Default** | **Everything, unless the caller says otherwise** |

The lowest-numbered occupied level drives the device. Writes at the same level simply replace
each other.

Levels 1 to 4 are all *overrides*: nothing reaches them unless a caller asks for them by name, and
they all survive a restart. Level 4 exists for the automation that does not deserve an emergency
level but does need to outrank ordinary traffic, and to keep doing so through a reboot; an
automation that fires on an edge (a peak window opening, a threshold crossing) has no trigger to
re-fire afterwards.

## It changes nothing until you ask it to

Everything defaults to level 5, including automations. Since same-level writes replace each
other, a house that never mentions priority behaves exactly as it does today: last command
wins, and a person can always countermand an automation from the app.

That default is deliberate and load-bearing. Splitting it (automations at 4, people at 5) is
the faithful BACnet reading, and it is wrong here. Home Assistant automations fire and forget;
there is no relinquish idiom. Level 4 would fill up and never drain, and every entity an
automation had ever touched would go deaf to the UI. Arbitration has to be something you opt
into per command, not something that accumulates behind your back.

## Temporary overrides, which is the thing I actually wanted

"Turn this light on for twenty minutes, then put it back" has never had a clean answer in Home
Assistant. The usual shape is turn on, `delay`, turn off; that breaks if anything else touches
the light meanwhile, it breaks across a restart, and it hardcodes "off" as the thing to go back
to even when that was never true.

Give the command a level and a lease instead:

```yaml
action: light.turn_on
target:
  entity_id: light.porch
data:
  priority: 3              # Manual
  priority_ttl: "00:20:00"
```

Twenty minutes later the level clears itself and the light goes back to whatever is underneath.
If nothing else wanted it on, that is off. If an automation had turned it on in the meantime,
it stays on, because the automation's command was never destroyed; it was just outranked while
the override held.

That is the part a timer cannot do. The lease does not turn the light off after twenty minutes;
it stops overriding after twenty minutes, and the house resumes whatever it was already trying
to do. Nothing needs to remember a previous state, so nothing can restore a stale one.

It survives a restart (every override level persists with its lease, and a lease that lapsed while
Home Assistant was down is dropped rather than resurrected), and you can end it early with
`priority.relinquish` or the Release button on the entity's own more-info dialog.

This fell out of the priority model rather than being designed in, which is usually a sign the
model is the right shape.

## Using it

Any arbitrated service takes two extra fields:

```yaml
action: light.turn_on
target:
  entity_id: light.porch
data:
  brightness_pct: 60
  priority: 2            # Automatic Emergency
  priority_ttl: "00:30:00"   # release itself after half an hour
```

Both appear as proper form fields in the automation editor, the script editor, Developer Tools
and button tap actions; the integration rewrites the affected service descriptions at runtime.

`priority_ttl` turns an override into a loan rather than a seizure. An override with no end is
easy to issue and easy to forget, and until it is released everything below it is dead. Omit it
(or pass `0`) to hold until something relinquishes. It is rejected at level 5, which has nothing
underneath to fall back to.

### Releasing

```yaml
action: priority.relinquish        # clear one level
target: {entity_id: light.porch}
data: {priority: 2}

action: priority.relinquish_all    # clear every level above Default
target: {entity_id: light.porch}

action: priority.set               # write a level directly
target: {entity_id: light.porch}
data: {priority: 3, service: turn_on, data: {brightness: 128}}

action: priority.get               # returns the full array (response service)
target: {entity_id: light.porch}
```

Clearing a level hands control to the next one down and re-issues **that command as it stands
right now**, not a snapshot taken when the override began. That is the failure every
hand-rolled capture-and-restore automation eventually hits.

## In the UI

- **On the entity itself.** Open any supported entity's more-info dialog and a priority row
  appears beneath the normal controls: a level picker, a lease picker, and the full array. The
  pickers are *modifiers*; choose a level, then use the entity's ordinary controls (the toggle,
  the brightness slider, the position handle) and those commands carry it.
- **On the built-in tile card**, via `features: - type: custom:priority-feature`.
- **`custom:priority-overrides-card`**: everything currently held, with one-click release.
- **`custom:priority-control-card`**: a standalone card that issues commands at a level.

The array is shown in full, so you can see what is queued underneath what is winning:

No Priority Commands (default, regular HA behavior):
<img width="688" height="750" alt="image" src="https://github.com/user-attachments/assets/1a396fba-64fb-4a3d-94c8-6312107af488" />

Two priority commands and a default command: 
<img width="639" height="750" alt="image" src="https://github.com/user-attachments/assets/7629adc3-3bda-479c-9bc5-495a7e648429" />

The priority array:
<img width="774" height="217" alt="image" src="https://github.com/user-attachments/assets/c5e4ef0d-da3b-4057-85f5-cb431d32cb00" />


Each override level can be released on its own. Taking, releasing and expiring an override are
written to the entity's logbook, so the history shows what held it and when.

## Supported domains

`light`, `switch`, `fan`, `cover`, `climate`, `water_heater`, `humidifier`, `lock`, `valve`,
`media_player`, `input_boolean`, `input_number`. Anything else passes through untouched.

## Installation

HACS → Custom repositories → add this repo as an Integration, install, restart, then add
**Priority Command Arbitration** from Settings → Devices & Services. Or copy
`custom_components/priority` into your config directory and restart.

The dashboard card registers itself; no Lovelace resource step is needed. Hard-refresh the
browser after installing or updating.

## How it works

No fork of Home Assistant is required. For each arbitrated service the integration captures the
`Service` object already in the registry and registers a wrapper over the same name. The wrapper
holds the arbitration decision; the captured original is the only thing that ever touches a
device.

Dispatch deliberately does **not** go back through `hass.services.async_call`; the captured
job is invoked directly. That makes recursion structurally impossible rather than something a
guard flag has to catch, and means one relinquish produces exactly one dispatch.

Physical changes (a wall switch, a vendor app, a Zigbee group) are detected by their state
change not matching any dispatch of ours, and recorded at Default. Touching a switch therefore
behaves exactly like an ordinary command. The array is never re-asserted against somebody
standing at a light switch.

## Caveats

Read these two first; they are the ones that would actually cost you something.

### It rewrites the service registry

For every supported service (`light.turn_on`, `lock.unlock`, `cover.set_cover_position` and
about fifty others) this pulls the registered handler out of the live service registry and
registers a wrapper over the same name. That is the central bet, and it rests on Home Assistant
internals rather than public API: `async_services_internal()`, the shape of `Service`, and the
target-resolution helpers.

Those can change in any core release. The code is careful about it (a marker so a reload cannot
double-wrap, a suspend flag so re-registering does not retrigger the registration listener,
originals restored on unload) but careful does not help if core renames something. The realistic
bad day is an upgrade after which supported entities stop responding, fixed by removing the
config entry.

Two things reduce that risk rather than just describing it. `tests/test_core_contract.py`
asserts every core internal this depends on, so a break is one obvious failure naming exactly
what moved instead of eighty confusing ones. CI runs that suite weekly against current Home
Assistant, so a core release that breaks it shows up as a red build rather than as your lights
going unresponsive.

### A suppressed command reports success

If a level is holding an entity and something lower tries to move it, the command is recorded
and not dispatched. The service call still returns successfully. There is no error to the
caller, so a script, a voice assistant, or a dashboard button will all look like they worked.

For a light that is mildly confusing. For `lock` and `valve` it is worth thinking about before
you enable it: a forgotten indefinite override on a lock is a genuinely bad failure mode. Either
use `priority_ttl` so overrides expire on their own, or exclude those entities in the options
flow. The `Active overrides` sensor and the overrides card exist to make a forgotten hold
visible, but they only help if you look.

### Everything else

- The more-info row patches a compiled frontend element and has no supported API behind it. It
  is written to fail closed; if a Home Assistant update breaks it, the row stops appearing and
  nothing else changes. The tile-card feature uses a supported extension point and is the
  fallback.
- Every override level (1 to 4) is restored across a restart, leases included; a lease that lapsed
  while Home Assistant was down is dropped rather than resurrected. Level 5 is not restored, since
  it is ordinary last-wins traffic and a stored copy of it would be a claim about physical reality
  that may have moved while Home Assistant was down.
- A restored hold at level 1 or 2 is re-issued once, about half a minute after startup finishes.
  Nothing else is. While Home Assistant is running the array is never re-asserted against reality,
  but a restart is the one case that rule does not cover honestly: a change during downtime was
  observed by nobody, so a power blip that returned a relay to its default looks identical to a
  person deciding something. At emergency levels that is worth one command; at levels 3 and 4 it is
  not, because "do not let automations interfere" is a weaker claim than "this must be true", and
  silently re-commanding a device on every boot is too surprising to do on your behalf. Entities
  still unavailable at that point are skipped and picked up when they return.
- **`entity_id: all` is arbitrated.** A goodnight scene or an "all off" sweep will *not* defeat
  your overrides (held entities are skipped, everything else is switched normally). This was not
  true before 2026-08-11, when `all` silently bypassed arbitration entirely.

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements-test.txt
.venv/bin/python -m pytest tests/     # integration behaviour
node tests/card_test.js               # frontend logic
```

Home Assistant 2026.8.1 or newer.

## Status

Working and in daily use, but young. Bug reports welcome.

## Licence

Apache 2.0, matching Home Assistant core so the code can be proposed upstream without a
relicensing step.
