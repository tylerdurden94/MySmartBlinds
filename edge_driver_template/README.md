# SmartThings Edge source

Novice users should use the community channel link in the root README.
Maintainers should read [PUBLISHING.md](../docs/PUBLISHING.md).

The ready package is `../edge_driver_build/package`. Its custom capabilities use
the maintainer's registered namespace `travelagain19498`; this identifier is
public and is unrelated to the GitHub account name. Its registered device
presentation IDs are in `device-presentations.json`.

Regenerate the package and UI definitions from the repository root:

```text
python edge_driver_template/prepare_driver.py travelagain19498
```

The package key is `tylerdurden94.msb.espbridge`. Each installation uses the
blind UUIDs imported into its own bridge. The driver runs on your SmartThings
hub and exposes those blinds to SmartThings; Alexa reaches them through the
separate SmartThings skill and account link.

Tests run the real Lua source against mocked SmartThings and LAN responses:

```text
python -m pip install -r requirements-dev.txt
python edge_driver_template/test_driver.py
```

These tests do not move real motors or validate a new user's hub installation.
