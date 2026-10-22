"""Test PurpleAir sensors."""

import pytest
from pytest_homeassistant.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "setup_config_entry")
async def test_all_entities(
    hass: HomeAssistant,
    snapshot: SnapshotAssertion,
    config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Test all entities."""
    await snapshot_platform(hass, entity_registry, snapshot, config_entry.entry_id)


@pytest.mark.parametrize(
    "average", ["10minute", "30minute", "60minute", "6hour", "24hour", "1week"]
)
@pytest.mark.usefixtures("setup_config_entry")
async def test_pm25_averages_disabled_by_default(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    average: str,
) -> None:
    """Test the PM2.5 average sensors are disabled by default."""
    entity_id = entity_registry.async_get_entity_id(
        "sensor", "purpleair", f"123456-pm2.5_{average}_mass_concentration"
    )
    assert entity_id is not None
    entry = entity_registry.async_get(entity_id)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
