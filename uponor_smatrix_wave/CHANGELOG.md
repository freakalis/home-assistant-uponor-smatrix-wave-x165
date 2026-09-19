# Changelog

## 0.2.0

- Add `aarch64` builds for 64-bit Raspberry Pi, Home Assistant Green and Home
  Assistant Yellow.
- Keep ARM64 hardware support marked as unvalidated until sustained RTL-SDR
  reception and USB reconnect recovery have been tested on a physical host.

## 0.1.0

- First repository release for Home Assistant OS on amd64.
- Receive Uponor Smatrix Wave X-165 and T-165 RF data with an RTL-SDR receiver.
- Publish thermostat, controller and I-167 observations through MQTT Discovery.
- Import `U_BACKUP.TXT` and `U_LOGA.TXT` through the authenticated app web UI.
