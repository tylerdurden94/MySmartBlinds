# MySmartBlinds ESP32 bridge for SmartThings

Control first-generation MySmartBlinds Bluetooth tilt motors from SmartThings
using an ESP32 and a local SmartThings Edge driver. The ESP32 talks to the motors
over Bluetooth and to your hub over your home network. The original MySmartBlinds
cloud bridge is not required for this connection.

**Start here: [step-by-step beginner guide](docs/BEGINNER_GUIDE.md).**

This repository contains the standalone **ESP1** firmware and its matching Edge
driver. There is no ESP2 firmware or SurgeArrest control in this release. You can
use multiple boards running this same standalone firmware, assigning each blind
to one board. Up to ten blind records are supported per board.

## Before you start

- A classic ESP32-WROOM-32/32D development board with Bluetooth LE and a USB port.
  This build targets `esp32dev`; other ESP32 variants have not been validated.
- A USB data cable, a computer, and 2.4 GHz Wi-Fi.
- Working first-generation MySmartBlinds motors and a private JSON backup from
  the MySmartBlinds Android app. The backup supplies each motor's pairing key.
- A SmartThings hub that supports LAN Edge drivers and the SmartThings app.

Your phone, hub, and ESP32 must be able to communicate on the same local network.
Guest network isolation can prevent discovery. No port forwarding is needed.

## Install the SmartThings driver

**[Install from the MySmartBlinds Community channel](https://bestow-regional.api.smartthings.com/invite/gV2qaXZ9de29).**
The source and prepared driver package are also included for advanced installation;
see [publishing and build instructions](docs/PUBLISHING.md).

Users accept the invitation, enroll their hub, and install
**MySmartBlinds ESP Bridge**. They then scan for devices in SmartThings and put
their own ESP32 token into the discovered bridge device's Settings.

## What it supports

- Open/Close presets and a tilt slider: **50% is flat/open**, while 0% and 100%
  are the two closed tilt directions. This is tilt, not blind lifting.
- Battery level, solar/USB charging state, and raw sensor readings.
- Configurable position polling, including Off, and manual refresh.
- Optional extra Open/Close movement attempts, disabled by default.
- Per-blind assignment across multiple standalone ESP32 boards.

Light is reported in relative sensor units. Temperature and lux conversions
require calibration; leave them disabled until you validate them yourself.
Motors are contacted serially, so group commands are not simultaneous.

## Files

| Folder | Contents |
| --- | --- |
| `esp_bridge` | Standalone ESP1 firmware, optional provisioning helpers, firmware tests |
| `edge_driver_template` | Editable Edge source, capability definitions, package generator, driver tests |
| `edge_driver_build` | Generated driver package and UI definitions using the maintainer's registered namespace |
| `docs` | Beginner installation, troubleshooting, and maintainer publishing instructions |

See [release notes](CHANGELOG.md), [security notes](SECURITY.md), and
[license](LICENSE). This is an independent community project, not an official
MySmartBlinds, SmarterHome, Samsung, or SmartThings product.
