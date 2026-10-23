# Third-party components

PlatformIO downloads development dependencies separately. Their own licenses
apply; the project MIT license does not relicense them.

- [NimBLE-Arduino](https://github.com/h2zero/NimBLE-Arduino), pinned at 2.3.5:
  see its license and bundled NimBLE notices.
- [Arduino core for ESP32](https://github.com/espressif/arduino-esp32), supplied
  by the pinned Espressif32 PlatformIO platform: see its license and notices.
- [SmartThings Edge libraries](https://github.com/SmartThingsCommunity/SmartThingsEdgeDrivers)
  are supplied by the hub's Edge runtime and retain their own licenses.
- Python helper/test dependencies are installed separately and retain their
  package licenses.

The firmware implements communication with existing MySmartBlinds devices.
Product and service names belong to their respective owners.

## Desktop installer

The `installer/` source is GPL-2.0-or-later, as identified in its source files
and `installer/LICENSE.txt`, because it incorporates
[esptool 4.11.0](https://github.com/espressif/esptool/tree/v4.11.0).
This does not change the MIT licensing of the project's original firmware,
driver, or documentation. Installer packages include a source ZIP, build
instructions, and the available license notices from bundled dependencies.

- esptool 4.11.0: GPL-2.0-or-later; corresponding source is included in the
  package's source ZIP under `third-party-source/`.
- [pyserial 3.5](https://github.com/pyserial/pyserial/tree/v3.5): BSD license.
- [PyInstaller 6.16.0](https://github.com/pyinstaller/pyinstaller/tree/v6.16.0):
  GPL with the distribution exception documented by that project.
- Python, Tcl/Tk, and esptool's dependencies retain their own licenses. The
  package's Licenses folder includes the notices available in the build environment.
- Firmware dependencies and their source releases are specified by
  `esp_bridge/platformio.ini`. Arduino framework sources used by PlatformIO:
  https://github.com/espressif/arduino-esp32/tree/2.0.17 . NimBLE-Arduino sources:
  https://github.com/h2zero/NimBLE-Arduino/tree/2.3.5 .
