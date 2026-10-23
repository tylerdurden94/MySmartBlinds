# Publishing and maintaining this project

## Distribution choices

| Option | Who it serves | What users do |
| --- | --- | --- |
| Public GitHub source | Developers and anyone wanting to inspect/change the code | Download/build ESP1 firmware; advanced users package their own driver |
| Community Edge channel (recommended) | Ordinary SmartThings users | Open an invitation, enroll their hub, install the driver, then scan nearby |
| Separate beta channel | Volunteers testing updates | Explicitly enroll a test hub in the beta channel |
| Official SmartThings catalog | A supported product seeking platform certification | Complete Works with SmartThings certification and publication requirements |

GitHub does not host an installable SmartThings channel. A source ZIP is not a
channel invitation. Publishing the driver source and publishing a channel are
separate actions. Channel users do not need developer tools or their own custom
capability namespace.

Use a dedicated **MySmartBlinds Community** channel containing only this driver.
Keep the owner's existing private channel separate. A channel invitation grants
access to the drivers on that channel, so do not share a channel containing
unrelated private drivers. Add a beta channel later if desired.

The official [Driver Channels guide](https://developer.smartthings.com/docs/devices/hub-connected/driver-channels)
documents packaging, assigning a specific version, and invitations. The
[catalog publication guide](https://developer.smartthings.com/docs/devices/hub-connected/certify-your-device)
explains the separate certification route; community sharing does not require
catalog publication or imply certification.

## 1. Verify a clean release

Publish only this repository's contents. Do not initialize a public repository in
the parent private workspace or copy that workspace's history. It contains
unrelated firmware and household diagnostics.

```text
python -m pip install -r requirements-dev.txt
python edge_driver_template/test_driver.py
python esp_bridge/test_firmware.py
pio run -d esp_bridge
```

The firmware host tests require a C++17 `g++` compiler. Alternatively use
`python esp_bridge/test_firmware.py --zig /path/to/zig`.
Inspect the file inventory for backups, tokens, configured flash dumps, logs,
and personal paths. `esp_bridge/src/main.cpp` should match the standalone ESP1
source. Review the MIT license before publishing under a different owner.

## 2. Publish GitHub source

Create a public repository named **MySmartBlinds** under **TylerDurden94**. The
public root must contain README.md, esp_bridge/, edge_driver_template/,
edge_driver_build/, and docs/ directly. Commit only this clean snapshot.
Do not publish the parent's diagnostics, ESP2, migration tools, or app backups.

After confirming the public files render correctly, tag a beta source release
such as `v0.1.0`. A GitHub release automatically offers source archives. If you
provide compiled firmware, include firmware/bootloader/partition images built
from clean source with exact flash addresses and checksums. Never extract a
configured ESP32's flash as a public installation image.

This initial guide uses PlatformIO upload so users do not have to combine binary
images or choose flash offsets manually.

## 3. Package the public Edge driver

Install the official [SmartThings CLI](https://github.com/SmartThingsCommunity/smartthings-cli)
and authenticate with the Samsung account that owns the custom capabilities.
The local release preparation used CLI 2.1.2. If a newer CLI separates Edge into
a plugin, install the official Edge plugin as described by its documentation.

The prepared package uses public namespace **travelagain19498**, with the
existing registered custom capability and device presentation IDs. That
namespace does not have to match the GitHub username. No LAN token is included.

```text
python edge_driver_template/prepare_driver.py travelagain19498
smartthings edge:drivers:package edge_driver_build/package --build-only MySmartBlinds-edge.zip
smartthings edge:drivers:package edge_driver_build/package
```

The first CLI command builds a local ZIP without uploading it. The second uploads
and returns the public driver's **driverId** and **version**. Record both.
The public `packageKey` is `tylerdurden94.msb.espbridge`; it is separate
from the private pilot's package key. Do not replace it with the pilot key.

## 4. Create a dedicated community channel

From the repository root:

```text
smartthings edge:channels:create --input smartthings-community-channel.json
```

Record the returned **channelId**. The JSON gives the channel a public project
description and links the repository's license. The repository URL must exist
before distributing the invitation.

Assign the exact reviewed version, substituting actual values:

```text
smartthings edge:channels:assign DRIVER_ID DRIVER_VERSION --channel CHANNEL_ID
smartthings edge:channels:drivers CHANNEL_ID
```

Verify that the channel contains only the expected public driver and version.
Channels pin a driver version; uploading a new package alone does not promote
it into every channel. Keep beta promotion separate. Review current SmartThings
update behavior before telling users that a new version is installed; verify
the actual hub version, and provide reinstall/update steps when required.

## 5. Create and publish an invitation

```text
smartthings edge:channels:invites:create --channel CHANNEL_ID
```

Use the CLI prompts to select sharing suitable for public community enrollment
rather than restricting access to the maintainer's account. Record the returned
invitation URL. Do not invent a URL from a channel ID.

Replace the pending-channel paragraph in README.md with a Markdown link to that
exact invitation. Verify it opens the intended channel. Then follow the
[beginner installation steps](BEGINNER_GUIDE.md#7-install-the-smartthings-edge-driver)
on a test hub. Accepting the invitation, enrolling the hub, and installing the
driver are three distinct steps. A separate user's hub is the best final check
that custom capabilities and presentations render outside the maintainer account.

Do not claim the channel is live until the API confirms the assignment and the
invitation works. The initial public channel and driver version are recorded in
[`smartthings-release.json`](../smartthings-release.json).

## 6. If publishing from a different SmartThings account

Advanced maintainers can create their own custom capability namespace and UI
presentations. Do not merely substitute the GitHub username for the namespace.

1. Find/create the namespace assigned to the target Samsung account using the
   CLI's custom capability tools.
2. Run `python edge_driver_template/prepare_driver.py YOUR_NAMESPACE`.
3. Register the seven generated custom schemas in `edge_driver_build/capabilities`:
   tiltPosition, tiltControl, rawTelemetry, sensorStatus, temperatures,
   lightLevel, and legacyLightLevel. Use `smartthings capabilities:create --help`
   for the installed CLI's registration options. Schema names determine the IDs;
   verify them against the generated package. Capability version is 1.
4. Register each matching generated capability presentation and English
   translation. The presentation filenames are tiltPosition, tiltControl,
   rawTelemetry, sensorStatus, temperatures, and lightLevel. The retained legacy
   light capability is for compatibility and has no new detail-view presentation.
   Example, substituting the correct full capability ID:

   ```text
   smartthings capabilities:presentation:create YOUR_NAMESPACE.mysmartblindsTiltPosition --input edge_driver_build/presentations/tiltPosition.json
   ```

5. Create both device presentations:

   ```text
   smartthings presentation:device-config:create --input edge_driver_build/device-configs/clean.json
   smartthings presentation:device-config:create --input edge_driver_build/device-configs/debug.json
   ```

6. Record each returned `vid` and `mnmn` in
   `edge_driver_template/device-presentations.json` under `clean` and `debug`,
   with `namespace` set to your actual namespace. Regenerate the package. Its
   active clean/debug profiles must contain those IDs.
7. Package, assign, invite, and test as above. Capability schema/presentation
   changes may be cached by hubs/apps; test visible UI and routines after changes.

The source generator does not register cloud resources for you. Publishing a
GitHub copy of a schema alone does not make that capability exist in SmartThings.

## 7. Maintain future releases

Record source tag, firmware identifier, driver version, channel assignment, test
results, and limitations in release notes. Test updates through a beta channel
before promoting to community. Keep a prior known-good package and version
available for rollback. Make no changes to the owner's installed devices merely
to publish source; private pilot migration is a separate task.
