"""The pip requirements the description tests need, derived from installed core.

Nothing here is used by this integration. `async_get_all_descriptions` imports
every base component in order to read its services.yaml, so the test rig has to
satisfy those components' own requirements or the description tests fail on a
ModuleNotFoundError for something entirely unrelated to priority.

That list was maintained by hand until Home Assistant 2026.9 added a
`gazetteer_matcher` import to conversation and CI went red on a release that
changed nothing here. Reading the manifests instead keeps it correct, and pins
each package to what the installed core actually declares rather than to
whatever is newest on PyPI.

Usage, after core is installed:

    python tests/shim_requirements.py | xargs -r pip install
"""

from __future__ import annotations

import json
import pathlib

import homeassistant

# `homeassistant.helpers.service._base_components`, copied rather than called:
# calling it imports these modules, which is the very thing that fails when a
# requirement is missing. test_core_contract.py asserts the two stay equal.
BASE_COMPONENTS = [
    "ai_task",
    "alarm_control_panel",
    "assist_satellite",
    "calendar",
    "camera",
    "climate",
    "cover",
    "fan",
    "humidifier",
    "light",
    "lock",
    "media_player",
    "notify",
    "remote",
    "siren",
    "todo",
    "update",
    "vacuum",
    "water_heater",
]


def shim_requirements() -> set[str]:
    """Return the requirements of the base components and everything they pull."""
    components = pathlib.Path(homeassistant.__file__).parent / "components"
    seen: set[str] = set()
    requirements: set[str] = set()
    pending = list(BASE_COMPONENTS)
    while pending:
        component = pending.pop()
        if component in seen:
            continue
        seen.add(component)
        manifest = components / component / "manifest.json"
        if not manifest.exists():
            continue
        data = json.loads(manifest.read_text())
        requirements.update(data.get("requirements", []))
        pending.extend(data.get("dependencies", []))
    return requirements


if __name__ == "__main__":
    print("\n".join(sorted(shim_requirements())))
