# ESP32 bridge firmware

Use the [beginner guide](../docs/BEGINNER_GUIDE.md) for installation. This firmware
connects MySmartBlinds Bluetooth motors to the SmartThings Edge driver over your
home network. Credentials and motor records are provisioned at runtime and saved
on the ESP32, not compiled in.

## Build from a command line

Install PlatformIO Core, then run from the repository root:

```text
pio run -d esp_bridge
pio run -d esp_bridge -t upload --upload-port YOUR_USB_PORT
pio device monitor --port YOUR_USB_PORT --baud 115200
```

Replace `YOUR_USB_PORT`, for example with COM6 on Windows or /dev/ttyUSB0 on
Linux. Use the VS Code workflow in the beginner guide if these tools are unfamiliar.
The project pins Espressif32 7.1.3 and NimBLE-Arduino 2.3.5.

## Local API

`GET /v1/info` is public: bridge identity, firmware version, enabled blind names
and IDs. Normal service is port 80; compatibility port 8080 is also supported.
The following require the `X-MSB-Token` header containing your private LAN token:

```text
GET  /v1/scan
POST /v1/scan
GET  /v1/blinds/BLIND_UUID
POST /v1/blinds/BLIND_UUID/position?position=0..200
POST /v1/blinds/BLIND_UUID/poll?minutes=0|1|2|5|10|15|30
POST /v1/blinds/BLIND_UUID/refresh
```

Native position 100 is flat; native 0/200 are opposite closed tilt directions.
SmartThings percentage is `(200 - native) / 2`.
HTTP 202 means work was queued. Read state to inspect delivery and measurement.
The ESP releases its motor connection after each transaction. With polling Off,
automatic startup work ends after any valid sample or three failed no-data
attempts; explicit controls and Refresh remain available.

Optional Python helpers require `requirements-tools.txt`. The USB helper accepts
an explicit backup path; keep backups and token files outside this repository.
Never distribute full-flash or NVS images from a configured board: they can
contain Wi-Fi credentials and motor keys.
