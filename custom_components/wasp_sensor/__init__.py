"""
Custom integration for Wasp Sensor

For more details about this integration, please refer to
https://github.com/benbraun/hass-wasp_sensor
"""

import asyncio
import logging
from typing import List

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant import config as conf_util
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import discovery
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration
from voluptuous.schema_builder import ALLOW_EXTRA, PREVENT_EXTRA

from .const import (
    BINARY_SENSOR,
    CONF_BOX_INV_SENSORS,
    CONF_BOX_SENSORS,
    CONF_NAME,
    CONF_SENSOR_CHANGE_DELAY,
    CONF_TIMEOUT,
    CONF_WASP_INV_SENSORS,
    CONF_WASP_SENSORS,
    DEFAULT_SENSOR_CHANGE_DELAY,
    DEFAULT_WASP_TIMEOUT,
    DOMAIN,
    SERVICE_RELOAD,
    STARTUP_MESSAGE,
)

_LOGGER: logging.Logger = logging.getLogger(__package__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
]

ENTRY_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_NAME): cv.string,
        vol.Optional(CONF_WASP_SENSORS, default=[]): cv.entity_ids,
        vol.Optional(CONF_WASP_INV_SENSORS, default=[]): cv.entity_ids,
        vol.Optional(CONF_BOX_SENSORS, default=[]): cv.entity_ids,
        vol.Optional(CONF_BOX_INV_SENSORS, default=[]): cv.entity_ids,
        vol.Optional(CONF_TIMEOUT, default=DEFAULT_WASP_TIMEOUT): vol.All(
            vol.Coerce(float), vol.Range(min=0)
        ),
        vol.Optional(
            CONF_SENSOR_CHANGE_DELAY, default=DEFAULT_SENSOR_CHANGE_DELAY
        ): vol.All(vol.Coerce(float), vol.Range(min=0)),
    },
    extra=PREVENT_EXTRA,
)

CONFIG_SCHEMA = vol.Schema({vol.Optional(DOMAIN): [ENTRY_SCHEMA]}, extra=ALLOW_EXTRA)


class EntityRegistry:
    """Handle Registering Entities for later Destruction"""

    def __init__(self) -> None:
        self.registered_entities: List[BinarySensorEntity] = []

    async def register_entities(self, entities: List[BinarySensorEntity]) -> None:
        """Perform Entity Registration"""
        for entity in entities:
            self.registered_entities.append(entity)

    async def shutdown(self):
        """Destroy all Entities"""
        for entity in self.registered_entities:
            await entity.async_remove()

        self.registered_entities = []


async def async_setup(hass: HomeAssistant, hass_config: ConfigType) -> bool:
    """Component setup."""
    if hass.data.get(DOMAIN) is None:
        _LOGGER.info(STARTUP_MESSAGE)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN] = hass_config.get(DOMAIN, [])

    registry = EntityRegistry()

    setup_task = hass.async_create_task(start_it_up(hass, hass_config, registry))

    reload_lock = asyncio.Lock()

    async def reload_scripts_handler(_) -> None:
        async with reload_lock:
            await setup_task
            await reload_yaml()

    async def reload_yaml() -> None:
        """Handle reload service calls."""
        _LOGGER.debug("reloading")

        try:
            unprocessed_conf = await conf_util.async_hass_config_yaml(hass)
        except HomeAssistantError as err:
            _LOGGER.error(err)
            return

        conf = await conf_util.async_process_component_and_handle_errors(
            hass, unprocessed_conf, await async_get_integration(hass, DOMAIN)
        )

        if conf is None:
            return

        await registry.shutdown()
        hass.data[DOMAIN] = conf.get(DOMAIN, [])
        await start_it_up(hass, conf, registry)

    hass.services.async_register(DOMAIN, SERVICE_RELOAD, reload_scripts_handler)

    return True


async def start_it_up(
    hass: HomeAssistant, hass_config: ConfigType, registry: EntityRegistry
):
    """Handle Startup Tasks"""

    if not hass.data[DOMAIN]:
        return
    ready = hass.loop.create_future()
    config = {
        "registrar": registry.register_entities,
        "entities": hass.data[DOMAIN],
        "ready": ready,
    }

    await discovery.async_load_platform(
        hass,
        BINARY_SENSOR,
        DOMAIN,
        config,
        hass_config,
    )
    # Discovery dispatches setup; wait for actual entity registration as well.
    async with asyncio.timeout(60):
        await ready


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Wasp in a Box from a config entry."""

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update."""
    _LOGGER.debug("Configuration options updated, reloading Wasp in a Box integration")
    await hass.config_entries.async_reload(entry.entry_id)
