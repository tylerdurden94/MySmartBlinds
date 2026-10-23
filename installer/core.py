"""USB installer engine. No network uploads; no configured flash images.

SPDX-License-Identifier: GPL-2.0-or-later
"""
from __future__ import annotations

import base64
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
import hashlib
import io
import ipaddress
import json
from pathlib import Path
import re
import time
import uuid

VERSION = "0.2.0-beta.1"
FIRMWARE_VERSION = "multi-bridge-2026-10-17-31-bounded-startup"
PARTS = ((0x1000, "bootloader.bin"), (0x8000, "partitions.bin"),
         (0xE000, "boot_app0.bin"), (0x10000, "firmware.bin"))


class InstallerError(Exception):
    """An error suitable for displaying without exposing credentials."""


@dataclass(frozen=True)
class Blind:
    id: str
    room: str
    name: str
    mac: str
    key: bytes


def load_backup(path: Path) -> list[Blind]:
    try:
        if path.stat().st_size > 10 * 1024 * 1024:
            raise ValueError()
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        rooms = data["user"]["rooms"]
        if not isinstance(rooms, list):
            raise ValueError()
        result = []
        for room in rooms:
            for item in room.get("blinds", []):
                ident = str(uuid.UUID(item["id"]))
                name = item["name"]
                if not isinstance(name, str) or not name.strip() or len(name.encode()) > 48:
                    raise ValueError()
                mac = base64.b64decode(item["encodedMacAddress"], validate=True)
                key = base64.b64decode(item["encodedPasskey"], validate=True)
                if len(mac) != 6 or len(key) != 6:
                    raise ValueError()
                result.append(Blind(ident, str(room.get("name", "")), name,
                                    ":".join(f"{b:02X}" for b in reversed(mac)), key))
        if not 1 <= len(result) <= 10:
            raise ValueError()
        if len({b.id for b in result}) != len(result) or len({b.mac for b in result}) != len(result):
            raise ValueError()
        return result
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        raise InstallerError("Select a compatible Android MySmartBlinds JSON backup containing "
                             "1–10 blinds with unique IDs and addresses. The file was not imported.") from None


def validate_network(ssid: str, password: str) -> None:
    # The existing firmware requires a nonempty password and uses C strings.
    if not ssid or len(ssid.encode()) > 32 or "\0" in ssid:
        raise InstallerError("Enter a Wi-Fi name between 1 and 32 bytes long.")
    if not password or len(password.encode()) > 63 or "\0" in password:
        raise InstallerError("Enter a Wi-Fi password between 1 and 63 bytes long. "
                             "Open networks are not supported by this firmware.")


def validate_bundle(folder: Path) -> list[tuple[int, Path]]:
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        if (manifest["chip"] != "esp32" or manifest["flash_size"] != 4194304
                or manifest["firmware_version"] != FIRMWARE_VERSION
                or len(manifest["parts"]) != len(PARTS)):
            raise ValueError()
        result = []
        for entry, (offset, name) in zip(manifest["parts"], PARTS):
            if entry["offset"] != offset or entry["file"] != name:
                raise ValueError()
            path = folder / name
            content = path.read_bytes()
            if (not content or len(content) != entry["size"]
                    or hashlib.sha256(content).hexdigest() != entry["sha256"]):
                raise ValueError()
            # Never touch NVS at 0x9000..0xDFFF; use separate, bounded images.
            limit = {0x1000: 0x7000, 0x8000: 0x1000, 0xE000: 0x2000, 0x10000: 0x140000}[offset]
            if len(content) > limit:
                raise ValueError()
            result.append((offset, path))
        return result
    except (OSError, ValueError, KeyError, TypeError):
        raise InstallerError("The bundled firmware is missing or damaged. Download a fresh installer.") from None


class ToolOutput(io.StringIO):
    def __init__(self, report):
        super().__init__()
        self.report = report
        self.pending = ""

    def write(self, text):
        count = super().write(text)
        self.pending += text.replace("\r", "\n")
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            match = re.search(r"\((\d+) %\)", line)
            if match:
                self.report("Flashing: " + match.group(1) + "% of current image")
        return count


def run_esptool(arguments: list[str], report) -> str:
    import esptool
    output = ToolOutput(report)
    try:
        with redirect_stdout(output), redirect_stderr(output):
            esptool.main(arguments)
    except (Exception, SystemExit):
        # Do not expose raw device output or identifiers in shareable diagnostics.
        raise InstallerError("Could not communicate with the ESP32. Close other serial apps, "
                             "check the USB data cable and driver, then retry. If needed, hold "
                             "BOOT while connecting and release it when flashing starts. "
                             "Only classic ESP32 boards with at least 4 MB flash are supported.") from None
    return output.getvalue()


