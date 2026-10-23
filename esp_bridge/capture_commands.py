"""Record read-only LAN command diagnostics without moving any blinds."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen


COMMAND_FIELDS = (
    "queuedPosition", "lastCommandId", "lastCommandPosition",
    "lastCommandWriteOk", "lastCommandAttempts", "lastCommandLatencyMs",
    "position", "bleStage",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host")
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--interval", type=float, default=1)
    parser.add_argument("--name", action="append", default=[])
    parser.add_argument("--token-file", type=Path, default=Path(
        os.environ.get("LOCALAPPDATA", str(Path.home() / ".config"))) /
        "MySmartBlinds" / "bridge_token.txt")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.seconds <= 0 or args.interval <= 0:
        parser.error("seconds and interval must be positive")
    token = args.token_file.read_text(encoding="utf-8").strip()
    base = "http://" + args.host

    def get(path, authenticate=True):
        headers = {"X-MSB-Token": token} if authenticate else {}
        with urlopen(Request(base + path, headers=headers), timeout=5) as response:
            return json.load(response)

    info = get("/v1/info", authenticate=False)
    blinds = [blind for blind in info["blinds"]
              if not args.name or blind["name"] in args.name]
    if not blinds:
        parser.error("no matching assigned blinds")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    previous = {}
    deadline = time.monotonic() + args.seconds
    with args.output.open("w", encoding="utf-8") as output:
        def record(payload):
            output.write(json.dumps({
                "timeUtc": datetime.now(timezone.utc).isoformat(), **payload,
            }) + "\n")
            output.flush()

        record({"info": info})
        print("Recording " + ", ".join(blind["name"] for blind in blinds), flush=True)
        while time.monotonic() < deadline:
            started = time.monotonic()
            for blind in blinds:
                try:
                    state = get("/v1/blinds/" + blind["id"])
                except Exception as error:
                    # Never print request headers or a token-bearing request object.
                    record({"name": blind["name"], "error": type(error).__name__})
                    continue
                record({"name": blind["name"], "state": state})
                summary = {field: state.get(field) for field in COMMAND_FIELDS}
                if previous.get(blind["id"]) != summary:
                    print(blind["name"] + ": " + json.dumps(summary), flush=True)
                    previous[blind["id"]] = summary
            time.sleep(max(0, min(args.interval - (time.monotonic() - started),
                                  deadline - time.monotonic())))
    print("Saved " + str(args.output.resolve()), flush=True)


if __name__ == "__main__":
    main()
