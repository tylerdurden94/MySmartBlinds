# Release notes

## Documentation and channel update - 2026-10-23

- Renamed the existing public channel to **TylerDurden94’s Edge Drivers** so it
  can serve multiple projects. The channel ID and MySmartBlinds driver's
  identity and assigned version remain unchanged. A new branded invitation is
  published, and the original invitation remains active.
- Updated setup and maintainer instructions to select **MySmartBlinds ESP
  Bridge** from the shared channel. Existing users need no reinstall for the rename.

## v0.1.0 - 2026-10-23

Initial community source snapshot for first-generation MySmartBlinds tilt motors.

- ESP32 bridge firmware: `multi-bridge-2026-10-17-31-bounded-startup`.
- Matching Edge source with the October 21 bounded Open/Close retry handling.
- SmartThings driver package and registered capability presentations.
- Beginner installation, troubleshooting, local API, and publishing guides.
- Community channel with an enrollment invitation, initially named MySmartBlinds
  Community and subsequently renamed TylerDurden94’s Edge Drivers.
- Generic setup paths and runtime provisioning of each user's own blinds.
- No configured flash images, household logs, motor inventories, backups, or tokens.

Tooling uses an explicit USB port and a generic backup path. The development
platform is pinned for repeatability.
This initial community release is a beta; a clean public-channel installation on
a separate user's hub has not yet been verified.
