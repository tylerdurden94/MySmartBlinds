# Security and privacy

- Never publish an app backup, LAN token, Wi-Fi password, motor pairing key,
  configured-device flash dump, or NVS partition. The app backup contains keys
  even though its fields are encoded.
- Source builds are unconfigured. Your settings are provisioned on your own board.
- This release uses local HTTP. The firmware has shared setup Wi-Fi and web
  credentials (`MySmartBlinds`, web username `msb`). Anyone with suitable local
  access and those defaults may be able to change configuration. The separate
  LAN API token does not make the shared setup login unique.
- Keep the board on a trusted home network. Do not forward its HTTP ports or
  expose the setup network publicly. The setup network is for initial setup,
  Wi-Fi recovery, or a deliberate BOOT-button request; restart to turn it off.
- Browser import sends individual blind records over local HTTP. Use the USB
  provisioning helper if motor keys must not travel over Wi-Fi.
- `GET /v1/info` intentionally exposes enabled blind names/IDs and bridge identity
  to the LAN. Raw sensor history and logs can also reveal household details.
- The bridge stores keys and Wi-Fi settings in flash without application-level
  encryption. Treat physical access to a configured board as access to its secrets.

Report a security concern through GitHub's private vulnerability reporting if
enabled. If unavailable, open an issue asking for a private reporting route and
omit exploitation details and credentials. Do not attach a private backup to
an issue. The maintainer has not promised a response time or security audit.
