# Troubleshooting

| Problem | What to do |
| --- | --- |
| PlatformIO build waits on OneDrive downloads | Extract the project to a local folder outside OneDrive. Build there using PlatformIO's default local tool storage. Cloud placeholders are unsuitable for compiler caches. |
| Board is absent from USB ports | Try a known data cable and another USB port. Check Device Manager. If a serial driver is needed, obtain it from the board maker or USB chip maker. |
| Upload stays at Connecting | Hold BOOT while connecting, then release when writing starts. Close any serial monitor. Confirm the selected port belongs to this board. |
| No MSB-Setup network | Allow ten seconds after boot. A configured board may already be on home Wi-Fi. After normal startup, hold BOOT for five seconds to enable setup; do not hold it during power-on unless uploading. |
| Setup page does not open | On the setup network use `http://192.168.4.1/setup`. On home Wi-Fi use the board's own IP or `.local` name. Disable a VPN temporarily if it blocks local access. |
| Board cannot join Wi-Fi | Confirm 2.4 GHz availability, SSID, and password. Use setup recovery after startup to correct them. Guest isolation can prevent LAN use even when Wi-Fi connects. |
| Token was not saved | The token is only displayed during first-time network setup. Check your private USB token file if you used USB provisioning. A configured bridge does not expose the token through its public API. Seek recovery help before erasing saved configuration. |
| Backup import fails | Use the original Android JSON export; do not edit encoded fields. The importer supports 1–10 unique blind records. Export formats from other versions/platforms may differ. |
| No blinds after import | Reload the setup page, check Enabled here for the desired blind, save, then rescan in SmartThings. Importing the inventory alone does not enable blinds. |
| SmartThings finds nothing | Verify the hub supports LAN Edge, the channel driver was installed, and hub/ESP share a LAN with multicast/mDNS allowed. Guest Wi-Fi and separate VLANs commonly prevent discovery. |
| Devices appear but controls fail | Put the correct LAN token in the matching bridge setup device. Leave the ESP powered and close the original app. Use one blind assignment per board. |
| Duplicate assignment warning | Disable that blind on all but one board and save. Run discovery again. The driver deliberately blocks ambiguous routing. |
| A command is accepted but nothing moves | Move the board nearer, charge the motor, and close the original app. Acceptance means queued work; it does not prove successful Bluetooth delivery or physical movement. |
| Battery updates but position does not | Refresh once, allow Bluetooth work to finish, and compare with the original app after the connection is released. The firmware bounds automatic startup work so a motor cannot retry indefinitely. Do not rely on unlimited retries. |
| Position looks reversed | The percentage represents tilt through both closed directions. Default flat/open is 50%. Adjust Open/Close presets to suit your installation. |
| Manual changes are slow to appear | Check Position check interval. Off requires manual refresh. Group requests are processed serially and can delay sensor reads. |
| Light/temperature looks wrong | Raw light is not lux. Temperature scaling is unverified until you calibrate it. Leave physical-unit conversion disabled without your own measurements. |
| Old target remains on screen briefly | The driver confirms commands asynchronously and then reconciles with measured values. Slow Bluetooth connections and optional retries extend this period. |

## Recover setup access

After the board finishes booting, hold BOOT for five seconds. Join its temporary
`MSB-Setup-xxxx` network using `MySmartBlinds`, then open
`http://192.168.4.1/setup`. Restart the board to disable the temporary network.
Existing Wi-Fi credentials remain unchanged unless you save new ones.

## Understand the current security model

The setup Wi-Fi password and web login are shared defaults in this firmware.
The LAN API has a separate per-board token. Local HTTP is not encrypted.
Use a trusted network, avoid guest exposure, and do not forward ports. See
[SECURITY.md](../SECURITY.md) before deploying outside a trusted home LAN.
