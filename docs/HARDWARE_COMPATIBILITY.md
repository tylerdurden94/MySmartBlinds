# ESP32 hardware compatibility

This project is built for the **classic ESP32**, using PlatformIO's `esp32dev`
target with a 4 MB flash configuration. The reference installation uses the
ESP32-WROOM-32/32D development-board family. A successful compile does not prove
that every board or radio arrangement has been tested with a blind.

## Which boards can I use?

Read the full marking on the metal module shield and the development-board
model. A listing that says only "ESP32" or "WROOM" is not specific enough.

| Module / family | Project status | Installation notes |
| --- | --- | --- |
| Classic ESP-WROOM-32 / ESP32-WROOM-32 / ESP32-WROOM-32D USB development board | Reference family used for this project | Follow the beginner guide with the supplied configuration |
| **ESP32-WROOM-32U USB development board** | **Expected compatible; not physically validated with this project** | Same classic ESP32 build; attach the correct external 2.4 GHz antenna before use |
| ESP32-WROOM-32E / ESP32-WROOM-32UE USB development board | Expected compatible on a conventional classic-ESP32 board meeting the requirements below; not physically validated here | Keep the supplied 4 MB configuration on boards with at least 4 MB flash; UE requires an external antenna; extra flash/PSRAM is not needed |
| ESP32-S3, C3, C6, or another different ESP32 chip family | Not supported by the supplied build/instructions | Some have Wi-Fi and BLE, but need a separate target and hardware validation; do not upload this classic-ESP32 build to them |
| ESP32-S2 or ESP8266 | Not suitable as a standalone bridge for this project | They do not provide the required built-in Bluetooth LE radio |
| Bare WROOM module, without a USB development board | Outside the beginner workflow | Requires suitable power, programming and reset/boot hardware; use an assembled USB development board for these instructions |

The expected-compatibility entries are an engineering assessment based on the
module specifications and this firmware's requirements, **not a hardware test
result or a guarantee for every third-party board**. WROOM-32E/UE are classic
ESP32 modules; ESP32-S3-WROOM and ESP32-C3-WROOM are different chip families.

Espressif documents the shared platform and antenna differences in its
[WROOM-32D/32U datasheet](https://documentation.espressif.com/esp32-wroom-32d_esp32-wroom-32u_datasheet_en.html)
and [WROOM-32E/32UE datasheet](https://documentation.espressif.com/esp32-wroom-32e_esp32-wroom-32ue_datasheet_en.html).
See also the [PlatformIO esp32dev target](https://docs.platformio.org/en/stable/boards/espressif32/esp32dev.html).

## Check the complete development board

For the same beginner workflow, use a board with:

- A classic ESP32 module and **at least 4 MB flash**, compatible with the
  `esp32dev` target and its standard flash/partition settings.
- A working USB-to-serial connection and onboard power regulator. USB connector
  shape and the number of header pins do not by themselves identify the chip.
- A **BOOT button connected to GPIO0** and an **EN/RESET button**, or equivalent
  board-documented controls. This firmware uses GPIO0 for setup recovery.
- A suitable antenna, reliable USB power, and access to 2.4 GHz home Wi-Fi.

The bridge uses the board's Wi-Fi and Bluetooth radios; no wires connect it to
the blind motors. Follow the board manufacturer's USB driver and power guidance.
If its documented flash, boot or upload settings differ, have those checked
before proceeding rather than guessing from a similar-looking board.

## Step by step: ESP32-WROOM-32U

1. Confirm the shield says **ESP32-WROOM-32U** and that it is mounted on a USB
   development board. If you only have the small module itself, this USB guide
   does not cover assembling the additional circuitry.
2. With power disconnected, attach the board manufacturer's specified **2.4 GHz
   external antenna**, using the matching miniature connector. The 32U uses an
   external antenna instead of the 32D's printed antenna. It serves both Wi-Fi
   and Bluetooth. Seat it carefully and do not run radio tests with it missing.
3. Download and open the project's **esp_bridge** folder as described in
   [beginner steps 2–3](BEGINNER_GUIDE.md#2-export-your-private-blind-backup).
   Use a USB data cable and install the board's USB serial driver if needed.
4. **Keep the supplied `platformio.ini` unchanged** for a conventional compatible
   4 MB 32U development board. It already contains `board = esp32dev`. The name
   **esp32-wroom-32d** in PlatformIO's task list is just this project's environment
   label; it does not limit that target to a 32D antenna/module version.
5. Under **Project Tasks → esp32-wroom-32d → General**, choose **Build**, then
   **Upload**, exactly as in beginner step 4. If the upload waits at Connecting,
   use the BOOT/EN procedure there. No alternate SmartThings driver is needed.
6. After startup, check for **MSB-Setup-xxxx** and follow beginner steps 5–8:
   configure Wi-Fi, save the private token, import the backup, enable one blind,
   install/discover with the same Edge driver, and enter this bridge's token.
7. Test near one charged blind first. Confirm Wi-Fi stays connected, the range
   scan sees the motor, Refresh returns readings, and observed Open/Close movement
   works. Then test at the intended location. An external antenna does not
   guarantee greater range; its design, orientation, obstructions, and placement
   affect both links.
8. Keep the full board model, module marking, flash capacity, antenna details,
   and test outcome when reporting compatibility. Omit tokens and pairing keys.

The same workflow is expected for conventional WROOM-32E/32UE development boards
that meet the requirements above. The 32E has a printed antenna; the 32UE needs
the specified external antenna. Keep extra memory features unused with this
project's supplied configuration. Report successful real-motor testing before
calling a particular board validated.

## If a 32U uploads but communication is unreliable

First check the external antenna connection and suitability. Test both home Wi-Fi
and Bluetooth to a nearby motor; working Wi-Fi alone does not establish Bluetooth
coverage. Try a different antenna orientation and move away from metal enclosures.
Use a reliable power supply and close the original app before motor tests.
Follow [Troubleshooting](TROUBLESHOOTING.md), or add a bridge near a distant group
using the [multiple-bridge instructions](BEGINNER_GUIDE.md#9-use-multiple-esp32-bridges-for-better-coverage).
