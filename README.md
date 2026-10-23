# Bring MySmartBlinds back to SmartThings and Alexa

Restore control of first-generation MySmartBlinds Bluetooth tilt motors with an
ESP32 bridge and a SmartThings hub. Control your blinds from SmartThings at home
or away, include them in routines, and use Alexa **through the SmartThings skill**.

**[SmartThings driver features and settings](docs/SMARTTHINGS_DRIVER_GUIDE.md)** —
controls, polling and battery life, movement retries, and experimental sensor readings.

## Why this project exists

MySmartBlinds ended its cloud support. The original MySmartBlinds Bridge devices
depended on that service for remote access and Alexa integration, leaving owners
with working blind motors but no working cloud bridge for those features. The
Android app's [release notes](https://play.google.com/store/apps/details?id=com.mysmartblinds2.android)
describe its move to offline operation and the addition of backup export/import.
Nearby Bluetooth control through the original app is separate from the lost
cloud bridge features.

This project provides a new path from those existing motors to SmartThings.
Install the firmware on an ESP32 and the supplied Edge driver on a compatible
SmartThings hub. The ESP32 communicates with the blinds over Bluetooth; the
driver on the hub communicates with the ESP32 over your home network. The
retired MySmartBlinds cloud service and original bridge are no longer part of
that control path.

## How SmartThings restores remote and Alexa control

**SmartThings is the connection between this bridge and Alexa.** This project
does not connect directly to Alexa or revive the old MySmartBlinds Alexa skill.

```text
Alexa voice request
  -> SmartThings skill and linked Samsung account
  -> SmartThings hub running this project's Edge driver
  -> ESP32 over your home network
  -> MySmartBlinds motor over Bluetooth
```

Once a blind works in SmartThings, link SmartThings to Alexa using the
**SmartThings skill** and discover the devices in Alexa. Alexa can then control
the blinds exposed by SmartThings and include them in Alexa groups or routines.
See [Samsung's SmartThings/Alexa setup guide](https://www.samsung.com/us/support/answer/ANS10006871/)
and the [beginner guide](docs/BEGINNER_GUIDE.md#use-remote-control-and-alexa).

For control away from home, use the SmartThings app with your hub online.
Communication from the hub to the motors stays local, while remote app access
and Alexa depend on internet access and the SmartThings/Amazon services. You
still need a compatible SmartThings hub; the ESP32 alone does not provide
Alexa integration or remote access.

## What you gain

| Function | What this solution provides |
| --- | --- |
| Remote access | Control existing blinds away from home through SmartThings |
| Alexa voice control | Control through Alexa's SmartThings skill after linking accounts and discovering the blinds |
| Smart home routines | Combine blind control with schedules, scenes, sensors, and other devices supported by SmartThings |
| Individual blind control | A separate SmartThings device for each blind, with adjustable Open/Close presets and a tilt slider |
| Battery and charging information | Battery level and solar/USB charging state alongside the controls |
| Flexible coverage | Use multiple ESP32 bridges, assigning each blind to the bridge with suitable Bluetooth coverage |
| Configurable feedback | Choose a position polling interval or request a reading manually |
| Optional retries | Configure extra Open/Close attempts when a motor misses its target; disabled by default |
| Open source maintenance | Inspect, modify, and build the bridge firmware and driver without relying on the retired manufacturer's cloud |

Beyond recovering remote and voice control, this makes the blinds part of your
broader SmartThings home, with per-blind settings and useful sensor information.
It reuses the existing motors; it does not require replacing the blind hardware.

**Start here: [step-by-step beginner guide](docs/BEGINNER_GUIDE.md).**

This repository contains the ESP32 bridge firmware and its matching SmartThings
Edge driver. Up to ten blind records are supported per bridge.

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

Polling and extra movement attempts can increase motor battery use. See the
[driver guide](docs/SMARTTHINGS_DRIVER_GUIDE.md) for when to enable each feature.
Temperature and light interpretation are tentative: temperature/lux conversions
have not been confirmed accurate. Light is normally shown in relative sensor
units. Leave conversions disabled until you validate them yourself.
Motors are contacted serially, so group commands are not simultaneous.

## Files

| Folder | Contents |
| --- | --- |
| `esp_bridge` | ESP32 bridge firmware, optional provisioning helpers, firmware tests |
| `edge_driver_template` | Editable Edge source, capability definitions, package generator, driver tests |
| `edge_driver_build` | Generated driver package and UI definitions using the maintainer's registered namespace |
| `docs` | Beginner installation, troubleshooting, and maintainer publishing instructions |

See [release notes](CHANGELOG.md), [security notes](SECURITY.md), and
[license](LICENSE). This is an independent community project, not an official
MySmartBlinds, SmarterHome, Samsung, or SmartThings product.
