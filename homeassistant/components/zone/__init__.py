"""Support for the definition of zones."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
import logging
from types import SimpleNamespace
from typing import Any, cast

from typing_extensions import Self
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import (
    ATTR_EDITABLE,
    ATTR_ENTITY_ID,
    ATTR_LATITUDE,
    ATTR_LONGITUDE,
    ATTR_PERSONS,
    CONF_ICON,
    CONF_ID,
    CONF_LATITUDE,
    CONF_LONGITUDE,
    CONF_NAME,
    CONF_RADIUS,
    CONF_TYPE,
    EVENT_CORE_CONFIG_UPDATE,
    SERVICE_RELOAD,
    STATE_HOME,
    STATE_NOT_HOME,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import Event, HomeAssistant, ServiceCall, State, callback
from homeassistant.helpers import (
    collection,
    config_validation as cv,
    entity_component,
    event,
    service,
    storage,
)
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import bind_hass
from homeassistant.util.location import distance

from .const import (
    ATTR_PASSIVE,
    ATTR_POINTS,
    ATTR_RADIUS,
    CONF_PASSIVE,
    CONF_POINTS,
    DOMAIN,
    HOME_ZONE,
    ZONE_TYPE_CIRCLE,
    ZONE_TYPE_MULTI,
    ZONE_TYPE_POLYGON,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_PASSIVE = False
DEFAULT_RADIUS = 100

ENTITY_ID_FORMAT = "zone.{}"
ENTITY_ID_HOME = ENTITY_ID_FORMAT.format(HOME_ZONE)

ICON_HOME = "mdi:home"
ICON_IMPORT = "mdi:import"

COMMON_FIELDS = {
    vol.Required(CONF_NAME): cv.string,
    vol.Optional(CONF_PASSIVE, default=DEFAULT_PASSIVE): cv.boolean,
    vol.Optional(CONF_ICON): cv.icon,
}

CREATE_FIELDS = vol.Schema(
    {
        vol.All([
            COMMON_FIELDS,
            vol.Schema(
                # vol.Required(CONF_LATITUDE): cv.latitude,
                # vol.Required(CONF_LONGITUDE): cv.longitude,
                # vol.Optional(CONF_RADIUS, default=DEFAULT_RADIUS): vol.Coerce(float),
            })
        )
    }
)


UPDATE_FIELDS = {
    vol.Optional(CONF_NAME): cv.string,
    vol.Optional(CONF_LATITUDE): cv.latitude,
    vol.Optional(CONF_LONGITUDE): cv.longitude,
    vol.Optional(CONF_RADIUS): vol.Coerce(float),
    vol.Optional(CONF_PASSIVE): cv.boolean,
    vol.Optional(CONF_ICON): cv.icon,
}

POLYGON_FIELDS = {
    vol.Required(CONF_POINTS): vol.list[vol.exact_sequence([cv.latitude, cv.longitude])],
}


def empty_value(value: Any) -> Any:
    """Test if the user has the default config value from adding "zone:"."""
    if isinstance(value, dict) and len(value) == 0:
        return []

    raise vol.Invalid("Not a default value")


CONFIG_SCHEMA = vol.Schema(
    {
        vol.Optional(DOMAIN, default=[]): vol.Any(
            vol.All(cv.ensure_list, [vol.Schema(COMMON_FIELDS), vol.Schema(CREATE_FIELDS)]),
            vol.All(cv.ensure_list, [vol.Schema(COMMON_FIELDS), vol.Schema(POLYGON_FIELDS)]),
            empty_value,
        )
    },
    extra=vol.ALLOW_EXTRA,
)

RELOAD_SERVICE_SCHEMA = vol.Schema({})
STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1


@bind_hass
def async_active_zone(
    hass: HomeAssistant, latitude: float, longitude: float, radius: int = 0
) -> State | None:
    """Find the active zone for given latitude, longitude.

    This method must be run in the event loop.
    """
    # Sort entity IDs so that we are deterministic if equal distance to 2 zones
    zones = (
        cast(State, hass.states.get(entity_id))
        for entity_id in sorted(hass.states.async_entity_ids(DOMAIN))
    )

    min_dist: float = float.infinity
    closest: State = None

    for zone in zones:
        if zone.state == STATE_UNAVAILABLE or zone.attributes.get(ATTR_PASSIVE):
            continue

        zone_dist = distance(
            latitude,
            longitude,
            zone.attributes[ATTR_LATITUDE],
            zone.attributes[ATTR_LONGITUDE],
        )

        if zone_dist is None:
            continue

        within_zone = zone_dist - radius < zone.attributes[ATTR_RADIUS]

        if within_zone:
            closer_zone = closest is None or zone_dist < min_dist  # type: ignore[unreachable]
            smaller_zone = zone_dist == min_dist and zone.attributes[ATTR_RADIUS] < closest.attributes[ATTR_RADIUS]

            if (closer_zone or smaller_zone):
                min_dist = zone_dist
                closest = zone

    return closest

class ZoneStorageCollection(collection.DictStorageCollection):
    """Zone collection stored in storage."""

    CREATE_SCHEMA = vol.Schema(CREATE_FIELDS)
    UPDATE_SCHEMA = vol.Schema(UPDATE_FIELDS)

    async def _process_create_data(self, data: dict) -> dict:
        """Validate the config is valid."""
        return cast(dict, self.CREATE_SCHEMA(data))

    @callback
    def _get_suggested_id(self, info: dict) -> str:
        """Suggest an ID based on the config."""
        return cast(str, info[CONF_NAME])

    async def _update_data(self, item: dict, update_data: dict) -> dict:
        """Return a new updated data object."""
        update_data = self.UPDATE_SCHEMA(update_data)
        return {**item, **update_data}


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up configured zones as well as Home Assistant zone if necessary."""
    component = entity_component.EntityComponent[Zone](_LOGGER, DOMAIN, hass)
    id_manager = collection.IDManager()

    yaml_collection = collection.IDLessCollection(
        logging.getLogger(f"{__name__}.yaml_collection"), id_manager
    )
    collection.sync_entity_lifecycle(
        hass, DOMAIN, DOMAIN, component, yaml_collection, Zone
    )

    storage_collection = ZoneStorageCollection(
        storage.Store(hass, STORAGE_VERSION, STORAGE_KEY),
        id_manager,
    )
    collection.sync_entity_lifecycle(
        hass, DOMAIN, DOMAIN, component, storage_collection, Zone
    )

    if config[DOMAIN]:
        await yaml_collection.async_load(config[DOMAIN])

    await storage_collection.async_load()

    collection.DictStorageCollectionWebsocket(
        storage_collection, DOMAIN, DOMAIN, CREATE_FIELDS, UPDATE_FIELDS
    ).async_setup(hass)

    async def reload_service_handler(service_call: ServiceCall) -> None:
        """Remove all zones and load new ones from config."""
        conf = await component.async_prepare_reload(skip_reset=True)
        if conf is None:
            return
        await yaml_collection.async_load(conf[DOMAIN])

    service.async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RELOAD,
        reload_service_handler,
        schema=RELOAD_SERVICE_SCHEMA,
    )

    if component.get_entity("zone.home"):
        return True

    home_zone = CircleZone(_home_conf(hass), False)
    home_zone.entity_id = ENTITY_ID_HOME
    await component.async_add_entities([home_zone])

    async def core_config_updated(_: Event) -> None:
        """Handle core config updated."""
        await home_zone.async_update_config(_home_conf(hass))

    hass.bus.async_listen(EVENT_CORE_CONFIG_UPDATE, core_config_updated)

    hass.data[DOMAIN] = storage_collection

    return True


