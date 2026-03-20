#!/usr/bin/env python3
"""Enumerate connected iOS devices and installed applications."""

from __future__ import annotations

import argparse
import json
from typing import Any

from common import list_apps, list_devices


def print_json(payload: Any) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True))


def cmd_devices(args: argparse.Namespace) -> int:
    devices = list_devices(include_disconnected=args.include_disconnected)
    if args.json:
        print_json(
            [
                {
                    key: value
                    for key, value in device.items()
                    if key != "raw"
                }
                for device in devices
            ]
        )
        return 0

    if not devices:
        print("No selectable physical iOS/iPadOS/tvOS devices found.")
        print("Check USB/Wi-Fi pairing, trust prompts, Developer Mode, and DDI availability.")
        return 0

    for index, device in enumerate(devices, start=1):
        status = "selectable" if device["selectable"] else "seen-only"
        print(
            f"[{index}] {device['name']} | {device['udid']} | {device['model']} | "
            f"{device['platform']} {device['osVersion']} | transport={device['transport']} | "
            f"ddi={device['ddiServicesAvailable']} | {status}"
        )
    return 0


def cmd_apps(args: argparse.Namespace) -> int:
    apps = list_apps(args.udid, include_all_apps=not args.developer_only)
    if args.match:
        needle = args.match.lower()
        apps = [
            app
            for app in apps
            if needle in (app.get("name") or "").lower() or needle in (app.get("bundleId") or "").lower()
        ]
    if args.limit:
        apps = apps[: args.limit]

    if args.json:
        print_json(
            [
                {
                    key: value
                    for key, value in app.items()
                    if key != "raw"
                }
                for app in apps
            ]
        )
        return 0

    if not apps:
        print("No matching apps found.")
        return 0

    for index, app in enumerate(apps, start=1):
        name = app.get("name") or "<unknown>"
        bundle_id = app.get("bundleId") or "<missing bundle id>"
        version = app.get("version") or "?"
        flags = []
        if app.get("builtByDeveloper"):
            flags.append("dev")
        if app.get("defaultApp"):
            flags.append("system")
        if app.get("hidden"):
            flags.append("hidden")
        flag_text = ",".join(flags) if flags else "-"
        print(f"[{index}] {name} | {bundle_id} | version={version} | flags={flag_text}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Inspect connected iOS devices and installed apps.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    devices_parser = subparsers.add_parser("devices", help="List selectable physical devices.")
    devices_parser.add_argument("--include-disconnected", action="store_true", help="Also include paired but currently unusable devices.")
    devices_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    devices_parser.set_defaults(func=cmd_devices)

    apps_parser = subparsers.add_parser("apps", help="List installed apps for a device.")
    apps_parser.add_argument("--udid", required=True, help="Device UDID.")
    apps_parser.add_argument("--match", help="Filter by app name or bundle identifier substring.")
    apps_parser.add_argument("--limit", type=int, default=0, help="Limit the number of rows shown.")
    apps_parser.add_argument("--developer-only", action="store_true", help="Show only developer apps.")
    apps_parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    apps_parser.set_defaults(func=cmd_apps)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
