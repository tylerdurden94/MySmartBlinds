# SmartThings Edge source

Novice users should use the community channel link in the root README once
published. Maintainers should read [PUBLISHING.md](../docs/PUBLISHING.md).

The ready package is `../edge_driver_build/package`. Its custom capabilities use
the maintainer's registered namespace `travelagain19498`; this identifier is
public and is unrelated to the GitHub account name. Its registered device
presentation IDs are in `device-presentations.json`.

Regenerate the package and UI definitions from the repository root:

```text
python edge_driver_template/prepare_driver.py travelagain19498
```

The public package key is `tylerdurden94.msb.espbridge`, separate from
the private development driver. The legacy pilot's personal blind UUID has been
replaced with a synthetic value in the public source. New installations use the
UUIDs imported by their own bridge. Do not use this snapshot as an automatic
migration of the maintainer's private pilot devices.

Tests run the real Lua source against mocked SmartThings and LAN responses:

```text
python -m pip install -r requirements-dev.txt
python edge_driver_template/test_driver.py
```

These tests do not move real motors or validate a new user's hub installation.
