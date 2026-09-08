# Home Assistant app: Uponor Smatrix Wave X-165

Unofficial, local and receive-only Home Assistant app for observed Uponor
Smatrix Wave X-165 controllers, I-167 displays and T-165 thermostats. An
RTL-SDR receiver listens at 868.250 MHz and the app publishes decoded values
through MQTT Discovery.

[![Add repository to Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Ffreakalis%2Fhome-assistant-uponor-smatrix-wave-x165)

## Requirements

- Home Assistant OS on `amd64` hardware
- An RTL2832U-compatible RTL-SDR receiver with an antenna for 868 MHz
- An MQTT broker reachable from Home Assistant
- An Uponor Smatrix Wave X-165 installation

The tested receiver reports a Rafael Micro R820T-family tuner. Other hardware
and architectures have not yet been validated.

## Installation

1. Use the button above, or add this repository URL in **Settings → Apps → App
   store → Repositories**.
2. Install **Uponor Smatrix Wave X-165**.
3. Connect the RTL-SDR receiver and configure the MQTT broker.
4. Start the app and open **Open Web UI** to upload `U_BACKUP.TXT` and
   `U_LOGA.TXT`, or enter the controller and display IDs manually.

Only one program can use the RTL-SDR receiver at a time. Do not run another
receiver with the same MQTT topic prefix.

## Current scope

The app receives thermostat temperature and setpoint reports, actuator and
bypass state, a provisional I-167 house temperature and observed home/away
mode. It cannot transmit RF commands or change setpoints. Protocol support is
based on controlled observations and remains incomplete.

This independent project is not affiliated with or endorsed by Uponor.

Released under the [MIT License](LICENSE). Third-party components retain their
own licenses; see [third-party notices](THIRD_PARTY_NOTICES.md).
