"""Who a slot says wrote it."""

from __future__ import annotations

from homeassistant.core import Context

LIGHT = "light.one"


async def test_user_id_resolves_to_a_display_name(
    priority_entry, demo_hass
) -> None:
    """A raw user id in the UI is useless; the card shows this string."""
    user = await demo_hass.auth.async_create_user("Ada")
    manager = priority_entry.runtime_data
    await manager.async_refresh_user_names()

    assert manager.async_attribute(Context(user_id=user.id)) == "user:Ada"


async def test_unknown_user_id_falls_back_to_the_id(
    priority_entry, demo_hass
) -> None:
    """Attribution degrades to the raw id rather than to nothing at all."""
    manager = priority_entry.runtime_data

    assert manager.async_attribute(Context(user_id="nosuchuser")) == (
        "user:nosuchuser"
    )


async def test_a_miss_refreshes_so_the_next_write_resolves(
    priority_entry, demo_hass
) -> None:
    """A user created after setup should not stay unresolved forever."""
    manager = priority_entry.runtime_data
    user = await demo_hass.auth.async_create_user("Grace")

    # Not in the cache yet, because the cache was built during setup.
    assert manager.async_attribute(Context(user_id=user.id)) == f"user:{user.id}"

    await demo_hass.async_block_till_done()

    assert manager.async_attribute(Context(user_id=user.id)) == "user:Grace"


async def test_attribution_survives_an_auth_failure(
    priority_entry, demo_hass, monkeypatch
) -> None:
    """A write must never fail because the user list could not be read."""
    manager = priority_entry.runtime_data

    async def _boom():
        raise RuntimeError("auth unavailable")

    monkeypatch.setattr(demo_hass.auth, "async_get_users", _boom)
    await manager.async_refresh_user_names()

    assert manager.async_attribute(Context(user_id="anyone")) == "user:anyone"


async def test_automation_attribution_is_unchanged(
    priority_entry, demo_hass
) -> None:
    """Automations already resolved to an entity id; keep it that way."""
    manager = priority_entry.runtime_data
    context = Context()
    demo_hass.states.async_set(
        "automation.porch", "on", {}, context=context
    )
    await demo_hass.async_block_till_done()

    assert manager.async_attribute(context) == "automation.porch"
