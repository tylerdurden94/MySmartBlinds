# MySmartBlinds USB installer — first tester guide

Version **0.2.0-beta.1**, experimental. The packaged app has automated software
checks. Actual flashing, USB reset behavior and blind operation still need testing
on real hardware. Use a spare supported ESP32 for the first installation.

## Pick the download

- **Windows-x64:** for a 64-bit Intel/AMD Windows PC. Extract the ZIP, then open
  `MySmartBlinds-Setup.exe`. Python, PlatformIO and VS Code are not required.
- **macOS-AppleSilicon:** for Macs showing an Apple M-series chip in Apple menu →
  About This Mac. Extract the ZIP and open `MySmartBlinds-Setup.app`.
- **macOS-Intel:** for Macs showing an Intel processor in About This Mac.

These are unsigned test downloads. Windows may show an unknown-publisher warning;
only proceed after confirming the file came from the maintainer. On Mac, if the
verified test build is blocked, try opening it once and then System Settings →
Privacy & Security → Open Anyway. Do not disable system security globally. If it
remains blocked, report the exact message and stop. Organizational policies may
prevent unsigned apps from running. Signing/notarization are not included yet.

Keep the source ZIP and Licenses folder with the package when sharing it. The
`.sha256` file records the ZIP's checksum. A `self-test.json` file describes the
software checks performed on the build computer, not a physical board test.

## Before starting

Use a classic ESP32-WROOM USB development board with at least 4 MB flash, a USB
**data** cable, your home **2.4 GHz** Wi-Fi details, and a compatible JSON backup
exported by the MySmartBlinds Android app. The firmware does not support open
Wi-Fi networks. ESP32-S3/C3/C6/S2 and ESP8266 are not supported by this build.
For a 32U/32UE board, attach the appropriate external antenna before powering it.
See [hardware compatibility](HARDWARE_COMPATIBILITY.md).

Connect one ESP32 at a time. Close the original MySmartBlinds app and any computer
serial monitors. Installing firmware replaces the board's current application.
Setting up the board replaces its Wi-Fi, bridge token, and blind assignments.
Do not use a board containing another application you need to keep.

## Install and set up

1. Open the wizard, select **Install firmware and set up this board**, and choose
   the intended USB port. Click Refresh after connecting the board if needed.
2. On **Wi-Fi & blinds**, enter the home Wi-Fi name and password. Choose your
   private Android backup JSON. Click the blinds this board should control.
   Start with one nearby blind, and assign each blind to only one bridge.
3. On **Install & finish**, review the action, check the board confirmation, then
   click **Install and set up**. Keep USB connected until it finishes.
4. The wizard flashes the bundled firmware, verifies it, and sends configuration
   directly over USB. Joining `MSB-Setup-xxxx` or using the ESP web page is not
   required when USB setup succeeds.
5. Confirm the wizard reports that Wi-Fi connected. Copy the displayed private
   bridge token. Its recovery-file location appears below it. Keep that file
   private; the installer does not save the Wi-Fi password or a copy of your backup.
6. Open the SmartThings driver invitation from the wizard. Enroll your hub and
   install **MySmartBlinds ESP Bridge**. In SmartThings, Scan nearby, then paste
   the token into the discovered bridge device's Settings → Bridge token.
7. Refresh the blind and physically observe one Open/Close test. Firmware and
   configuration success alone do not prove a motor moved.

All imported blind records are stored on the ESP32. Only your selected blinds
are enabled. The setup operation itself does not move a motor. The ESP web page
remains available for later changes and recovery; setup login is username `msb`,
password `MySmartBlinds` on home Wi-Fi.

## If something fails

- **No port:** check the data cable, another USB port, and the board manufacturer's
  USB serial driver. A charging-only cable cannot flash the ESP32.
- **Cannot connect:** close other serial apps. Hold BOOT while retrying; release
  when flashing starts. Release BOOT before USB configuration. A board whose reset
  wiring differs may require a manual EN/RESET press when retrying setup.
- **Firmware installed, setup failed:** select **Set up existing bridge** and retry.
  There is no need to flash again. This action generates a new token; update it in
  SmartThings. Re-enter the password, which is cleared after every attempt.
- **Wi-Fi failed or not confirmed:** check 2.4 GHz coverage, name and password,
  then retry setup. An unconfirmed connection is not a completed installation.
- **Update layout mismatch:** nothing was written by the update. Use the manual
  guide to investigate the board's existing application and partitions.
- **Interrupted flashing:** reconnect and repeat installation on the same board.
  Do not unplug deliberately during a write.

Recovery: hold BOOT for five seconds while the installed firmware is running to
start its setup access point. Join `MSB-Setup-xxxx` using `MySmartBlinds`, then
open `http://192.168.4.1/setup`. See the [beginner guide](BEGINNER_GUIDE.md).

## Later updates

Choose **Update firmware only**. This checks the partition layout, writes only the
application, and preserves existing settings. It does not generate a token, import
a backup, or change Wi-Fi. Use it only for this project's already installed bridge.

## Report the test result

Use **Save test report** and send that file to the maintainer. It omits passwords,
Wi-Fi names, tokens, backup contents, blind names and board identifiers. Also say:

- Your OS/version, Mac chip if applicable, and ESP32 board model.
- Whether the app opened, the board appeared, flashing succeeded, USB setup
  completed, Wi-Fi connected, and SmartThings could control one blind.
- Any exact error message or needed BOOT/RESET steps. Crop private details out of
  screenshots. Never send the backup JSON or token recovery file.

You can test app startup and port detection without installing anything on a board.
For an entirely hardware-free preview, start the executable with `--demo`; this
opens no serial ports and saves no credentials. Physical tests are still necessary.