@callback
def _home_conf(hass: HomeAssistant) -> dict:
    """Return the home zone config."""
    return {
        CONF_NAME: hass.config.location_name,
        CONF_LATITUDE: hass.config.latitude,
        CONF_LONGITUDE: hass.config.longitude,
        CONF_RADIUS: DEFAULT_RADIUS,
        CONF_ICON: ICON_HOME,
        CONF_PASSIVE: False,
    }


async def async_setup_entry(
    hass: HomeAssistant, config_entry: config_entries.ConfigEntry
) -> bool:
    """Set up zone as config entry."""
    storage_collection = cast(ZoneStorageCollection, hass.data[DOMAIN])

    data = dict(config_entry.data)
    data.setdefault(CONF_PASSIVE, DEFAULT_PASSIVE)
    data.setdefault(CONF_RADIUS, DEFAULT_RADIUS)

    await storage_collection.async_create_item(data)

    hass.async_create_task(hass.config_entries.async_remove(config_entry.entry_id))

    return True


async def async_unload_entry(
    hass: HomeAssistant, config_entry: config_entries.ConfigEntry
) -> bool:
    """Will be called once we remove it."""
    return True

class Zone(collection.CollectionEntity):
    """Representation of a Zone."""

    editable: bool

    def __init__(self, config: ConfigType, editable: bool = True) -> None:
        """Initialize the zone."""
        self._config = config
        self.editable = editable
        self._attrs: dict | None = None
        self._remove_listener: Callable[[], None] | None = None
        self._persons_in_zone: set[str] = set()

    @classmethod
    def from_storage(cls, config: ConfigType) -> Self:
        """Return entity instance initialized from storage."""
        return cls.from_config(config, True)

    @classmethod
    def from_yaml(cls, config: ConfigType) -> Self:
        """Return entity instance initialized from yaml."""
        return cls.from_config(config, False)

    @classmethod
    def from_config(cls, config: ConfigType, editable: bool) -> Self:
        """Return entity instance initialized from config."""
        type: str = config[CONF_TYPE]

        zone = None
        if(type == ZONE_TYPE_CIRCLE or type == None):
            zone = CircleZone(config, editable)
        elif(type == ZONE_TYPE_POLYGON):
            zone = PolygonZone(config, editable)
        elif(type == ZONE_TYPE_MULTI):
            zone = MultiZone(config, editable)
        else:
            raise ValueError(f"Unknown zone type {type}")

        zone._generate_attrs()
        return zone

    @property
    def state(self) -> int:
        """Return the state property which indicates the number of people within a zone."""
        return len(self._persons_in_zone)

    @property
    def name(self) -> str:
        """Return name."""
        return cast(str, self._config[CONF_NAME])

    @property
    def unique_id(self) -> str | None:
        """Return unique ID."""
        return self._config.get(CONF_ID)

    @property
    def icon(self) -> str | None:
        """Return the icon if any."""
        return self._config.get(CONF_ICON)

    @property
    def should_poll(self) -> bool:
        """Zone does not poll."""
        return False

    async def async_update_config(self, config: ConfigType) -> None:
        """Handle when the config is updated."""
        if self._config == config:
            return
        self._config = config
        self._generate_attrs()
        self.async_write_ha_state()

    @callback
    def _person_state_change_listener(self, evt: Event) -> None:
        person_entity_id = evt.data[ATTR_ENTITY_ID]
        cur_count = len(self._persons_in_zone)
        if self._state_is_in_zone(evt.data.get("new_state")):
            self._persons_in_zone.add(person_entity_id)
        elif person_entity_id in self._persons_in_zone:
            self._persons_in_zone.remove(person_entity_id)

        if len(self._persons_in_zone) != cur_count:
            self._generate_attrs()
            self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        person_domain = "person"  # avoid circular import
        persons = self.hass.states.async_entity_ids(person_domain)
        for person in persons:
            if self._state_is_in_zone(self.hass.states.get(person)):
                self._persons_in_zone.add(person)
        self._generate_attrs()

        self.async_on_remove(
            event.async_track_state_change_filtered(
                self.hass,
                event.TrackStates(False, set(), {person_domain}),
                self._person_state_change_listener,
            ).async_remove
        )

    @callback
    def _generate_attrs(self) -> None:
        """Generate new attrs based on config."""
        attrs = {
            ATTR_PASSIVE: self._config[CONF_PASSIVE],
            ATTR_EDITABLE: self.editable,
            ATTR_PERSONS: sorted(self._persons_in_zone),
        }

        attrs.update(self._generate_specific_attrs())
        self._attr_extra_state_attributes = attrs

    @callback
    def _state_is_in_zone(self, state: State | None) -> bool:
        """Return if given state is in zone."""
        return (
            state is not None
            and state.state
            not in (
                STATE_NOT_HOME,
                STATE_UNKNOWN,
                STATE_UNAVAILABLE,
            )
            and (
                state.state.casefold() == self.name.casefold()
                or (state.state == STATE_HOME and self.entity_id == ENTITY_ID_HOME)
            )
        )

    @abstractmethod
    def _generate_specific_attrs(self) -> None:
        """Generate attributes that are specific to a given type of zone."""

    @abstractmethod
    def is_in_zone(self, position: tuple[float, float]) -> bool:
        """Return if given position is in zone."""

