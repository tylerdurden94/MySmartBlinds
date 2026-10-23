"""Compile real firmware BLE/scheduling functions against a host radio double.

Requires a C++17 compiler, or --zig pointing to an installed Zig executable.
No devices are accessed. Each selected production function is copied verbatim.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def extract(source, pattern):
    match = re.search(pattern, source, re.M | re.S)
    if not match:
        raise ValueError("Production definition not found: " + pattern)
    return match[0] + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, default=HERE)
    parser.add_argument("--zig", type=Path)
    parser.add_argument("--cxx", default="g++")
    args = parser.parse_args()
    source = (args.project / "src/main.cpp").read_text()
    generated = '#include "bridge_storage.h"\n'
    generated += extract(source, r'^constexpr char kService\[\].*?(?=^struct MotorState)')
    for name in ["MotorState", "PositionCommand", "MotorConnection"]:
        generated += extract(source, rf'^struct {name} \{{.*?^\}};')
    generated += '''
BridgeConfig config;
MotorState states[kMaxBlinds];
MotorConnection motorConnections[3];
uint8_t pollMinutes[kMaxBlinds] = {};
PositionCommand positionCommands[kMaxPositionCommands];
size_t positionCommandCount = 0;
int activeBlind = -1;
bool scanRequested = false;
void scanKnownMotors() {}
'''
    for name in ["le16", "ingestForBlind", "ingest", "onIndication"]:
        generated += extract(source, rf'^\w+ {name}\(.*?^\}}')
    generated += extract(source, r'^class MotorClientCallbacks.*?^MotorClientCallbacks motorClientCallbacks;')
    for name in ["subscribeAndRead", "updateQueuedPositionLocked", "removePositionCommandLocked",
                 "bleStageName", "markBleStage", "releaseMotorConnection",
                 "releaseIdleMotorConnections", "connectionForMotor", "pollMotor", "bleWorker"]:
        generated += extract(source, rf'^(?:const char\*|\w+\*?) {name}\(.*?^\}}')
    with tempfile.TemporaryDirectory(prefix="msb-lifecycle-") as directory:
        tmp = Path(directory)
        (tmp / "firmware_under_test.inc").write_text(generated)
        executable = tmp / "test_lifecycle.exe"
        compiler = [str(args.zig), "c++"] if args.zig else [args.cxx]
        flags = ["-Wno-nullability-completeness"] if args.zig else []
        result = subprocess.run(compiler + flags + ["-std=c++17", "-Wall", "-Wextra", "-Werror",
            "-I" + str(tmp), "-I" + str(args.project.resolve() / "include"),
            str(HERE / "tests/test_connection_lifecycle.cpp"), "-o", str(executable)],
            capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError((result.stdout + result.stderr)[-8000:])
        subprocess.run([str(executable)], check=True)


if __name__ == "__main__":
    main()
