# Release notes

## v0.1.0 - 2026-10-23

Initial community source snapshot for first-generation MySmartBlinds tilt motors.

- Standalone ESP1 firmware: `multi-bridge-2026-10-17-31-bounded-startup`.
- Matching Edge source with the October 21 bounded Open/Close retry handling.
- Public driver package key separate from the private development installation.
- Beginner installation, troubleshooting, local API, and publishing guides.
- Dedicated MySmartBlinds Community channel with an enrollment invitation.
- Personal backup paths and the private pilot blind UUID removed from the snapshot.
- No ESP2 or SurgeArrest application, configured flash images, household logs,
  motor inventories, backups, or tokens.

Firmware behavior is unchanged from the ESP1 source. Tooling uses an explicit USB
port and a generic backup path. The development platform is pinned for repeatability.
This initial community release is a beta; a clean public-channel installation on
a separate user's hub has not yet been verified.
