"""Behavior regressions exercised against the real Home Assistant runtime."""

import asyncio
import logging
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.config_entries import ConfigEntries
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import EntityComponent

from custom_components.wasp_sensor import ENTRY_SCHEMA, async_setup, vol
from custom_components.wasp_sensor.binary_sensor import WaspBinarySensor


class WaspTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hass = HomeAssistant(self.tmp.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        dr.async_setup(self.hass)
        await dr.async_load(self.hass)
        await er.async_load(self.hass)
        self.hass.set_state(CoreState.running)
        self.component = EntityComponent(
            logging.getLogger(__name__), "binary_sensor", self.hass
        )
        self.hass.states.async_set("binary_sensor.motion", "off")
        self.hass.states.async_set("binary_sensor.door", "off")

    async def asyncTearDown(self):
        for entity in list(self.component.entities):
            await entity.async_remove()
        await self.hass.async_stop(force=True)
        self.tmp.cleanup()

    async def sensor(self, restored=None, **overrides):
        config = dict(
            name="room",
            wasp_sensors=["binary_sensor.motion"],
            wasp_inv_sensors=[],
            box_sensors=["binary_sensor.door"],
            box_inv_sensors=[],
            timeout=0.12,
            sensor_change_delay=0.02,
        )
        config.update(overrides)
        entity = WaspBinarySensor(
            BinarySensorEntityDescription(key="occupancy", name="room"),
            "wasp_sensor_room",
            self.hass,
            config,
        )
        with patch.object(
            entity, "async_get_last_state", AsyncMock(return_value=restored)
        ):
            await self.component.async_add_entities([entity])
        return entity

    async def settle(self, seconds=0.04):
        await asyncio.sleep(seconds)

    async def test_missing_sources_do_not_crash_or_close_box(self):
        entity = await self.sensor(
            wasp_sensors=["binary_sensor.missing_motion"],
            box_sensors=["binary_sensor.missing_door"],
        )
        self.assertIn(entity.entity_id, self.hass.states.async_entity_ids())
        self.assertFalse(entity.extra_state_attributes["box_closed"])

    async def test_unavailable_door_is_not_closed(self):
        self.hass.states.async_set("binary_sensor.door", "unavailable")
        entity = await self.sensor()
        self.assertFalse(entity.extra_state_attributes["box_closed"])

    async def test_motion_latches_until_door_opens(self):
        entity = await self.sensor()
        self.hass.states.async_set("binary_sensor.motion", "on")
        await self.settle()
        self.assertTrue(entity.is_on)
        self.hass.states.async_set("binary_sensor.motion", "off")
        await self.settle()
        self.assertTrue(entity.is_on)
        self.hass.states.async_set("binary_sensor.door", "on")
        await self.settle()
        self.assertFalse(entity.is_on)

    async def test_attribute_only_door_update_preserves_occupancy(self):
        entity = await self.sensor()
        self.hass.states.async_set("binary_sensor.motion", "on")
        await self.settle()
        self.hass.states.async_set("binary_sensor.motion", "off")
        await self.settle()
        self.hass.states.async_set("binary_sensor.door", "off", {"battery": 90})
        await self.settle()
        self.assertTrue(entity.is_on)

    async def test_motion_before_closing_cannot_bypass_timeout(self):
        self.hass.states.async_set("binary_sensor.door", "on")
        entity = await self.sensor()
        self.hass.states.async_set("binary_sensor.motion", "on")
        await self.settle(0.005)
        self.hass.states.async_set("binary_sensor.door", "off")
        await self.settle(0.04)
        self.assertFalse(entity.is_on)
        await self.settle(0.13)
        self.assertTrue(entity.is_on)

    async def test_reopening_cancels_old_close_timer(self):
        self.hass.states.async_set("binary_sensor.door", "on")
        self.hass.states.async_set("binary_sensor.motion", "on")
        entity = await self.sensor()
        self.hass.states.async_set("binary_sensor.door", "off")
        await self.settle(0.08)
        self.hass.states.async_set("binary_sensor.door", "on")
        await self.settle(0.005)
        self.hass.states.async_set("binary_sensor.door", "off")
        await self.settle(0.06)
        self.assertFalse(entity.is_on)
        await self.settle(0.08)
        self.assertTrue(entity.is_on)

    async def test_removed_source_does_not_crash_or_keep_box_closed(self):
        entity = await self.sensor()
        self.hass.states.async_remove("binary_sensor.door")
        await self.settle()
        self.assertFalse(entity.extra_state_attributes["box_closed"])

    async def test_startup_with_active_motion_confirms_after_timeout(self):
        self.hass.states.async_set("binary_sensor.motion", "on")
        entity = await self.sensor()
        self.assertFalse(entity.is_on)
        await self.settle(0.16)
        self.assertTrue(entity.is_on)

    async def test_short_motion_pulse_is_ignored(self):
        entity = await self.sensor()
        self.hass.states.async_set("binary_sensor.motion", "on")
        await self.settle(0.005)
        self.hass.states.async_set("binary_sensor.motion", "off")
        await self.settle()
        self.assertFalse(entity.is_on)

    async def test_ui_only_setup_does_not_require_yaml(self):
        with patch(
            "custom_components.wasp_sensor.discovery.async_load_platform", AsyncMock()
        ):
            self.assertTrue(await async_setup(self.hass, {}))

    def test_yaml_rejects_negative_delays(self):
        for key in ("timeout", "sensor_change_delay"):
            with self.subTest(key=key), self.assertRaises(vol.Invalid):
                ENTRY_SCHEMA({"name": "room", key: -1})

    def test_yaml_requires_name(self):
        with self.assertRaises(vol.Invalid):
            ENTRY_SCHEMA({})

    async def test_inverted_sources_and_duration_options(self):
        self.hass.states.async_set("binary_sensor.motion", "on")
        self.hass.states.async_set("binary_sensor.door", "on")
        entity = await self.sensor(
            wasp_sensors=[],
            wasp_inv_sensors=["binary_sensor.motion"],
            box_sensors=[],
            box_inv_sensors=["binary_sensor.door"],
            timeout={"milliseconds": 120},
            sensor_change_delay={"milliseconds": 20},
        )
        self.hass.states.async_set("binary_sensor.motion", "off")
        await self.settle()
        self.assertTrue(entity.is_on)
        self.hass.states.async_set("binary_sensor.door", "off")
        await self.settle()
        self.assertFalse(entity.is_on)

    async def test_unloading_cancels_pending_motion(self):
        entity = await self.sensor(sensor_change_delay=0.08)
        self.hass.states.async_set("binary_sensor.motion", "on")
        await self.settle(0.01)
        await entity.async_remove()
        await self.settle(0.12)
        self.assertFalse(entity.is_on)
        self.assertEqual(self.hass.states.get(entity.entity_id).state, "unavailable")

    async def test_unloading_cancels_pending_close(self):
        self.hass.states.async_set("binary_sensor.motion", "on")
        entity = await self.sensor()
        await entity.async_remove()
        await self.settle(0.16)
        self.assertFalse(entity.is_on)

    async def test_restored_occupancy_requires_known_closed_doors(self):
        from homeassistant.core import State

        restored = State("binary_sensor.room", "on", {"wasp_in_box": True})
        entity = await self.sensor(restored=restored)
        self.assertTrue(entity.is_on)
        await entity.async_remove()
        self.hass.states.async_set("binary_sensor.door", "unknown")
        entity = await self.sensor(restored=restored)
        self.assertFalse(entity.is_on)

    async def test_lost_motion_during_close_timeout_cancels_confirmation(self):
        self.hass.states.async_set("binary_sensor.motion", "on")
        entity = await self.sensor()
        self.hass.states.async_set("binary_sensor.motion", "unavailable")
        await self.settle(0.16)
        self.assertFalse(entity.is_on)

    async def test_zero_delays_are_supported(self):
        entity = await self.sensor(timeout=0, sensor_change_delay=0)
        self.hass.states.async_set("binary_sensor.motion", "on")
        await self.settle(0.01)
        self.assertTrue(entity.is_on)

    async def test_invalid_yaml_reload_preserves_existing_entity(self):
        from homeassistant.exceptions import HomeAssistantError

        entity = await self.sensor()

        async def load_platform(hass, platform, domain, info, config):
            await info["registrar"]([entity])
            info["ready"].set_result(None)

        with patch(
            "custom_components.wasp_sensor.discovery.async_load_platform", load_platform
        ):
            await async_setup(self.hass, {"wasp_sensor": [{"name": "room"}]})
            await self.hass.async_block_till_done()
        with patch(
            "custom_components.wasp_sensor.conf_util.async_hass_config_yaml",
            AsyncMock(side_effect=HomeAssistantError("invalid YAML")),
        ):
            with self.assertLogs("custom_components.wasp_sensor", level="ERROR"):
                await self.hass.services.async_call(
                    "wasp_sensor", "reload", {}, blocking=True
                )
        self.assertIsNotNone(self.hass.states.get(entity.entity_id))

    async def test_ui_flow_applies_optional_defaults(self):
        from custom_components.wasp_sensor.config_flow import WaspConfigFlow

        flow = WaspConfigFlow()
        flow.hass = self.hass
        result = await flow.async_step_user({"name": "Room"})
        self.assertEqual(result["options"]["wasp_sensors"], [])
        self.assertEqual(result["options"]["timeout"], {"seconds": 5})

    async def test_real_yaml_setup_and_concurrent_reload(self):
        from pathlib import Path

        from homeassistant import loader
        from homeassistant.setup import async_setup_component

        Path(self.tmp.name, "custom_components").symlink_to(
            Path(__file__).resolve().parents[1] / "custom_components"
        )
        loader.async_setup(self.hass)
        config = {
            "wasp_sensor": [
                {
                    "name": "test_yaml",
                    "wasp_sensors": ["binary_sensor.motion"],
                    "box_sensors": ["binary_sensor.door"],
                    "sensor_change_delay": 0,
                }
            ]
        }
        self.assertTrue(await async_setup_component(self.hass, "wasp_sensor", config))
        await self.hass.async_block_till_done()
        with patch(
            "custom_components.wasp_sensor.conf_util.async_hass_config_yaml",
            AsyncMock(return_value=config),
        ):
            await asyncio.gather(
                *[
                    self.hass.services.async_call(
                        "wasp_sensor", "reload", {}, blocking=True
                    )
                    for _ in range(3)
                ]
            )
        self.hass.states.async_set("binary_sensor.motion", "on")
        await self.settle()
        sensors = [
            s
            for s in self.hass.states.async_all()
            if s.entity_id.startswith("binary_sensor.test_yaml")
        ]
        self.assertEqual(len(sensors), 1)
        self.assertEqual(sensors[0].state, "on")

    async def test_real_ui_setup_options_reload_and_unload(self):
        from pathlib import Path

        from homeassistant import loader
        from homeassistant.setup import async_setup_component

        Path(self.tmp.name, "custom_components").symlink_to(
            Path(__file__).resolve().parents[1] / "custom_components"
        )
        loader.async_setup(self.hass)
        await self.hass.config_entries.async_initialize()
        self.assertTrue(await async_setup_component(self.hass, "wasp_sensor", {}))
        result = await self.hass.config_entries.flow.async_init(
            "wasp_sensor",
            context={"source": "user"},
            data={
                "name": "UI Room",
                "wasp_sensors": ["binary_sensor.motion"],
                "box_sensors": ["binary_sensor.door"],
                "sensor_change_delay": {"seconds": 0},
            },
        )
        await self.hass.async_block_till_done()
        entry = result["result"]
        self.hass.states.async_set("binary_sensor.motion", "on")
        await self.settle()
        self.assertEqual(self.hass.states.get("binary_sensor.ui_room").state, "on")
        result = await self.hass.config_entries.options.async_init(
            entry.entry_id,
            data={
                "wasp_sensors": ["binary_sensor.motion"],
                "box_sensors": ["binary_sensor.door"],
                "timeout": {"seconds": 0},
                "sensor_change_delay": {"seconds": 0},
            },
        )
        await self.hass.async_block_till_done()
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(self.hass.states.get("binary_sensor.ui_room").state, "on")
        self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))
