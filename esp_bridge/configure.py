"""Provision one ESP32 with the private ten-blind inventory over USB serial.

Every known blind's key is stored on the ESP32 so assignments can later be
changed in its protected local web page. Only assigned blinds are connected.
The backup and decoded keys are never copied into this project or printed.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import secrets
import sys
import time
from pathlib import Path

import serial  # noqa: E402

BACKUP = Path.home() / "Downloads" / "MSB_BACKUP.json"
TOKEN_FILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config"))) / "MySmartBlinds" / "bridge_token.txt"


def inventory(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "user" not in data:
        raise ValueError("This does not look like a MySmartBlinds app backup")
    items = []
    for room in data["user"].get("rooms", []):
        for blind in room.get("blinds", []):
            mac_bytes = base64.b64decode(blind["encodedMacAddress"], validate=True)
            key = base64.b64decode(blind["encodedPasskey"], validate=True)
            if len(mac_bytes) != 6 or len(key) != 6:
                raise ValueError("Unexpected motor address or key length")
            name = str(blind["name"])
            uid = str(blind["id"]).lower()
            if len(uid) != 36 or len(name.encode("utf-8")) > 48:
                raise ValueError("Unexpected blind ID or name length")
            items.append({
                "id": uid,
                "room": str(room.get("name", "")),
                "name": name,
                "mac": ":".join(f"{byte:02X}" for byte in reversed(mac_bytes)),
                "key": key,
            })
    if not 1 <= len(items) <= 10 or len({b["id"] for b in items}) != len(items):
        raise ValueError("Expected 1–10 blinds with unique IDs")
    return items


def resolve_assignments(items: list[dict], choices: list[str]) -> set[str]:
    selected = set()
    for choice in choices:
        matches = [
            item for index, item in enumerate(items, 1)
            if choice.casefold() in {
                str(index), item["id"], f'{item["room"]}/{item["name"]}'.casefold()
            }
        ]
        if len(matches) != 1:
            raise ValueError(f"Assignment {choice!r} did not match exactly one blind")
        selected.add(matches[0]["id"])
    return selected


def wait_for(connection: serial.Serial, expected: str, seconds: float = 15) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        reply = connection.readline().decode("ascii", errors="replace").strip()
        if reply == expected:
            return
        if reply == "CONFIG_ERROR":
            raise RuntimeError("ESP32 rejected a configuration record")
    raise TimeoutError(f"ESP32 did not reply {expected}")


def send(connection: serial.Serial, fields: list[str], expected: str) -> None:
    connection.write(("|".join(fields) + "\n").encode("ascii"))
    connection.flush()
    wait_for(connection, expected)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("port", nargs="?", default=None, help="ESP32 USB serial port")
    parser.add_argument("--backup", type=Path, default=BACKUP)
    parser.add_argument("--list", action="store_true", help="Show numbered blind names")
    parser.add_argument("--assign", nargs="+", action="append", metavar="NUMBER_OR_ROOM/NAME",
                        help="Blinds this ESP32 will control; repeatable")
    parser.add_argument("--scan-only", action="store_true",
                        help="Provision all known addresses but assign no blinds yet")
    parser.add_argument("--reuse-network", action="store_true",
                        help="Keep Wi-Fi credentials and LAN token already on this ESP32")
    parser.add_argument("--network-only", action="store_true",
                        help="Set up Wi-Fi and LAN token; import blinds later in the web page")
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE,
                        help="Private LAN token file outside the project")
    args = parser.parse_args()
    if not args.port and not args.list:
        parser.error("Specify your board USB port, for example COM6 or /dev/ttyUSB0")
    if args.list and args.network_only:
        parser.error("--list and --network-only cannot be combined")
    items = [] if args.network_only else inventory(args.backup)
    if args.list:
        for index, item in enumerate(items, 1):
            print(f'{index:2}. {item["room"]} / {item["name"]}')
        return
    if args.network_only:
        if args.assign or args.scan_only or args.reuse_network:
            parser.error("--network-only cannot be combined with assignment or reuse options")
    elif bool(args.assign) == bool(args.scan_only):
        parser.error("Choose --assign for active blinds or --scan-only for placement scans")
    choices = [choice for group in (args.assign or []) for choice in group]
    selected = resolve_assignments(items, choices)
    if not args.network_only:
        print("Assigned:", ", ".join(f'{b["room"]}/{b["name"]}' for b in items if b["id"] in selected)
              or "(none; placement scan only)")
    if not args.reuse_network:
        ssid = input("2.4 GHz Wi-Fi SSID: ").strip()
        password = getpass.getpass("Wi-Fi password (hidden): ")
        if not ssid or not password or len(ssid.encode()) > 32 or len(password.encode()) > 63:
            raise ValueError("Wi-Fi SSID or password is missing or too long")
    if args.reuse_network and not args.token_file.exists():
        raise ValueError("The existing LAN token file is required with --reuse-network")
    token = (args.token_file.read_text(encoding="utf-8").strip()
             if args.token_file.exists() else secrets.token_urlsafe(32))
    if not 24 <= len(token) <= 128 or "|" in token:
        raise ValueError("LAN token must be 24–128 characters without a pipe")

    print(f"Connecting to {args.port}; keep the ESP32 attached by USB...")
    with serial.Serial(args.port, 115200, timeout=1) as connection:
        wait_for(connection, "CONFIG_READY", 12)
        if args.network_only:
            send(connection, [
                "NETWORK",
                base64.b64encode(ssid.encode()).decode("ascii"),
                base64.b64encode(password.encode()).decode("ascii"), token
            ], "PROVISIONED")
        elif args.reuse_network:
            send(connection, ["BEGIN_KEEP", str(len(items))], "CONFIG_BEGIN_OK")
        else:
            send(connection, [
                "BEGIN",
                base64.b64encode(ssid.encode()).decode("ascii"),
                base64.b64encode(password.encode()).decode("ascii"),
                token, str(len(items))
            ], "CONFIG_BEGIN_OK")
        if not args.network_only:
            for item in items:
                assigned = item["id"] in selected
                send(connection, [
                    "BLIND", item["id"],
                    base64.b64encode(item["name"].encode("utf-8")).decode("ascii"),
                    item["mac"], item["key"].hex().upper(),
                    "1" if assigned else "0"
                ], "CONFIG_BLIND_OK")
            send(connection, ["COMMIT"], "PROVISIONED")
        if not args.reuse_network:
            args.token_file.parent.mkdir(parents=True, exist_ok=True)
            args.token_file.write_text(token + "\n", encoding="utf-8")
        print("ESP32 saved configuration. Keys and Wi-Fi password were not printed.")
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            reply = connection.readline().decode("ascii", errors="replace").strip()
            if reply.startswith("Bridge IP: "):
                print(reply)
                break
            if reply == "WIFI_CONNECTION_FAILED":
                print(reply)
                break
    print(f"LAN token file: {args.token_file}")


if __name__ == "__main__":
    main()
