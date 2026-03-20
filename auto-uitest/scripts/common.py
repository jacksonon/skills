#!/usr/bin/env python3
"""Shared helpers for the auto-uitest skill."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest


class CommandError(RuntimeError):
    """Raised when an external command fails."""


def run(
    cmd: list[str],
    *,
    check: bool = True,
    capture_output: bool = True,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=capture_output,
        env=env,
        cwd=cwd,
    )
    if check and result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        details = stderr or stdout or f"exit code {result.returncode}"
        raise CommandError(f"{' '.join(cmd)} failed: {details}")
    return result


def devicectl_json(args: list[str]) -> dict[str, Any]:
    fd, raw_path = tempfile.mkstemp(prefix="auto-uitest-", suffix=".json")
    os.close(fd)
    path = Path(raw_path)
    try:
        run(["xcrun", "devicectl", *args, "--json-output", str(path)], capture_output=True)
        return json.loads(path.read_text())
    finally:
        path.unlink(missing_ok=True)


def is_selectable_device(device: dict[str, Any]) -> bool:
    capabilities = {
        item.get("featureIdentifier")
        for item in device.get("capabilities", [])
        if isinstance(item, dict)
    }
    connection = device.get("connectionProperties", {})
    props = device.get("deviceProperties", {})
    return bool(
        props.get("ddiServicesAvailable")
        or connection.get("tunnelState") == "connected"
        or {
            "com.apple.coredevice.feature.installapp",
            "com.apple.coredevice.feature.launchapplication",
            "com.apple.coredevice.feature.getdeviceinfo",
        }.issubset(capabilities)
    )


def list_devices(*, include_disconnected: bool = False) -> list[dict[str, Any]]:
    payload = devicectl_json(["list", "devices"])
    devices = payload.get("result", {}).get("devices", [])
    normalized: list[dict[str, Any]] = []
    for device in devices:
        hardware = device.get("hardwareProperties", {})
        connection = device.get("connectionProperties", {})
        props = device.get("deviceProperties", {})
        if hardware.get("reality") != "physical":
            continue
        if hardware.get("platform") not in {"iOS", "iPadOS", "tvOS"}:
            continue
        item = {
            "name": props.get("name") or hardware.get("marketingName") or hardware.get("productType"),
            "udid": hardware.get("udid"),
            "platform": hardware.get("platform"),
            "model": hardware.get("marketingName") or hardware.get("productType"),
            "productType": hardware.get("productType"),
            "osVersion": props.get("osVersionNumber"),
            "transport": connection.get("transportType"),
            "tunnelState": connection.get("tunnelState"),
            "paired": connection.get("pairingState") == "paired",
            "developerMode": props.get("developerModeStatus"),
            "ddiServicesAvailable": props.get("ddiServicesAvailable"),
            "identifier": device.get("identifier"),
            "selectable": is_selectable_device(device),
            "raw": device,
        }
        if include_disconnected or item["selectable"]:
            normalized.append(item)
    normalized.sort(key=lambda item: (not item["selectable"], item["name"] or "", item["udid"] or ""))
    return normalized


def list_apps(udid: str, *, include_all_apps: bool = True) -> list[dict[str, Any]]:
    args = ["device", "info", "apps", "--device", udid]
    if include_all_apps:
        args.append("--include-all-apps")
    payload = devicectl_json(args)
    apps = payload.get("result", {}).get("apps", [])
    normalized: list[dict[str, Any]] = []
    for app in apps:
        normalized.append(
            {
                "name": app.get("name"),
                "bundleId": app.get("bundleIdentifier"),
                "version": app.get("version"),
                "bundleVersion": app.get("bundleVersion"),
                "builtByDeveloper": app.get("builtByDeveloper"),
                "removable": app.get("removable"),
                "hidden": app.get("hidden"),
                "defaultApp": app.get("defaultApp"),
                "raw": app,
            }
        )
    normalized.sort(key=lambda item: ((item["name"] or "").lower(), item["bundleId"] or ""))
    return normalized


def find_wda_apps(apps: list[dict[str, Any]], bundle_id: str | None = None) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    expected = bundle_id.strip() if bundle_id else None
    for app in apps:
        candidate = app.get("bundleId") or ""
        name = app.get("name") or ""
        if expected:
            if candidate == expected:
                matches.append(app)
            continue
        if "webdriveragent" in candidate.lower() or "webdriveragent" in name.lower():
            matches.append(app)
            continue
        if candidate.endswith(".xctrunner") and "facebook" in candidate.lower():
            matches.append(app)
    return matches


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text())


def process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def read_pid(path: str | Path) -> int | None:
    target = Path(path)
    if not target.exists():
        return None
    try:
        return int(target.read_text().strip())
    except ValueError:
        return None


def stop_pid_file(path: str | Path, *, sig: int = signal.SIGTERM) -> bool:
    pid = read_pid(path)
    if not pid:
        return False
    if not process_alive(pid):
        Path(path).unlink(missing_ok=True)
        return False
    os.kill(pid, sig)
    Path(path).unlink(missing_ok=True)
    return True


def wait_for_port(host: str, port: int, *, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1.0):
                return True
        except OSError:
            time.sleep(0.25)
    return False


def http_json(
    method: str,
    url: str,
    *,
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urlrequest.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urlerror.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise CommandError(f"{method.upper()} {url} failed: {exc.code} {body}") from exc
    except urlerror.URLError as exc:
        raise CommandError(f"{method.upper()} {url} failed: {exc.reason}") from exc
    if not body:
        return {}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"value": body}
