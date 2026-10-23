"""Import the private app backup through an ESP32's protected setup API.

This is a command-line fallback for the setup page's file picker. The complete
backup is read locally; only the ten blind records are sent to the bridge.
No key, address, or LAN token is printed.
"""

from __future__ import annotations

import argparse
import base64
import os
import re
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import ProxyHandler, Request, build_opener

from configure import BACKUP, TOKEN_FILE, inventory

OPENER = build_opener(ProxyHandler({}))


def request(base: str, path: str, authorization: str, fields: dict | None = None) -> str:
    data = urlencode(fields).encode("utf-8") if fields is not None else None
    headers = {"Authorization": authorization}
    if data is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    with OPENER.open(Request(base + path, data=data, headers=headers), timeout=10) as response:
        return response.read().decode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="ESP32 IP address or hostname")
    parser.add_argument("--backup", type=Path, default=BACKUP)
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    args = parser.parse_args()
    items = inventory(args.backup)
    token = args.token_file.read_text(encoding="utf-8").strip()
    if len(token) < 24:
        raise ValueError("The private LAN token is missing or invalid")
    authorization = "Basic " + base64.b64encode(f"msb:{token}".encode()).decode()
    base = args.host.rstrip("/")
    if not base.startswith(("http://", "https://")):
        base = f"http://{base}:8080"
    page = request(base, "/setup", authorization)
    match = re.search(r'name="nonce" value="([0-9a-f]+)"', page)
    if not match:
        raise RuntimeError("The bridge setup page did not provide a session nonce")
    nonce = match.group(1)
    request(base, "/setup/import/start", authorization, {"nonce": nonce, "count": len(items)})
    for item in items:
        request(base, "/setup/import/blind", authorization, {
            "nonce": nonce, "id": item["id"], "name": item["name"],
            "mac": item["mac"], "key": item["key"].hex().upper(),
        })
    request(base, "/setup/import/commit", authorization, {"nonce": nonce})
    print(f"Imported {len(items)} blind records. Waiting for the ESP32 to restart...")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        time.sleep(2)
        try:
            page = request(base, "/setup", authorization)
            if page.count('type="checkbox"') == len(items):
                print(f"Setup page shows {len(items)} known blinds.")
                return
        except OSError:
            pass
    raise TimeoutError("The bridge did not return with the expected inventory")


if __name__ == "__main__":
    main()
