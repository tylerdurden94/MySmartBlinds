# Initial release validation

Checks performed on the clean source snapshot on 2026-10-23:

- The published firmware source and storage layout were verified against the
  tested bridge implementation.
- All 52 existing Lua driver tests passed against the public driver source.
- All 21 existing firmware lifecycle scenarios passed using the host radio
  simulation and Zig 0.13.0 as the C++ compiler.
- A fresh ESP32 build passed using Espressif32 7.1.3 and NimBLE-Arduino 2.3.5,
  with development dependencies downloaded outside the OneDrive workspace.
  Flash usage: 1,148,009 / 1,310,720 bytes. RAM usage: 62,084 / 327,680 bytes.
- SmartThings CLI 2.1.2 successfully built the generated driver package as a
  local ZIP without deploying it to the owner's hub.

These checks do not replace a clean public-channel installation on another
user's hub, Bluetooth range testing, or observing actual motor movement.
No ESP32 firmware was uploaded and no installed SmartThings device was changed
as part of source release preparation.
