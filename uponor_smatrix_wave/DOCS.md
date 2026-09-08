# Uponor Smatrix Wave X-165

This unofficial Home Assistant app listens for observed Uponor Smatrix Wave RF
messages at 868.250 MHz and publishes decoded state through MQTT Discovery. It
has been tested on Home Assistant OS running on an amd64 Intel NUC.

## Hardware

Connect an RTL2832U-compatible RTL-SDR receiver and an antenna suitable for the
European 868 MHz band to the Home Assistant host. The tested receiver identifies
as `Generic RTL2832U OEM` with a Rafael Micro R820T-family tuner. The app uses
the Linux `rtl-sdr` utility included in its container; Windows DLL files are not
needed. Only one app or process can use the receiver at a time.

The default radio settings are 868250000 Hz, 250000 samples/s, PPM correction
0 and receiver index 0.

Home Assistant maps the receiver into the container because `config.json` sets
`usb: true`. The included AppArmor profile permits the required
`/dev/bus/usb` access while protection mode remains enabled. No firewall rule,
host networking, privileged mode or fixed USB device path is required.

## Initial setup

1. Install the app and connect the RTL-SDR receiver.
2. Configure `mqtt_host`, `mqtt_port`, `mqtt_username` and `mqtt_password`.
3. Start the app. It may remain running without a controller ID so that its
   setup page stays available.
4. On the app's Info page, select **Open Web UI** and upload copies of
   `U_BACKUP.TXT` and `U_LOGA.TXT` from the X-165 microSD card. The page
   validates both files and shows candidate controller, display and thermostat
   IDs. The receiver starts automatically after a valid upload.
5. Alternatively, enter the eight-digit hexadecimal `controller_id` manually.
   Add `interface_id` if I-167 house temperature and system mode are wanted.
6. Enable **Start on boot** after reception has been verified.

Allow several minutes for periodic thermostat reports. The log should first
show the RTL-SDR opening and MQTT connection, followed by decoded reports.

## Getting the SD-card files

Use the microSD card from the X-165 controller. Make the controller powerless
before removing the card. Insert it into a computer and copy `U_BACKUP.TXT` and
`U_LOGA.TXT` without opening, editing or renaming them. Although their extension
is TXT, the files contain binary data. Keep the originals on the card, eject it
from the computer, return it to the same controller while power is still off,
and then restore power.

Use files from the same controller and copying occasion. `U_BACKUP.TXT`
provides observed I-167 and thermostat candidates; `U_LOGA.TXT` provides the
X-165 candidate. File layouts can vary between systems, so rejected files
should be preserved for future analysis rather than modified.

Uploaded copies are stored under `/data/sd_import` in the app's private data
volume and survive app restarts and updates. They can be removed from the same
web page. The page is available only through authenticated Home Assistant
Ingress.

## Configuration

| Option | Purpose |
| --- | --- |
| `controller_id` | X-165 ID; optional when supplied by an uploaded log |
| `interface_id` | Optional I-167 ID for house temperature and system mode |
| `mqtt_host`, `mqtt_port` | MQTT broker address and port |
| `mqtt_username`, `mqtt_password` | MQTT broker credentials |
| `topic_prefix` | MQTT state prefix, default `uponor` |
| `discovery_prefix` | MQTT Discovery prefix, default `homeassistant` |
| `stale_after` | Seconds before an entity becomes unavailable |
| `device` | RTL-SDR receiver index |
| `frequency`, `sample_rate`, `ppm` | Radio tuning parameters |
| `workers` | Decoder workers; keep the default 1 unless decoding falls behind |
| `threshold_db` | RF burst detection threshold |
| `debug_device` | Optional thermostat ID for focused debug output |

The legacy `sd_import`, `sd_backup_file` and `sd_log_file` options support
reading copies from Home Assistant's `/share` directory. Uploading through
**Open Web UI** is the normal setup method.

## Entities

Each observed thermostat can publish room temperature, setpoint, last seen,
bypass and actuator state. The actuator state only says whether the output is
open; bypass can open it without ordinary temperature demand.

When an I-167 ID is configured, the app can also publish a provisional house
temperature and an observed home/away system mode on an X-165 device. Scheduled
ECO and forced Away are not yet distinguished.

The app has no command topics and cannot transmit RF. The RTL-SDR hardware used
for reception cannot set temperatures.

## Troubleshooting

- If the RTL-SDR cannot be opened, first confirm that it appears under
  **Settings → System → Hardware**, then stop any other SDR app and restart this
  app after reconnecting the receiver. Keep protection mode enabled. Include
  any `apparmor="DENIED"` message from the host audit log in a bug report.
- If no reports appear, verify the antenna, radio settings and controller ID.
  Wait at least ten minutes before judging periodic reception.
- If MQTT connects but entities do not appear, verify the discovery prefix and
  that MQTT Discovery is enabled in Home Assistant.
- Run only one bridge per topic prefix. Two bridges would share retained state
  and availability topics.
- Use `debug_device` to focus logs on one thermostat.

This independent project is not affiliated with or endorsed by Uponor.
