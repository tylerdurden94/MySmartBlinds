# Desktop USB setup wizard

Experimental installer version 0.2.0-beta.1. See the [tester guide](../docs/INSTALLER_GUIDE.md).
The bridge firmware is unchanged. No board is flashed by builds or automated tests.

Build separately on Windows x64, Intel macOS, and Apple Silicon macOS with Python
3.12 including Tk. The GitHub workflow builds on each OS and runs the packaged app's
hardware-free self-test. This is a prerequisite for hardware testing, not proof of
USB reset timing, driver compatibility, or working blinds.

```text
python -m pip install platformio==6.2.0
python -m pip install -r installer/requirements-build.txt
python -m pip download --no-deps --no-binary=esptool --no-build-isolation --dest installer/third-party-source esptool==4.11.0
python -m unittest discover -s installer/tests -v
pio run -d esp_bridge
python installer/build.py --framework-dir PATH_TO_PLATFORMIO/packages/framework-arduinoespressif32
```

The build copies only bootloader, partition table, stock boot_app0 and application
binaries from a clean compile. Never supply a full-flash dump or NVS backup.
Manifest SHA256 hashes are checked before flashing. New installs use separate
images and leave the NVS sectors untouched. Updates read and compare the partition
table, then write only the application. No erase-all or force flags are used.

The installer has three explicit actions: install and configure, app-only update,
and configure an already installed bridge. Configuration generates a unique LAN
token, saves a private recovery file before changing the board, resets into normal
boot and uses the existing CONFIG_READY/BEGIN/BLIND/COMMIT protocol. Only a valid
home-network IP reported by firmware is treated as a confirmed Wi-Fi connection.
An absent IP is shown as unconfirmed, and an explicit connection failure is an error.

Preview without hardware: run `python installer/app.py --demo`. A frozen executable
also accepts `--demo`. `--self-test REPORT.json` verifies firmware hashes, imports
the flashing dependencies, and constructs all GUI tabs without enumerating or
opening serial ports. It writes a machine-readable report and exits.

Installer source is GPL-2.0-or-later because it incorporates esptool. The rest of
the project's original source retains its MIT license. Include the source ZIP and
Licenses directory when sharing a binary package. Source and build instructions
are included in every package; upstream component sources and notices are linked
in THIRD_PARTY_NOTICES.md. Public test builds are unsigned and not notarized.

No remote update check, telemetry, backup upload, Wi-Fi switching, or blind movement
is performed by the wizard. External links open only when the user clicks them.
