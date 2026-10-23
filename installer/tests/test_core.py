"""Software tests use synthetic records and serial doubles, never physical ports."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import core


def record(index=1):
    return {"id": f"00000000-0000-4000-8000-{index:012d}", "name": "Test blind",
            "encodedMacAddress": base64.b64encode(bytes([index, 2, 3, 4, 5, 6])).decode(),
            "encodedPasskey": base64.b64encode(b"\x01\x02\x03\x04\x05\x06").decode()}


def bundle(folder):
    manifest = {"chip": "esp32", "flash_size": 4194304,
                "firmware_version": core.FIRMWARE_VERSION, "parts": []}
    for offset, name in core.PARTS:
        data = b"synthetic test image " + name.encode()
        (folder / name).write_bytes(data)
        manifest["parts"].append({"offset": offset, "file": name, "size": len(data),
                                   "sha256": hashlib.sha256(data).hexdigest()})
    (folder / "manifest.json").write_text(json.dumps(manifest))


class Clock:
    def __init__(self):
        self.tick = 0

    def __call__(self):
        self.tick += 0.2
        return self.tick


class SerialDouble:
    def __init__(self, final=b"Bridge IP: 192.0.2.10\n", reject=False, ready=True):
        self.replies = [b"boot log\n", b"CONFIG_READY\n"] if ready else []
        self.writes = []
        self.final = final
        self.reject = reject

    def readline(self):
        return self.replies.pop(0) if self.replies else b""

    def write(self, data):
        self.writes.append(data)
        if self.reject:
            self.replies.append(b"CONFIG_ERROR\n")
        elif data.startswith(b"BEGIN|"):
            self.replies.append(b"CONFIG_BEGIN_OK\n")
        elif data.startswith(b"BLIND|"):
            self.replies.append(b"CONFIG_BLIND_OK\n")
        elif data == b"COMMIT\n":
            self.replies += [b"PROVISIONED\n", self.final]
        return len(data)

    def flush(self):
        pass


class BackupTests(unittest.TestCase):
    def parse(self, records):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "backup.json"
            path.write_text(json.dumps({"user": {"rooms": [{"name": "Test room", "blinds": records}]}}))
            return core.load_backup(path)

    def test_valid_backup_and_address_byte_order(self):
        blind, = self.parse([record()])
        self.assertEqual(blind.mac, "06:05:04:03:02:01")
        self.assertEqual(blind.key, bytes([1, 2, 3, 4, 5, 6]))

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(core.InstallerError):
            self.parse([record(), record()])

    def test_duplicate_addresses_rejected(self):
        second = record(2)
        second["encodedMacAddress"] = record()["encodedMacAddress"]
        with self.assertRaises(core.InstallerError):
            self.parse([record(), second])

    def test_empty_and_over_limit_rejected(self):
        for records in ([], [record(i) for i in range(1, 12)]):
            with self.subTest(count=len(records)), self.assertRaises(core.InstallerError):
                self.parse(records)

    def test_invalid_uuid_key_name_base64_rejected(self):
        for field, value in [("id", "invalid"), ("encodedPasskey", "private-secret"),
                             ("name", "é" * 25), ("name", ""), ("encodedMacAddress", "AA==")]:
            item = record()
            item[field] = value
            with self.subTest(field=field), self.assertRaises(core.InstallerError) as result:
                self.parse([item])
            self.assertNotIn("private-secret", str(result.exception))

    def test_missing_and_wrong_structure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "missing.json"
            with self.assertRaises(core.InstallerError):
                core.load_backup(path)
            path.write_text('{"user": {"rooms": null}}')
            with self.assertRaises(core.InstallerError):
                core.load_backup(path)

    def test_unicode_wifi_limits_and_nul(self):
        core.validate_network("é" * 16, "a" * 63)
        for ssid, password in [("é" * 17, "password"), ("", "password"), ("test", ""),
                               ("test", "é" * 32), ("te\0st", "password"), ("test", "a\0b")]:
            with self.subTest(ssid=ssid), self.assertRaises(core.InstallerError):
                core.validate_network(ssid, password)


class BundleTests(unittest.TestCase):
    def test_intact_bundle(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            bundle(folder)
            self.assertEqual(len(core.validate_bundle(folder)), 4)

    def test_corruption_stops_before_any_tool_call(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            bundle(folder)
            (folder / "firmware.bin").write_bytes(b"corrupt")
            calls = []
            with self.assertRaises(core.InstallerError):
                core.flash("TEST", folder, lambda _: None, tool=lambda *args: calls.append(args))
            self.assertEqual(calls, [])

    def test_manifest_path_and_offsets_cannot_override_expected_parts(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            for key, value in [("file", "../outside.bin"), ("offset", 0x9000)]:
                bundle(folder)
                path = folder / "manifest.json"
                manifest = json.loads(path.read_text())
                manifest["parts"][0][key] = value
                path.write_text(json.dumps(manifest))
                with self.assertRaises(core.InstallerError):
                    core.validate_bundle(folder)

    def test_small_or_unknown_flash_never_written(self):
        for output in ("Detected flash size: 2MB", "unknown"):
            with tempfile.TemporaryDirectory() as temp:
                folder = Path(temp)
                bundle(folder)
                calls = []
                def tool(args, report):
                    calls.append(args)
                    return output
                with self.assertRaises(core.InstallerError):
                    core.flash("TEST", folder, lambda _: None, tool=tool)
                self.assertEqual(len(calls), 1)

    def test_install_uses_separate_images_and_no_erase_or_force(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            bundle(folder)
            calls = []
            def tool(args, report):
                calls.append(args)
                return "Detected flash size: 4MB"
            core.flash("TEST", folder, lambda _: None, tool=tool)
            args = calls[-1]
            self.assertIn("esp32", args)
            self.assertEqual([arg for arg in args if arg.startswith("0x")], ["0x1000", "0x8000", "0xe000", "0x10000"])
            self.assertNotIn("erase_flash", args)
            self.assertNotIn("--force", args)

    def test_update_only_writes_application_after_layout_match(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            bundle(folder)
            calls = []
            def tool(args, report):
                calls.append(args)
                if "read_flash" in args:
                    Path(args[-1]).write_bytes((folder / "partitions.bin").read_bytes())
                return "Detected flash size: 8MB"
            core.flash("TEST", folder, lambda _: None, update_only=True, tool=tool)
            args = calls[-1]
            self.assertEqual([arg for arg in args if arg.startswith("0x")], ["0x10000"])

    def test_layout_mismatch_never_written(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            bundle(folder)
            calls = []
            def tool(args, report):
                calls.append(args)
                if "read_flash" in args:
                    Path(args[-1]).write_bytes(b"different")
                return "Detected flash size: 4MB"
            with self.assertRaises(core.InstallerError):
                core.flash("TEST", folder, lambda _: None, update_only=True, tool=tool)
            self.assertFalse(any("write_flash" in args for args in calls))


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.blinds = [core.Blind(record()["id"], "Test room", "Test blind", "06:05:04:03:02:01", bytes(range(6))),
                       core.Blind(record(2)["id"], "Test room", "Other blind", "06:05:04:03:02:02", bytes(range(6)))]

    def run_setup(self, connection):
        logs = []
        address = core.configure(connection, "Private Wi-Fi", "private-password", "t" * 43,
                                 self.blinds, {self.blinds[0].id}, logs.append, clock=Clock())
        return address, logs

    def test_success_sends_all_blinds_with_only_selected_enabled(self):
        connection = SerialDouble()
        address, logs = self.run_setup(connection)
        self.assertEqual(address, "192.0.2.10")
        self.assertEqual(connection.writes[1].split(b"|")[-1], b"1\n")
        self.assertEqual(connection.writes[2].split(b"|")[-1], b"0\n")
        self.assertEqual(connection.writes[-1], b"COMMIT\n")
        self.assertNotIn("private-password", " ".join(logs))
        self.assertNotIn("Private Wi-Fi", " ".join(logs))
        self.assertNotIn("t" * 43, " ".join(logs))

    def test_rejection_does_not_commit(self):
        connection = SerialDouble(reject=True)
        with self.assertRaises(core.InstallerError):
            self.run_setup(connection)
        self.assertNotIn(b"COMMIT\n", connection.writes)

    def test_missing_handshake_does_not_write(self):
        connection = SerialDouble(ready=False)
        with self.assertRaises(core.InstallerError):
            self.run_setup(connection)
        self.assertEqual(connection.writes, [])

    def test_wifi_failure_is_explicit_error(self):
        with self.assertRaisesRegex(core.InstallerError, "could not join Wi-Fi"):
            self.run_setup(SerialDouble(final=b"WIFI_CONNECTION_FAILED\n"))

    def test_unconfirmed_or_invalid_ip_never_claims_connection(self):
        for final in (b"", b"Bridge IP: malicious.example\n", b"Bridge IP: 0.0.0.0\n"):
            with self.subTest(final=final):
                address, _ = self.run_setup(SerialDouble(final=final))
                self.assertIsNone(address)


if __name__ == "__main__":
    unittest.main()