class CircleZone(Zone):
    """Representation of a Circle Zone."""

    def __init__(self, config: ConfigType, editable: bool) -> None:
        """Initialize the circle zone."""
        super().__init__(config, editable)

    def is_in_zone(self, position: tuple[float, float], radius: float = 0) -> bool:
        """Return if given position is in zone."""
        if self.state == STATE_UNAVAILABLE:
            return False

        zone_dist = distance(
            position[0],
            position[1],
            self.attributes[ATTR_LATITUDE],
            self.attributes[ATTR_LONGITUDE],
        )

        if zone_dist is None or self.attributes[ATTR_RADIUS] is None:
            return False
        return zone_dist - radius < cast(float, self.attributes[ATTR_RADIUS])

    def _generate_specific_attrs(self) -> None:
        """Generate specific attributes for this zone."""
        return {
            ATTR_LATITUDE: self._config[CONF_LATITUDE],
            ATTR_LONGITUDE: self._config[CONF_LONGITUDE],
            ATTR_RADIUS: self._config[CONF_RADIUS],
        }

class PolygonZone(Zone):
    """Representation of a Polygon Zone."""

    def __init__(self, points: list[tuple[float, float]]) -> None:
        """Initialize the polygon zone."""
        self.points = points

    def is_in_zone(self, position: tuple[float, float]) -> bool:
        """Return if given position is in zone."""
        return self.point_in_polygon(position, self.points)

    def point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
        """Return if given point is in polygon."""
        x = point[0]
        y = point[1]
        return False

    def _generate_specific_attrs(self) -> dict:
        """Generate specific attributes for this zone."""
        return {
            ATTR_POINTS: self.points,
        }

class MultiZone(Zone):
    """Representation of a Multi Zone."""

    def __init__(self, config: ConfigType, editable: bool) -> None:
        """Initialize the multi zone."""

        Zone.from_config
        self.zones = zones

    def is_in_zone(self, position: tuple[float, float]) -> bool:
        """Return if given position is in zone."""
        for zone in self.zones:
            if zone.is_in_zone(position):
                return True
        return False
