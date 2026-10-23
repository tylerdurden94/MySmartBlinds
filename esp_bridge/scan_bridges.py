"""Compare the known-blind BLE range and assignments of two ESP32 bridges."""

from __future__ import annotations

import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import ProxyHandler, Request, build_opener

TOKEN_FILE = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / ".config"))) / "MySmartBlinds" / "bridge_token.txt"
LOCAL_OPENER = build_opener(ProxyHandler({}))


def api(base: str, path: str, token: str | None = None, method: str = "GET") -> dict:
    headers = {"X-MSB-Token": token} if token else {}
    request = Request(base.rstrip("/") + path, headers=headers, method=method)
    with LOCAL_OPENER.open(request, timeout=8) as response:
        return json.load(response)


def inspect(base: str, token: str, do_scan: bool) -> tuple[dict, dict]:
    info = api(base, "/v1/info")
    before = api(base, "/v1/scan", token)
    if do_scan:
        api(base, "/v1/scan", token, "POST")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            time.sleep(3)
            current = api(base, "/v1/scan", token)
            if current["scanCount"] != before["scanCount"]:
                return info, current
        raise TimeoutError(f"No placement scan completed at {base}")
    return info, before


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("first", help="First bridge IP or hostname")
    parser.add_argument("second", help="Second bridge IP or hostname")
    parser.add_argument("--token-file", type=Path, default=TOKEN_FILE)
    parser.add_argument("--second-token-file", type=Path,
                        help="Private LAN token file for the second ESP32 when it uses a different token")
    parser.add_argument("--inventory-only", action="store_true",
                        help="Show the most recent scan without starting a new one")
    parser.add_argument("--require-complete", action="store_true",
                        help="Exit with an error unless every blind is assigned exactly once")
    args = parser.parse_args()
    token = args.token_file.read_text(encoding="utf-8").strip()
    if len(token) < 24:
        parser.error("The private LAN token file is missing or invalid")
    second_token = (args.second_token_file.read_text(encoding="utf-8").strip()
                    if args.second_token_file else token)
    if len(second_token) < 24:
        parser.error("The second private LAN token file is missing or invalid")

    def base(host: str) -> str:
        if host.startswith(("http://", "https://")):
            return host.rstrip("/")
        return f"http://{host}:8080"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(inspect, base(host), bridge_token, not args.inventory_only)
            for host, bridge_token in ((args.first, token), (args.second, second_token))
        ]
        readings = [future.result() for future in futures]
    if readings[0][0]["bridgeId"] == readings[1][0]["bridgeId"]:
        parser.error("Both addresses point to the same ESP32")

    indexed = []
    for info, scan in readings:
        indexed.append({entry["id"]: entry for entry in scan["blinds"]})
        print(f'{info["bridgeId"]}: {len(info["blinds"])} assigned, '
              f'scan age {scan["ageSeconds"]} seconds')
    all_ids = sorted(set(indexed[0]) | set(indexed[1]))
    problems = []
    print("\nBlind                         First RSSI   Second RSSI   Assigned")
    for uid in all_ids:
        a, b = indexed[0].get(uid), indexed[1].get(uid)
        name = (a or b)["name"]
        first = str(a["rssi"]) if a and a["rssi"] is not None else "not seen"
        second = str(b["rssi"]) if b and b["rssi"] is not None else "not seen"
        owners = [i + 1 for i, entry in enumerate((a, b)) if entry and entry["assigned"]]
        assignment = ",".join(map(str, owners)) if owners else "none"
        if len(owners) != 1:
            problems.append(f"{name}: assigned to {assignment}")
        if first == second == "not seen":
            problems.append(f"{name}: not seen by either ESP32")
        print(f"{name[:29]:29} {first:12} {second:13} {assignment}")
    if problems:
        print("\nCheck:")
        for problem in problems:
            print(f"- {problem}")
    return 2 if args.require_complete and problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
