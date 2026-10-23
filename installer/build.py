"""Build on the target OS using Python 3.12. SPDX-License-Identifier: GPL-2.0-or-later"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
from core import FIRMWARE_VERSION, PARTS, VERSION, validate_bundle


def prepare(firmware_dir, framework_dir):
    target = HERE / "firmware"
    target.mkdir(exist_ok=True)
    version = re.search(r'kFirmwareVersion\[\] = "([^"]+)"',
                        (ROOT / "esp_bridge/src/main.cpp").read_text(encoding="utf-8"))[1]
    if version != FIRMWARE_VERSION:
        raise RuntimeError("Update the installer's expected firmware version before building.")
    manifest = {"chip": "esp32", "flash_size": 4194304, "firmware_version": version, "parts": []}
    for offset, name in PARTS:
        source = (framework_dir / "tools/partitions/boot_app0.bin" if name == "boot_app0.bin"
                  else firmware_dir / name)
        shutil.copyfile(source, target / name)
        data = source.read_bytes()
        manifest["parts"].append({"offset": offset, "file": name, "size": len(data),
                                   "sha256": hashlib.sha256(data).hexdigest()})
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    # Verify the pinned NVS and app layout directly from the partition table.
    import struct
    table = (target / "partitions.bin").read_bytes()
    entries = {}
    for start in range(0, len(table) - 31, 32):
        magic, kind, subtype, offset, size, label, flags = struct.unpack("<HBBII16sI", table[start:start+32])
        if magic == 0x50AA:
            entries[label.split(b"\0")[0].decode()] = (offset, size)
    if entries.get("nvs") != (0x9000, 0x5000) or entries.get("app0") != (0x10000, 0x140000):
        raise RuntimeError("Unexpected partition layout; do not distribute this installer.")
    validate_bundle(target)
    licenses = HERE / "licenses"
    licenses.mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "LICENSE", licenses / "MySmartBlinds-MIT.txt")
    shutil.copyfile(ROOT / "THIRD_PARTY_NOTICES.md", licenses / "Firmware-notices.md")
    shutil.copyfile(HERE / "LICENSE.txt", licenses / "Installer-GPL-2.0.txt")
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata.get("Name", "")
        # Only installed build/runtime dependencies; never copy the environment.
        if name.lower().replace("_", "-") not in {
            "esptool", "pyserial", "pyinstaller", "altgraph", "bitstring", "bitarray", "tibs",
            "cryptography", "ecdsa", "reedsolo", "pyyaml", "intelhex", "cffi", "pycparser", "six",
            "setuptools", "packaging", "pefile", "pywin32-ctypes", "pyinstaller-hooks-contrib"}:
            continue
        for file in distribution.files or []:
            if "license" in file.name.lower() or "copying" in file.name.lower():
                source = Path(distribution.locate_file(file))
                if source.is_file():
                    shutil.copyfile(source, licenses / f"{name}-{source.name}")
    for directory, prefix in [(framework_dir, "Arduino"),
                               (ROOT / "esp_bridge/.pio/libdeps/esp32-wroom-32d/NimBLE-Arduino", "NimBLE")]:
        if directory.exists():
            for file in directory.iterdir():
                if file.is_file() and ("license" in file.name.lower() or "copying" in file.name.lower()):
                    shutil.copyfile(file, licenses / f"{prefix}-{file.name}")
    return target, licenses


def source_archive(destination):
    # Explicit allowlist: never zip the parent workspace, user backups or build caches.
    if not (HERE / "third-party-source/esptool-4.11.0.tar.gz").exists():
        raise RuntimeError("Download the corresponding esptool source before packaging.")
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in ["LICENSE", "THIRD_PARTY_NOTICES.md", "requirements-tools.txt",
                     "docs/INSTALLER_GUIDE.md", ".github/workflows/build-installers.yml"]:
            archive.write(ROOT / name, name)
        for folder in [ROOT / "installer", ROOT / "esp_bridge"]:
            for file in sorted(folder.rglob("*")):
                relative = file.relative_to(ROOT)
                if (file.is_file() and not {".pio", "__pycache__", "build", "dist", "firmware", "licenses"}.intersection(relative.parts)
                        and file.suffix not in {".pyc", ".spec"}):
                    archive.write(file, relative.as_posix())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-dir", type=Path, default=ROOT / "esp_bridge/.pio/build/esp32-wroom-32d")
    parser.add_argument("--framework-dir", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    args = parser.parse_args()
    firmware, licenses = prepare(args.firmware_dir.resolve(), args.framework_dir.resolve())
    if args.prepare_only:
        print("Firmware bundle and licenses prepared and verified.")
        return
    destination = HERE / "dist"
    destination.mkdir(exist_ok=True)
    name = "MySmartBlinds-Setup"
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--noupx",
               "--name", name, "--distpath", str(destination), "--workpath", str(HERE / "build"),
               "--specpath", str(HERE / "build"), "--paths", str(HERE),
               "--collect-all", "esptool", "--collect-submodules", "serial.tools",
               "--add-data", f"{firmware}:firmware", "--add-data", f"{licenses}:licenses"]
    if sys.platform == "win32":
        command.append("--onefile")
    else:
        command += ["--onedir", "--osx-bundle-identifier", "com.tylerdurden94.mysmartblinds.setup"]
    command.append(str(HERE / "app.py"))
    subprocess.run(command, check=True)
    executable = destination / (name + ".exe" if sys.platform == "win32" else name + ".app/Contents/MacOS/" + name)
    report = destination / "self-test.json"
    # A windowed Windows executable needs an explicit wait, unlike a shell launch.
    subprocess.run([str(executable), "--self-test", str(report)], check=True, timeout=90)
    if json.loads(report.read_text())["status"] != "passed":
        raise RuntimeError("Packaged application self-test failed.")
    arch = platform.machine().lower()
    label = "Windows-x64" if sys.platform == "win32" else "macOS-" + ("AppleSilicon" if arch == "arm64" else "Intel")
    package = destination / f"MySmartBlinds-Setup-{VERSION}-{label}.zip"
    sources = destination / f"MySmartBlinds-Setup-{VERSION}-Source.zip"
    source_archive(sources)
    if sys.platform == "darwin":
        # ditto preserves executable permissions, symlinks and the app bundle.
        subprocess.run(["ditto", "-c", "-k", "--sequesterRsrc", "--keepParent",
                        str(destination / (name + ".app")), str(package)], check=True)
        with zipfile.ZipFile(package, "a", zipfile.ZIP_DEFLATED) as archive:
            archive.write(ROOT / "docs/INSTALLER_GUIDE.md", "READ-ME-FIRST.md")
            archive.write(report, "self-test.json")
            archive.write(sources, sources.name)
            for file in licenses.iterdir():
                archive.write(file, "Licenses/" + file.name)
    else:
        with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(executable, executable.name)
            archive.write(ROOT / "docs/INSTALLER_GUIDE.md", "READ-ME-FIRST.md")
            archive.write(report, "self-test.json")
            archive.write(sources, sources.name)
            for file in licenses.iterdir():
                archive.write(file, "Licenses/" + file.name)
    digest = hashlib.sha256(package.read_bytes()).hexdigest()
    (destination / (package.name + ".sha256")).write_text(digest + "  " + package.name + "\n", encoding="ascii")
    print(f"Built and self-tested: {package.name}\nSHA256: {digest}")


if __name__ == "__main__":
    main()
