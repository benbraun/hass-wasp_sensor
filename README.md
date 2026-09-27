# Home Assistant Wasp Sensor

Wasp Sensor infers occupancy from motion inside a closed room. Once motion is
confirmed with all doors closed, occupancy stays on until a door opens. It is a
local, event-driven helper with no network dependencies or polling.

Requires Home Assistant 2026.9 or later. Install with HACS using the custom
integration repository `https://github.com/benbraun/hass-wasp_sensor`, or copy
`custom_components/wasp_sensor` into your Home Assistant `custom_components`
directory and restart Home Assistant.

## Configuration

Add **Wasp Sensor** under Settings → Devices & services, or keep existing YAML:

```yaml
wasp_sensor:
  - name: office
    wasp_sensors:
      - binary_sensor.office_motion
    box_sensors:
      - binary_sensor.office_door
    timeout: 65
    sensor_change_delay: 1
```

| Setting | Meaning | Default |
| --- | --- | --- |
| `name` | Required sensor name | — |
| `wasp_sensors` | Motion is active when `on` | `[]` |
| `wasp_inv_sensors` | Motion is active when `off` | `[]` |
| `box_sensors` | Door is closed when `off` | `[]` |
| `box_inv_sensors` | Door is closed when `on` | `[]` |
| `timeout` | Confirmation delay for motion already active when the door closes | 5 seconds |
| `sensor_change_delay` | Debounce for new motion while the door is closed | 1 second |

YAML delays are nonnegative seconds; UI delays use duration selectors. Zero
disables the relevant delay. UI entries can be adjusted via their options.
Existing YAML unique IDs (`wasp_sensor_<name>`) and UI entry IDs are preserved.
YAML sensors stay YAML sensors; adding a UI entry does not migrate them.

Call `wasp_sensor.reload` to apply YAML edits without restarting. Invalid YAML
leaves existing sensors running. Installing updated Python code still requires a
Home Assistant restart.

## Occupancy behavior

- New motion while all doors are closed latches occupancy after the debounce.
- Opening any door immediately clears occupancy and cancels pending timers.
- Closing a door while motion is already active starts the full timeout. Opening
  and closing again restarts it; an earlier motion event cannot bypass it.
- A short motion pulse is ignored. Motion ending after occupancy is latched does
  not clear the latch.
- Attribute-only updates (such as a door's battery level) do not reset occupancy.
- Missing, unknown, or unavailable doors do not count as closed and clear the
  latch. Missing motion sensors do not count as active; they do not clear a latch
  by themselves. A restored latch is retained only if all doors are known closed.
- With no doors configured, the box is always considered closed, so confirmed
  occupancy remains latched. Configure door/exit sensors if it needs to reset.
- At startup, active motion in a closed room is confirmed after the timeout.

This helper represents the latched part of occupancy. If you also want occupancy
while a door is open, combine it with raw motion in a template binary sensor:

```yaml
template:
  - binary_sensor:
      - name: Office Occupied
        device_class: occupancy
        state: >-
          {{ is_state('binary_sensor.office_motion', 'on')
             or is_state('binary_sensor.office', 'on') }}
```

Use the actual Wasp entity ID from your entity registry in the template; existing
installations may retain a `wasp_sensor_` prefix or a manually assigned ID.

## Development

The tests use real Home Assistant entities, state events, registries, and platform
reloads in temporary configurations. They never connect to a running instance.

```sh
python -m pip install -r requirements_test.txt
python -m unittest discover -s tests -v
```

Or use the production runtime image:

```sh
docker run --rm -v "$PWD:/work" -w /work --entrypoint python \
  ghcr.io/home-assistant/home-assistant:2026.9.3 \
  -m unittest discover -s tests -v
```

Based on [dlashua/hass-wasp_sensor](https://github.com/dlashua/hass-wasp_sensor),
with configuration UI and translations from
[rrooggiieerr/homeassistant-wasp](https://github.com/rrooggiieerr/homeassistant-wasp).
