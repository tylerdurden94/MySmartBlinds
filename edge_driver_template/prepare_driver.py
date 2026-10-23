"""Build an Edge package and custom capability presentations for your namespace."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "edge_driver_build"


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def build_percent_ui(namespace: str) -> None:
    sensor_id = f"{namespace}.mysmartblindsSensorStatus"
    raw_id = f"{namespace}.mysmartblindsRawTelemetry"
    native_id = f"{namespace}.mysmartblindsTiltPosition"
    control_id = f"{namespace}.mysmartblindsTiltControl"
    temperatures_id = f"{namespace}.mysmartblindsTemperatures"
    light_id = f"{namespace}.mysmartblindsLightMeter"
    legacy_light_id = f"{namespace}.mysmartblindsLightLevel"
    enum_values = {
        "chargingSource": ["none", "solar", "usb", "both"],
        "temperatureReady": ["unverified", "ready"],
        "lightReady": ["unverified", "ready"],
    }
    write_json(OUT / "capabilities" / "sensorStatus.json", {
        "name": "MySmartBlinds Sensor Status",
        "attributes": {name: {"schema": {
            "type": "object", "properties": {"value": {"type": "string", "enum": values}},
            "required": ["value"], "additionalProperties": False,
        }} for name, values in enum_values.items()}, "commands": {},
    })
    charging_options = [{"key": key, "value": label} for key, label in [
        ("none", "Not charging"), ("solar", "Solar charging"),
        ("usb", "USB charging"), ("both", "Solar and USB charging")]]
    write_json(OUT / "presentations" / "sensorStatus.json", {
        "id": sensor_id, "version": 1,
        "dashboard": {"states": [], "actions": []},
        "detailView": [{"label": "Charging", "displayType": "state",
            "state": {"label": "{{chargingSource.value}}", "alternatives": charging_options}}],
        "automation": {"conditions": [{"label": "Charging source", "displayType": "list",
            "list": {"value": "chargingSource.value", "alternatives": charging_options}}], "actions": []},
    })
    write_json(OUT / "translations" / "sensorStatus.en.json", {
        "tag": "en", "label": "Charging", "attributes": {
            name: {"label": label, "displayTemplate": label + " of {{device.label}} is {{value}}",
                   "i18n": {"value": {value: value for value in enum_values[name]}}}
            for name, label in [("chargingSource", "Charging source"),
                                ("temperatureReady", "Temperature calibration"),
                                ("lightReady", "Light calibration")]
        },
    })

    def attribute(value_schema: dict, unit: str | None = None) -> dict:
        properties = {"value": value_schema}
        if unit:
            properties["unit"] = {"type": "string", "enum": [unit], "default": unit}
        return {"schema": {"type": "object", "properties": properties,
                           "required": ["value"], "additionalProperties": False}}

    write_json(OUT / "capabilities" / "temperatures.json", {
        "name": "MySmartBlinds Temperatures", "commands": {}, "attributes": {
            "ambientDisplay": attribute({"type": "string"}),
            "batteryDisplay": attribute({"type": "string"}),
            "ambientTemperature": attribute({"type": "number", "minimum": -100, "maximum": 150}, "C"),
            "batteryTemperature": attribute({"type": "number", "minimum": -100, "maximum": 150}, "C"),
        },
    })
    write_json(OUT / "presentations" / "temperatures.json", {
        "id": temperatures_id, "version": 1,
        "dashboard": {"states": [], "actions": []},
        "detailView": [
            {"label": label, "displayType": "state", "state": {"label": "{{" + name + ".value}}"}}
            for name, label in [("ambientDisplay", "Ambient temperature (inside motor)"),
                                ("batteryDisplay", "Battery temperature")]],
        "automation": {"conditions": [
            {"label": label, "displayType": "numberField", "numberField": {
                "value": name + ".value", "valueType": "number", "unit": name + ".unit"}}
            for name, label in [("ambientTemperature", "Ambient temperature (inside motor)"),
                                ("batteryTemperature", "Battery temperature")]], "actions": []},
    })
    write_json(OUT / "capabilities" / "lightLevel.json", {
        "name": "MySmartBlinds Light Meter", "commands": {}, "attributes": {
            "lightLevel": attribute({"type": "integer", "minimum": 0, "maximum": 32767}, "sensor units"),
            "lightRange": attribute({"type": "object", "additionalProperties": False,
                "properties": {
                    "minimum": {"type": "integer", "minimum": 0, "maximum": 32767},
                    "maximum": {"type": "integer", "minimum": 1, "maximum": 32767},
                    "step": {"type": "integer", "minimum": 1, "maximum": 32767}},
                "required": ["minimum", "maximum"]}, "sensor units"),
        },
    })
    # Keep the previous capability usable by existing light-based routines.
    write_json(OUT / "capabilities" / "legacyLightLevel.json", {
        "name": "MySmartBlinds Light Level", "commands": {}, "attributes": {
            "lightLevel": attribute({"type": "integer", "minimum": 0, "maximum": 32767}, "sensor units"),
            "lightRange": attribute({"type": "array", "minItems": 2, "maxItems": 2,
                "items": {"type": "number", "minimum": 0, "maximum": 32767}}),
        },
    })
    write_json(OUT / "presentations" / "lightLevel.json", {
        "id": light_id, "version": 1,
        "dashboard": {"states": [], "actions": []},
        "detailView": [{"label": "Relative light level", "displayType": "slider", "slider": {
            "value": "lightLevel.value", "valueType": "integer", "unit": "lightLevel.unit",
            "range": [0, 32767], "supportedValues": "lightRange.value"}}],
        "automation": {"conditions": [{"label": "Relative light level", "displayType": "numberField",
            "numberField": {"value": "lightLevel.value", "valueType": "integer", "unit": "lightLevel.unit",
                            "range": [0, 32767]}}], "actions": []},
    })
    for name, label, attributes in [
        ("temperatures", "Temperatures", {"ambientDisplay": "Ambient temperature (inside motor)",
          "batteryDisplay": "Battery temperature", "ambientTemperature": "Ambient temperature (inside motor)",
          "batteryTemperature": "Battery temperature"}),
        ("lightLevel", "Light level", {"lightLevel": "Relative light level", "lightRange": "Light display range"}),
    ]:
        write_json(OUT / "translations" / f"{name}.en.json", {
            "tag": "en", "label": label, "attributes": {
                attr: {"label": title, "displayTemplate": title + " of {{device.label}} is {{value}}", "i18n": {}}
                for attr, title in attributes.items()},
        })

    def entry(capability: str, component: str = "main", **extra: object) -> dict:
        return {"component": component, "capability": capability, "version": 1, **extra}

    def ready(attribute: str) -> dict:
        return {"component": "main", "capability": sensor_id, "version": 1,
                "value": attribute + ".value", "operator": "EQUALS", "operand": "ready",
                "hideOnUnmatch": True}

    levels = {"values": [{"key": "shadeLevel.value", "range": [0, 100], "step": 1}],
              "patch": [{"op": "replace", "path": "/0/label", "value": "Tilt (50% = flat)"}]}
    shades = {"values": [{"key": "{{enumCommands}}", "enabledValues": ["open", "close"]}],
              "patch": [{"op": "replace", "path": "/0/label", "value": "Blind position"}]}
    light_scale = {"values": [{"key": "lightLevel.value", "range": [0, 32767], "step": 1}]}
    details = [entry("windowShadeLevel", **levels), entry("windowShade", **shades),
               entry("battery"), entry(sensor_id), entry(light_id, **light_scale), entry(temperatures_id),
               entry("illuminanceMeasurement", visibleCondition=ready("lightReady")), entry("refresh")]
    conditions = [entry("windowShadeLevel"), entry("windowShade"), entry("battery"),
                  entry(sensor_id), entry("temperatureMeasurement"),
                  entry(temperatures_id), entry(light_id), entry(legacy_light_id), entry("illuminanceMeasurement"),
                  entry(raw_id), entry(native_id)]
    actions = [entry("windowShadeLevel"), entry("windowShade", **shades), entry("refresh")]
    for debug in (False, True):
        name = "debug" if debug else "clean"
        write_json(OUT / "device-configs" / f"{name}.json", {
            "type": "profile",
            "dashboard": {"states": [entry("windowShade")], "actions": [entry("windowShade", **shades)], "basicPlus": []},
            "detailView": details + ([entry(raw_id), entry(native_id)] if debug else []),
            "automation": {"conditions": conditions, "actions": actions},
        })

    def preference(name: str, title: str, kind: str, definition: dict, description: str) -> dict:
        return {"name": name, "title": title, "description": description,
                "required": False, "preferenceType": kind, "definition": definition}

    preferences = [
        preference("bridgeToken", "Bridge token", "string", {"stringType": "password", "minLength": 0,
            "maxLength": 128, "default": ""}, "Token stored under AppData Local MySmartBlinds"),
        preference("onPosition", "Open position (%)", "integer", {"minimum": 0, "maximum": 100, "default": 50},
            "Open goes to this tilt. 50% is flat/open; 0% and 100% tilt closed in opposite directions."),
        preference("offPosition", "Close position (%)", "integer", {"minimum": 0, "maximum": 100, "default": 0},
            "Close goes to this tilt. Choose a different position from Open; 0% and 100% are opposite closed tilts."),
        preference("openCloseRetries", "Open/Close retries", "integer", {"minimum": 0, "maximum": 15, "default": 0},
            "Extra Open/Close attempts (0 = off). Uses all attempts even without fresh position feedback. Stops when confirmed; accepts 3% from closed endpoints. No periodic checks."),
        preference("positionPollMinutes", "Position check interval", "enumeration", {
            "options": {"0": "Off", "1": "1 minute", "2": "2 minutes", "5": "5 minutes",
                        "10": "10 minutes", "15": "15 minutes", "30": "30 minutes"},
            "default": "2"},
            "How often this blind wakes for a position read. Off keeps the last known position until a command or manual Refresh."),
        preference("showDiagnostics", "Show raw diagnostics", "boolean", {"default": False},
            "Show native tilt and raw sensor readings in the device panel. Reopen the panel after changing."),
        preference("temperatureScale", "Temperature conversion", "enumeration", {
            "options": {"unverified": "Unverified: hide temperatures", "deciCTrial": "Provisional: raw / 10 degrees C",
                        "deciC": "Confirmed: raw / 10 degrees C"},
            "default": "unverified"}, "Confirm at another temperature before selecting Confirmed."),
        preference("interiorOffset", "Interior temperature correction (C)", "number", {
            "minimum": -50, "maximum": 50, "default": 0}, "Added after conversion. This measures inside the motor, not room air."),
        preference("batteryOffset", "Battery temperature correction (C)", "number", {
            "minimum": -50, "maximum": 50, "default": 0}, "Added after conversion to Celsius."),
        preference("temperatureUnit", "Temperature display unit", "enumeration", {
            "options": {"F": "Fahrenheit", "C": "Celsius"}, "default": "F"},
            "Display unit for both temperature readings after their conversion is confirmed."),
        preference("lightGraphMax", "Light graph maximum", "integer", {
            "minimum": 1, "maximum": 32767, "default": 1000},
            "Upper end of the relative light bar in sensor units. This sets the display scale, not a lux conversion."),
        preference("lightGraphBaseline", "Light graph baseline", "integer", {
            "minimum": 0, "maximum": 32766, "default": 0},
            "Lower reference for the relative light bar. A nighttime baseline does not establish zero lux."),
        preference("luxPerCount", "Light calibration: lux per count", "number", {
            "minimum": 0, "maximum": 10000, "default": 0}, "0 disables lux. Set only with a verified linear calibration against a light meter."),
        preference("lightDarkOffset", "Light calibration: dark raw value", "integer", {
            "minimum": -32768, "maximum": 32767, "default": 0}, "Estimated lux = (raw light - this value) x lux per count."),
    ]
    main_caps = ["windowShadeLevel", "windowShade", "switch", "battery", sensor_id,
                 "temperatureMeasurement", "illuminanceMeasurement", temperatures_id, light_id, legacy_light_id,
                 raw_id, native_id, control_id, "refresh"]
    manifest_path = HERE / "device-presentations.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    if manifest.get("namespace") != namespace:
        manifest = {}
    for debug in (False, True):
        key = "debug" if debug else "clean"
        profile = {"name": "msb-office-blind.v6" + ("-debug" if debug else ""),
            "components": [
                {"id": "main", "capabilities": [{"id": cap, "version": 1} for cap in main_caps],
                 "categories": [{"name": "Blind"}]}],
            "preferences": preferences}
        if key in manifest:
            profile["metadata"] = {"vid": manifest[key]["vid"], "mnmn": manifest[key]["mnmn"]}
        write_json(OUT / "package" / "profiles" / f"office-blind-percent{'-debug' if debug else ''}.yaml", profile)
    write_json(OUT / "package" / "profiles" / "bridge-config.yaml", {
        "name": "msb-bridge-config.v1",
        "components": [{"id": "main", "capabilities": [{"id": "refresh", "version": 1}],
                        "categories": [{"name": "Bridges"}]}],
        "preferences": [preference("bridgeToken", "Bridge token", "string", {
            "stringType": "password", "minLength": 0, "maxLength": 128, "default": ""
        }, "LAN token for this ESP32; its assigned blinds use this token automatically.")],
    })


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("namespace", help="Namespace assigned to your SmartThings account")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{2,40}", args.namespace):
        parser.error("Unexpected namespace format")
    namespace = args.namespace

    for relative in ("config.yaml", "search-parameter.yaml", "profiles/office-blind.yaml",
                     "profiles/office-blind-control.yaml", "profiles/office-blind-percent-v3.yaml",
                     "profiles/office-blind-percent-v3-debug.yaml", "src/init.lua", "src/interpretation.lua"):
        source = HERE / relative
        destination = OUT / "package" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            source.read_text(encoding="utf-8").replace("__NAMESPACE__", namespace),
            encoding="utf-8",
        )

    tilt = {
        "dashboard": {"states": [{"label": "Tilt {{position.value}}/200"}], "actions": []},
        "detailView": [
            {"label": "Measured tilt", "displayType": "state",
             "state": {"label": "{{position.value}}/200"}}
        ],
        "automation": {
            "conditions": [{"label": "Tilt position", "displayType": "slider",
                            "slider": {"value": "position.value", "range": [0, 200], "step": 1}}],
            "actions": [{"label": "Set tilt position", "displayType": "slider",
                         "slider": {"command": "setPosition", "range": [0, 200], "step": 1}}],
        },
        "id": f"{namespace}.mysmartblindsTiltPosition",
        "version": 1,
    }
    write_json(OUT / "presentations" / "tiltPosition.json", tilt)

    control = {
        "dashboard": {"states": [], "actions": []},
        "detailView": [
            {"label": "Requested tilt (100 = flat)", "displayType": "slider",
             "slider": {"value": "targetPosition.value", "command": "setPosition",
                        "range": [0, 200], "step": 1}}
        ],
        "automation": {
            "conditions": [],
            "actions": [{"label": "Set tilt position", "displayType": "slider",
                         "slider": {"command": "setPosition", "range": [0, 200], "step": 1}}],
        },
        "id": f"{namespace}.mysmartblindsTiltControl",
        "version": 1,
    }
    write_json(OUT / "presentations" / "tiltControl.json", control)

    labels = [
        ("solarCharging", "Solar charging"),
        ("usbCharging", "USB charging"),
        ("batteryVoltage", "Battery voltage raw"),
        ("batteryCharge", "Battery charge raw"),
        ("solarPanelVoltage", "Solar panel voltage raw"),
        ("interiorTemperature", "Interior temperature raw"),
        ("batteryTemperature", "Battery temperature raw"),
        ("light", "Light raw"),
    ]
    telemetry = {
        "dashboard": {"states": [], "actions": []},
        "detailView": [
            {"label": label, "displayType": "state", "state": {"label": "{{" + name + ".value}}"}}
            for name, label in labels
        ],
        "automation": {"conditions": [
            {"label": label, "displayType": "numberField",
             "numberField": {"value": name + ".value", "valueType": "integer"}}
            for name, label in labels if name not in ("solarCharging", "usbCharging")
        ], "actions": []},
        "id": f"{namespace}.mysmartblindsRawTelemetry",
        "version": 1,
    }
    write_json(OUT / "presentations" / "rawTelemetry.json", telemetry)

    translations = {
        "tiltPosition": {
            "tag": "en", "label": "Measured tilt",
            "attributes": {"position": {
                "label": "Measured tilt",
                "displayTemplate": "Measured tilt of {{device.label}} is {{value}}/200",
                "i18n": {},
            }},
            "commands": {"setPosition": {"label": "Set tilt position", "arguments": {}}},
        },
        "tiltControl": {
            "tag": "en", "label": "Requested tilt (100 = flat)",
            "attributes": {"targetPosition": {
                "label": "Requested tilt",
                "displayTemplate": "Requested tilt of {{device.label}} is {{value}}/200",
                "i18n": {},
            }},
            "commands": {"setPosition": {"label": "Set tilt position", "arguments": {}}},
        },
        "rawTelemetry": {
            "tag": "en", "label": "Blind sensors",
            "attributes": {name: {
                "label": label,
                "displayTemplate": label + " of {{device.label}} is {{value}}",
                "i18n": {},
            } for name, label in labels},
        },
    }
    for name, translation in translations.items():
        write_json(OUT / "translations" / f"{name}.en.json", translation)
    build_percent_ui(namespace)
    print(f"Prepared Edge package and presentations in {OUT}")


if __name__ == "__main__":
    main()
