"""Event-driven occupancy inferred from motion inside a closed room."""

from collections.abc import Callable
from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import callback
from homeassistant.helpers.entity_platform import async_get_current_platform
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity

from .const import (
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
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up legacy YAML sensors without changing their unique IDs."""
    if discovery_info is None:
        return
    entities = [
        WaspBinarySensor(
            BinarySensorEntityDescription(
                key="occupancy",
                name=item[CONF_NAME],
                device_class=BinarySensorDeviceClass.OCCUPANCY,
            ),
            f"{DOMAIN}_{item[CONF_NAME]}",
            hass,
            item,
        )
        for item in discovery_info["entities"]
    ]
    try:
        await async_get_current_platform().async_add_entities(entities)
        await discovery_info["registrar"](entities)
    finally:
        ready = discovery_info.get("ready")
        if ready is not None and not ready.done():
            ready.set_result(None)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up a UI sensor using its existing config-entry identity."""
    async_add_entities(
        [
            WaspBinarySensor(
                BinarySensorEntityDescription(
                    key="occupancy",
                    name=config_entry.data[CONF_NAME],
                    device_class=BinarySensorDeviceClass.OCCUPANCY,
                ),
                config_entry.entry_id,
                hass,
                config_entry.options,
            )
        ]
    )


def _seconds(value):
    """Accept YAML seconds and UI duration selector dictionaries."""
    return (
        timedelta(**value).total_seconds() if isinstance(value, dict) else float(value)
    )


class WaspBinarySensor(BinarySensorEntity, RestoreEntity):
    """Latch occupancy until a door opens; own every pending timer."""

    _attr_should_poll = False

    def __init__(self, entity_description, unique_id, hass, config):
        self.hass = hass
        self.entity_description = entity_description
        self._attr_unique_id = unique_id
        self._motion = {
            **dict.fromkeys(config.get(CONF_WASP_SENSORS) or [], "on"),
            **dict.fromkeys(config.get(CONF_WASP_INV_SENSORS) or [], "off"),
        }
        self._doors = {
            **dict.fromkeys(config.get(CONF_BOX_SENSORS) or [], "off"),
            **dict.fromkeys(config.get(CONF_BOX_INV_SENSORS) or [], "on"),
        }
        self._timeout = _seconds(config.get(CONF_TIMEOUT, DEFAULT_WASP_TIMEOUT))
        self._delay = _seconds(
            config.get(CONF_SENSOR_CHANGE_DELAY, DEFAULT_SENSOR_CHANGE_DELAY)
        )
        self._wasp_in_box = False
        self._box_closed = False
        self._wasp_seen = False
        self._close_cancel: Callable[[], None] | None = None
        self._motion_cancel: dict[str, Callable[[], None]] = {}

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if state := await self.async_get_last_state():
            self._wasp_in_box = state.attributes.get("wasp_in_box") is True
        self.async_on_remove(self._cancel_timers)
        if self.hass.is_running:
            self._startup()
        else:
            self.async_on_remove(
                self.hass.bus.async_listen_once(
                    EVENT_HOMEASSISTANT_STARTED, self._startup
                )
            )

    @callback
    def _startup(self, event=None):
        self._evaluate()
        sources = list(self._motion.keys() | self._doors.keys())
        if sources:
            self.async_on_remove(
                async_track_state_change_event(self.hass, sources, self._source_changed)
            )
        self._schedule_close()
        self.async_write_ha_state()

    def _matches(self, entity_id, expected):
        state = self.hass.states.get(entity_id)
        return state is not None and state.state == expected

    def _evaluate(self):
        self._wasp_seen = any(self._matches(k, v) for k, v in self._motion.items())
        # Unknown/missing doors must never count as proof the room is closed.
        self._box_closed = all(self._matches(k, v) for k, v in self._doors.items())
        if not self._box_closed:
            self._wasp_in_box = False

    @callback
    def _cancel_timers(self):
        if self._close_cancel:
            self._close_cancel()
            self._close_cancel = None
        for cancel in self._motion_cancel.values():
            cancel()
        self._motion_cancel.clear()

    @callback
    def _schedule_close(self):
        if self._close_cancel:
            self._close_cancel()
            self._close_cancel = None
        if self._box_closed and self._wasp_seen and not self._wasp_in_box:
            self._close_cancel = async_call_later(
                self.hass, self._timeout, self._confirm_close
            )

    @callback
    def _confirm_close(self, now):
        self._close_cancel = None
        self._evaluate()
        if self._box_closed and self._wasp_seen:
            self._wasp_in_box = True
        self.async_write_ha_state()

    @callback
    def _source_changed(self, event):
        old, new = event.data.get("old_state"), event.data.get("new_state")
        if old is not None and new is not None and old.state == new.state:
            return
        entity_id = event.data["entity_id"]
        self._evaluate()
        if entity_id in self._doors:
            # A motion event from before this door transition cannot latch later.
            self._cancel_timers()
            self._schedule_close()
        elif entity_id in self._motion:
            if cancel := self._motion_cancel.pop(entity_id, None):
                cancel()
            if not self._wasp_seen and self._close_cancel:
                self._close_cancel()
                self._close_cancel = None
            if self._box_closed and self._matches(entity_id, self._motion[entity_id]):

                @callback
                def confirm_motion(now):
                    self._motion_cancel.pop(entity_id, None)
                    self._evaluate()
                    if self._box_closed and self._matches(
                        entity_id, self._motion[entity_id]
                    ):
                        self._wasp_in_box = True
                    self.async_write_ha_state()

                self._motion_cancel[entity_id] = async_call_later(
                    self.hass, self._delay, confirm_motion
                )
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self):
        return {
            "wasp_in_box": self._wasp_in_box,
            "box_closed": self._box_closed,
            "wasp_seen": self._wasp_seen,
        }

    @property
    def is_on(self):
        return self._wasp_in_box