def flash(port: str, folder: Path, report, *, update_only=False, tool=None) -> None:
    parts = validate_bundle(folder)
    tool = tool or run_esptool
    report("Checking the ESP32 and flash memory…")
    base = ["--chip", "esp32", "--port", port, "--baud", "115200", "--before", "default_reset"]
    info = tool(base + ["--after", "no_reset", "flash_id"], report)
    match = re.search(r"Detected flash size:\s*(\d+)\s*MB", info, re.I)
    if not match or int(match.group(1)) < 4:
        raise InstallerError("The board did not report at least 4 MB of flash. Nothing was written.")
    if update_only:
        # Inspect the partition table before an app-only update. Never update an
        # unknown layout at a guessed address or overwrite its partition table.
        import tempfile
        with tempfile.TemporaryDirectory() as scratch:
            table = Path(scratch) / "partitions.bin"
            size = hex((folder / "partitions.bin").stat().st_size)
            tool(base + ["--after", "no_reset", "read_flash", "0x8000", size, str(table)], report)
            if table.read_bytes() != (folder / "partitions.bin").read_bytes():
                raise InstallerError("This board has a different partition layout. Update was stopped. "
                                     "Use a spare supported board for a new installation.")
        parts = parts[-1:]
    report("Writing firmware; keep the USB cable connected…")
    args = base + ["--after", "hard_reset", "write_flash", "--flash_mode", "dio",
                   "--flash_freq", "40m", "--flash_size", "4MB"]
    for offset, path in parts:
        args += [hex(offset), str(path)]
    tool(args, report)
    # esptool write_flash verifies each image's hash before returning success.
    report("Firmware written and verified.")


def wait_reply(connection, expected, timeout=15, clock=time.monotonic):
    deadline = clock() + timeout
    while clock() < deadline:
        reply = connection.readline().decode("utf-8", errors="replace").strip()
        if reply == expected:
            return
        if reply == "CONFIG_ERROR":
            raise InstallerError("The ESP32 rejected the settings. Check the backup and try again.")
    raise InstallerError("The ESP32 did not enter USB setup. Press EN/RESET and retry setup, "
                         "or install this wizard's firmware first. Release BOOT before setup.")


def configure(connection, ssid: str, password: str, token: str, blinds: list[Blind],
              selected: set[str], report, *, clock=time.monotonic) -> str | None:
    validate_network(ssid, password)
    if (not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", token) or not 1 <= len(blinds) <= 10
            or not selected <= {b.id for b in blinds}):
        raise InstallerError("Invalid setup selection. Choose the backup again and retry.")
    wait_reply(connection, "CONFIG_READY", 15, clock)

    def send(fields, expected):
        connection.write(("|".join(fields) + "\n").encode("ascii"))
        connection.flush()
        wait_reply(connection, expected, clock=clock)

    def encoded(value):
        return base64.b64encode(value.encode()).decode("ascii")

    report("Sending Wi-Fi and blind settings over USB…")
    send(["BEGIN", encoded(ssid), encoded(password), token, str(len(blinds))], "CONFIG_BEGIN_OK")
    for blind in blinds:
        send(["BLIND", blind.id, encoded(blind.name), blind.mac, blind.key.hex().upper(),
              "1" if blind.id in selected else "0"], "CONFIG_BLIND_OK")
    send(["COMMIT"], "PROVISIONED")
    report("Settings saved. Waiting for the home Wi-Fi connection…")
    deadline = clock() + 40
    while clock() < deadline:
        reply = connection.readline().decode("utf-8", errors="replace").strip()
        if reply == "WIFI_CONNECTION_FAILED":
            raise InstallerError("Settings were saved, but the ESP32 could not join Wi-Fi. "
                                 "Check the 2.4 GHz network and password, then use Set up existing bridge.")
        if reply.startswith("Bridge IP: "):
            try:
                address = str(ipaddress.IPv4Address(reply.removeprefix("Bridge IP: ")))
                if address == "0.0.0.0":
                    raise ValueError()
                return address
            except ValueError:
                continue
    return None


def open_for_setup(port: str):
    import serial
    connection = serial.Serial(port=None, baudrate=115200, timeout=0.5, write_timeout=5)
    connection.port = port
    connection.dtr = False  # GPIO0 must stay high for normal boot.
    connection.rts = False
    try:
        connection.open()
        connection.rts = True  # Pulse EN, then listen from the beginning of boot.
        time.sleep(0.15)
        connection.reset_input_buffer()
        connection.rts = False
        return connection
    except Exception:
        connection.close()
        raise InstallerError("Could not open the USB port. Close other serial apps, reconnect "
                             "the board, refresh the port list, and try again.") from None
