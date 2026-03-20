#!/usr/bin/env python3
"""Manage WebDriverAgent and inspect the foreground app on a real iOS device."""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from common import (
    CommandError,
    find_wda_apps,
    http_json,
    list_apps,
    process_alive,
    read_json,
    read_pid,
    run,
    stop_pid_file,
    wait_for_port,
    write_json,
)
from testcase_artifacts import (
    append_timeline,
    load_case as load_test_case,
    next_step_number,
    now_iso,
    slugify,
    update_case,
)


def session_url(base_url: str, session_id: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/session/{session_id}{path}"


def parse_session_id(payload: Any) -> str:
    if isinstance(payload, dict):
        value = payload.get("value")
        if isinstance(value, dict) and value.get("sessionId"):
            return value["sessionId"]
        if payload.get("sessionId"):
            return payload["sessionId"]
    raise CommandError(f"Unable to parse session id from response: {payload}")


def pid_file_payload(path: str | Path) -> dict[str, Any]:
    pid = read_pid(path)
    return {
        "pidFile": str(path),
        "pid": pid,
        "running": bool(pid and process_alive(pid)),
    }


def load_case_context(case_dir: str | None) -> tuple[dict[str, Any] | None, Path | None]:
    if not case_dir:
        return None, None
    metadata = load_test_case(case_dir)
    return metadata, Path(metadata["caseDir"])


def update_case_metadata(metadata: dict[str, Any] | None, **fields: Any) -> None:
    if not metadata:
        return
    changed = False
    for key, value in fields.items():
        if value is None:
            continue
        if metadata.get(key) != value:
            metadata[key] = value
            changed = True
    metadata["updatedAt"] = now_iso()
    if changed or True:
        update_case(Path(metadata["projectRoot"]), metadata)


def log_case_event(
    metadata: dict[str, Any] | None,
    *,
    kind: str,
    summary: str,
    data: dict[str, Any] | None = None,
    step: int | None = None,
) -> None:
    if not metadata:
        return
    case_dir = Path(metadata["caseDir"])
    append_timeline(
        case_dir,
        {
            "ts": now_iso(),
            "step": step if step is not None else next_step_number(case_dir),
            "kind": kind,
            "summary": summary,
            "data": data or {},
        },
    )
    metadata["updatedAt"] = now_iso()
    update_case(Path(metadata["projectRoot"]), metadata)


def case_path(case_dir: Path | None, relative: str) -> str | None:
    if not case_dir:
        return None
    return str(case_dir / relative)


def resolve_case_step_outputs(case_dir: Path, label: str) -> tuple[int, str, Path, Path]:
    step = next_step_number(case_dir)
    prefix = f"{step:03d}-{slugify(label, fallback=f'step-{step:03d}')}"
    return (
        step,
        prefix,
        case_dir / "captures" / f"{prefix}.xml",
        case_dir / "captures" / f"{prefix}.png",
    )


def resolve_session_file(session_file: str | None, case_dir: Path | None) -> str:
    if session_file:
        return str(Path(session_file).expanduser().resolve())
    if case_dir:
        return str((case_dir / "session" / "wda-session.json").resolve())
    raise CommandError("Provide --session-file or --case-dir")


def resolve_runtime_path(user_value: str | None, case_dir: Path | None, relative: str, required_name: str) -> str:
    if user_value:
        return str(Path(user_value).expanduser().resolve())
    if case_dir:
        return str((case_dir / relative).resolve())
    raise CommandError(f"Provide {required_name} or --case-dir")


@lru_cache(maxsize=1)
def detect_iproxy_mode() -> str:
    try:
        output = subprocess.run(
            ["iproxy", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as exc:
        raise CommandError("iproxy is not installed or not on PATH") from exc
    help_text = f"{output.stdout}\n{output.stderr}"
    if "LOCAL_PORT:DEVICE_PORT" in help_text or "-u, --udid" in help_text:
        return "modern"
    return "legacy"


def build_iproxy_command(udid: str, local_port: int, device_port: int) -> list[str]:
    if detect_iproxy_mode() == "modern":
        return ["iproxy", "-u", udid, f"{local_port}:{device_port}"]
    return ["iproxy", str(local_port), str(device_port), udid]


def launch_iproxy(udid: str, local_port: int, device_port: int, pid_file: str | Path, log_file: str | Path) -> dict[str, Any]:
    existing = read_pid(pid_file)
    if existing and process_alive(existing):
        return {
            "changed": False,
            "localPort": local_port,
            "devicePort": device_port,
            **pid_file_payload(pid_file),
        }

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(
        build_iproxy_command(udid, local_port, device_port),
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )
    Path(pid_file).parent.mkdir(parents=True, exist_ok=True)
    Path(pid_file).write_text(f"{process.pid}\n")
    if not wait_for_port("127.0.0.1", local_port, timeout=15.0):
        raise CommandError(f"iproxy did not expose 127.0.0.1:{local_port} in time")
    return {
        "changed": True,
        "localPort": local_port,
        "devicePort": device_port,
        **pid_file_payload(pid_file),
    }


def probe_wda(base_url: str, *, timeout: float = 5.0) -> dict[str, Any]:
    payload = http_json("GET", f"{base_url.rstrip('/')}/status", timeout=timeout)
    value = payload.get("value", payload)
    return {
        "reachable": True,
        "wda": value,
    }


def start_runner(
    cmd: list[str],
    *,
    pid_file: str | Path,
    log_file: str | Path,
    wait_seconds: int,
    base_url: str,
    udid: str,
    local_port: int,
    device_port: int,
    iproxy_pid_file: str | Path | None,
    iproxy_log_file: str | Path | None,
) -> dict[str, Any]:
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("a", encoding="utf-8")
    process = subprocess.Popen(cmd, stdout=handle, stderr=subprocess.STDOUT, text=True)
    Path(pid_file).parent.mkdir(parents=True, exist_ok=True)
    Path(pid_file).write_text(f"{process.pid}\n")

    if iproxy_pid_file and iproxy_log_file:
        launch_iproxy(udid, local_port, device_port, iproxy_pid_file, iproxy_log_file)

    deadline = time.time() + wait_seconds
    last_error = None
    while time.time() < deadline:
        try:
            status = probe_wda(base_url, timeout=3.0)
            return {
                "changed": True,
                "runnerPidFile": str(pid_file),
                "runnerPid": process.pid,
                "logFile": str(log_file),
                "status": status,
            }
        except CommandError as exc:
            last_error = str(exc)
            time.sleep(2.0)
        except Exception as exc:
            last_error = str(exc)
            time.sleep(2.0)
    raise CommandError(f"WDA did not become reachable within {wait_seconds}s. Last probe error: {last_error}")


def create_session(base_url: str) -> str:
    payload = http_json(
        "POST",
        f"{base_url.rstrip('/')}/session",
        payload={
            "capabilities": {"alwaysMatch": {}, "firstMatch": [{}]},
            "desiredCapabilities": {},
        },
        timeout=30.0,
    )
    return parse_session_id(payload)


def delete_session(base_url: str, session_id: str) -> None:
    http_json("DELETE", session_url(base_url, session_id, ""), timeout=15.0)


def save_session(path: str | Path, *, base_url: str, session_id: str, udid: str, bundle_id: str | None = None) -> None:
    payload = {
        "baseUrl": base_url.rstrip("/"),
        "sessionId": session_id,
        "udid": udid,
        "bundleId": bundle_id,
        "openedAt": int(time.time()),
    }
    write_json(path, payload)


def load_session(path: str | Path) -> dict[str, Any]:
    payload = read_json(path)
    if "baseUrl" not in payload or "sessionId" not in payload or "udid" not in payload:
        raise CommandError(f"Invalid session file: {path}")
    return payload


def perform_actions(base_url: str, session_id: str, actions: list[dict[str, Any]]) -> Any:
    return http_json(
        "POST",
        session_url(base_url, session_id, "/actions"),
        payload={"actions": actions},
        timeout=30.0,
    )


def tap_actions(x: int, y: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "pointer",
            "id": "finger1",
            "parameters": {"pointerType": "touch"},
            "actions": [
                {"type": "pointerMove", "duration": 0, "x": x, "y": y},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": 80},
                {"type": "pointerUp", "button": 0},
            ],
        }
    ]


def swipe_actions(x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> list[dict[str, Any]]:
    return [
        {
            "type": "pointer",
            "id": "finger1",
            "parameters": {"pointerType": "touch"},
            "actions": [
                {"type": "pointerMove", "duration": 0, "x": x1, "y": y1},
                {"type": "pointerDown", "button": 0},
                {"type": "pause", "duration": 100},
                {"type": "pointerMove", "duration": duration_ms, "x": x2, "y": y2},
                {"type": "pointerUp", "button": 0},
            ],
        }
    ]


def type_text(base_url: str, session_id: str, text: str) -> Any:
    letters = list(text)
    try:
        return http_json(
            "POST",
            session_url(base_url, session_id, "/wda/keys"),
            payload={"value": letters},
            timeout=30.0,
        )
    except CommandError:
        return http_json(
            "POST",
            session_url(base_url, session_id, "/keys"),
            payload={"text": text, "value": letters},
            timeout=30.0,
        )


def cmd_status(args: argparse.Namespace) -> int:
    case_metadata, _ = load_case_context(args.case_dir)
    apps = list_apps(args.udid)
    matches = find_wda_apps(apps, args.wda_bundle_id)
    result: dict[str, Any] = {
        "udid": args.udid,
        "wdaBundleId": args.wda_bundle_id,
        "wdaInstalled": bool(matches),
        "wdaApps": [{k: v for k, v in app.items() if k != "raw"} for app in matches],
        "iproxy": None,
        "wdaHttp": {"reachable": False},
    }
    if args.pid_file and args.log_file:
        result["iproxy"] = launch_iproxy(args.udid, args.local_port, args.device_port, args.pid_file, args.log_file)
    if args.probe_http:
        try:
            result["wdaHttp"] = probe_wda(args.base_url, timeout=4.0)
        except CommandError as exc:
            result["wdaHttp"] = {"reachable": False, "error": str(exc)}
        except Exception as exc:
            result["wdaHttp"] = {"reachable": False, "error": str(exc)}
    log_case_event(
        case_metadata,
        kind="wda_status_checked",
        summary="Checked WDA install and HTTP reachability",
        data={
            "udid": args.udid,
            "wdaInstalled": result["wdaInstalled"],
            "wdaReachable": result["wdaHttp"].get("reachable"),
        },
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def cmd_ensure_forward(args: argparse.Namespace) -> int:
    case_metadata, case_dir = load_case_context(args.case_dir)
    pid_file = resolve_runtime_path(args.pid_file, case_dir, "session/iproxy.pid", "--pid-file")
    log_file = resolve_runtime_path(args.log_file, case_dir, "logs/iproxy.log", "--log-file")
    payload = launch_iproxy(args.udid, args.local_port, args.device_port, pid_file, log_file)
    log_case_event(
        case_metadata,
        kind="iproxy_ready",
        summary="Prepared local WDA port forwarding",
        data={"udid": args.udid, "pidFile": pid_file, "logFile": log_file},
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_stop_forward(args: argparse.Namespace) -> int:
    changed = stop_pid_file(args.pid_file)
    print(json.dumps({"changed": changed, "pidFile": args.pid_file}, indent=2, sort_keys=True))
    return 0


def resolve_xctestrun_path(bootstrap_path: str | None, xctestrun_path: str | None) -> Path:
    if xctestrun_path:
        path = Path(xctestrun_path).expanduser().resolve()
        if not path.exists():
            raise CommandError(f"xctestrun file not found: {path}")
        return path
    if not bootstrap_path:
        raise CommandError("Provide --xctestrun-path or --bootstrap-path")
    root = Path(bootstrap_path).expanduser().resolve()
    matches = sorted(root.rglob("*.xctestrun"))
    if not matches:
        raise CommandError(f"No .xctestrun file found under {root}")
    iphone_matches = [path for path in matches if "iphoneos" in path.name.lower()]
    return iphone_matches[0] if iphone_matches else matches[0]


def cmd_start_prebuilt(args: argparse.Namespace) -> int:
    case_metadata, case_dir = load_case_context(args.case_dir)
    xctestrun = resolve_xctestrun_path(args.bootstrap_path, args.xctestrun_path)
    pid_file = resolve_runtime_path(args.pid_file, case_dir, "session/iproxy.pid", "--pid-file")
    log_file = resolve_runtime_path(args.log_file, case_dir, "logs/iproxy.log", "--log-file")
    runner_pid_file = resolve_runtime_path(args.runner_pid_file, case_dir, "session/wda-runner.pid", "--runner-pid-file")
    runner_log_file = resolve_runtime_path(args.runner_log_file, case_dir, "logs/wda-runner.log", "--runner-log-file")
    cmd = [
        "xcodebuild",
        "test-without-building",
        "-xctestrun",
        str(xctestrun),
        "-destination",
        f"id={args.udid}",
        f"USE_PORT={args.device_port}",
    ]
    payload = start_runner(
        cmd,
        pid_file=runner_pid_file,
        log_file=runner_log_file,
        wait_seconds=args.wait_seconds,
        base_url=args.base_url,
        udid=args.udid,
        local_port=args.local_port,
        device_port=args.device_port,
        iproxy_pid_file=pid_file,
        iproxy_log_file=log_file,
    )
    payload["xctestrun"] = str(xctestrun)
    payload["command"] = cmd
    log_case_event(
        case_metadata,
        kind="wda_started_prebuilt",
        summary="Started WDA from prebuilt xctestrun bundle",
        data={
            "udid": args.udid,
            "xctestrun": str(xctestrun),
            "runnerLogFile": runner_log_file,
            "iproxyLogFile": log_file,
        },
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_start_source(args: argparse.Namespace) -> int:
    case_metadata, case_dir = load_case_context(args.case_dir)
    repo = Path(args.wda_repo).expanduser().resolve()
    project = repo / "WebDriverAgent.xcodeproj"
    if not project.exists():
        raise CommandError(f"Missing WebDriverAgent.xcodeproj under {repo}")
    pid_file = resolve_runtime_path(args.pid_file, case_dir, "session/iproxy.pid", "--pid-file")
    log_file = resolve_runtime_path(args.log_file, case_dir, "logs/iproxy.log", "--log-file")
    runner_pid_file = resolve_runtime_path(args.runner_pid_file, case_dir, "session/wda-runner.pid", "--runner-pid-file")
    runner_log_file = resolve_runtime_path(args.runner_log_file, case_dir, "logs/wda-runner.log", "--runner-log-file")
    cmd = [
        "xcodebuild",
        "test",
        "-project",
        str(project),
        "-scheme",
        args.scheme,
        "-destination",
        f"id={args.udid}",
        f"USE_PORT={args.device_port}",
    ]
    if args.derived_data:
        cmd.extend(["-derivedDataPath", str(Path(args.derived_data).expanduser().resolve())])
    if args.allow_provisioning_updates:
        cmd.append("-allowProvisioningUpdates")
    if args.team_id:
        cmd.append(f"DEVELOPMENT_TEAM={args.team_id}")
    if args.code_sign_style:
        cmd.append(f"CODE_SIGN_STYLE={args.code_sign_style}")
    if args.signing_cert:
        cmd.append(f"CODE_SIGN_IDENTITY={args.signing_cert}")
    if args.provisioning_profile_specifier:
        cmd.append(f"PROVISIONING_PROFILE_SPECIFIER={args.provisioning_profile_specifier}")
    if args.updated_wda_bundle_id:
        cmd.append(f"PRODUCT_BUNDLE_IDENTIFIER={args.updated_wda_bundle_id}")
    payload = start_runner(
        cmd,
        pid_file=runner_pid_file,
        log_file=runner_log_file,
        wait_seconds=args.wait_seconds,
        base_url=args.base_url,
        udid=args.udid,
        local_port=args.local_port,
        device_port=args.device_port,
        iproxy_pid_file=pid_file,
        iproxy_log_file=log_file,
    )
    payload["command"] = cmd
    log_case_event(
        case_metadata,
        kind="wda_started_source",
        summary="Started WDA from source checkout",
        data={
            "udid": args.udid,
            "wdaRepo": str(repo),
            "runnerLogFile": runner_log_file,
            "iproxyLogFile": log_file,
            "teamId": args.team_id,
        },
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_stop_runner(args: argparse.Namespace) -> int:
    changed = stop_pid_file(args.runner_pid_file)
    print(json.dumps({"changed": changed, "runnerPidFile": args.runner_pid_file}, indent=2, sort_keys=True))
    return 0


def cmd_launch_app(args: argparse.Namespace) -> int:
    case_metadata, _ = load_case_context(args.case_dir)
    cmd = [
        "xcrun",
        "devicectl",
        "device",
        "process",
        "launch",
        "--device",
        args.udid,
        args.bundle_id,
        "--activate",
        "--terminate-existing",
    ]
    if args.payload_url:
        cmd.extend(["--payload-url", args.payload_url])
    run(cmd, capture_output=False)
    update_case_metadata(case_metadata, udid=args.udid, bundleId=args.bundle_id)
    log_case_event(
        case_metadata,
        kind="app_launched",
        summary=args.case_summary or "Launched target app on device",
        data={"udid": args.udid, "bundleId": args.bundle_id, "payloadUrl": args.payload_url},
    )
    print(json.dumps({"changed": True, "udid": args.udid, "bundleId": args.bundle_id}, indent=2, sort_keys=True))
    return 0


def cmd_open_session(args: argparse.Namespace) -> int:
    case_metadata, case_dir = load_case_context(args.case_dir)
    session_file = resolve_session_file(args.session_file, case_dir)
    session_id = create_session(args.base_url)
    save_session(session_file, base_url=args.base_url, session_id=session_id, udid=args.udid, bundle_id=args.bundle_id)
    update_case_metadata(case_metadata, udid=args.udid, bundleId=args.bundle_id)
    log_case_event(
        case_metadata,
        kind="wda_session_opened",
        summary="Opened WDA session",
        data={"sessionFile": session_file, "sessionId": session_id, "baseUrl": args.base_url.rstrip("/")},
    )
    print(json.dumps({"sessionFile": session_file, "sessionId": session_id, "baseUrl": args.base_url.rstrip("/")}, indent=2, sort_keys=True))
    return 0


def cmd_close_session(args: argparse.Namespace) -> int:
    case_metadata, case_dir = load_case_context(args.case_dir)
    session_file = resolve_session_file(args.session_file, case_dir)
    session = load_session(session_file)
    delete_session(session["baseUrl"], session["sessionId"])
    if args.delete_session_file:
        Path(session_file).unlink(missing_ok=True)
    log_case_event(
        case_metadata,
        kind="wda_session_closed",
        summary="Closed WDA session",
        data={"sessionFile": session_file, "sessionId": session["sessionId"]},
    )
    print(json.dumps({"changed": True, "sessionFile": session_file}, indent=2, sort_keys=True))
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    case_metadata, case_dir = load_case_context(args.case_dir)
    session_file = resolve_session_file(args.session_file, case_dir)
    session = load_session(session_file)
    source = http_json("GET", session_url(session["baseUrl"], session["sessionId"], "/source"), timeout=30.0)
    screenshot = http_json("GET", session_url(session["baseUrl"], session["sessionId"], "/screenshot"), timeout=30.0)
    xml = source.get("value") if isinstance(source, dict) else source
    image_b64 = screenshot.get("value") if isinstance(screenshot, dict) else None
    payload: dict[str, Any] = {"sessionFile": session_file}
    step = None
    if case_dir and not args.xml_out and not args.screenshot_out:
        label = args.label or "screen"
        step, _, xml_path, screenshot_path = resolve_case_step_outputs(case_dir, label)
        args.xml_out = str(xml_path)
        args.screenshot_out = str(screenshot_path)
    if args.xml_out:
        Path(args.xml_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.xml_out).write_text(xml if isinstance(xml, str) else json.dumps(xml, indent=2))
        payload["xmlOut"] = args.xml_out
    else:
        payload["xml"] = xml
    if args.screenshot_out and image_b64:
        Path(args.screenshot_out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.screenshot_out).write_bytes(base64.b64decode(image_b64))
        payload["screenshotOut"] = args.screenshot_out
    elif args.include_screenshot and image_b64:
        payload["screenshotBase64"] = image_b64
    log_case_event(
        case_metadata,
        step=step,
        kind="screen_captured",
        summary=args.case_summary or f"Captured screen state for {args.label or 'screen'}",
        data={
            "sessionFile": session_file,
            "xmlOut": payload.get("xmlOut"),
            "screenshotOut": payload.get("screenshotOut"),
            "label": args.label,
        },
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_tap(args: argparse.Namespace) -> int:
    case_metadata, case_dir = load_case_context(args.case_dir)
    session_file = resolve_session_file(args.session_file, case_dir)
    session = load_session(session_file)
    payload = perform_actions(session["baseUrl"], session["sessionId"], tap_actions(args.x, args.y))
    log_case_event(
        case_metadata,
        kind="tap",
        summary=args.case_summary or "Tapped screen coordinate",
        data={"sessionFile": session_file, "x": args.x, "y": args.y},
    )
    print(json.dumps({"changed": True, "response": payload}, indent=2, sort_keys=True))
    return 0


def cmd_swipe(args: argparse.Namespace) -> int:
    case_metadata, case_dir = load_case_context(args.case_dir)
    session_file = resolve_session_file(args.session_file, case_dir)
    session = load_session(session_file)
    payload = perform_actions(
        session["baseUrl"],
        session["sessionId"],
        swipe_actions(args.x1, args.y1, args.x2, args.y2, args.duration_ms),
    )
    log_case_event(
        case_metadata,
        kind="swipe",
        summary=args.case_summary or "Performed swipe gesture",
        data={
            "sessionFile": session_file,
            "x1": args.x1,
            "y1": args.y1,
            "x2": args.x2,
            "y2": args.y2,
            "durationMs": args.duration_ms,
        },
    )
    print(json.dumps({"changed": True, "response": payload}, indent=2, sort_keys=True))
    return 0


def cmd_type_text(args: argparse.Namespace) -> int:
    case_metadata, case_dir = load_case_context(args.case_dir)
    session_file = resolve_session_file(args.session_file, case_dir)
    session = load_session(session_file)
    payload = type_text(session["baseUrl"], session["sessionId"], args.text)
    log_case_event(
        case_metadata,
        kind="type_text",
        summary=args.case_summary or "Typed text into focused field",
        data={"sessionFile": session_file, "textLength": len(args.text)},
    )
    print(json.dumps({"changed": True, "response": payload}, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bootstrap WDA and inspect a foreground iOS app.")
    parser.set_defaults(func=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_base_url(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--base-url", default="http://127.0.0.1:8100", help="Local WDA base URL.")

    def add_forward_args(subparser: argparse.ArgumentParser, *, require_files: bool) -> None:
        subparser.add_argument("--udid", required=True, help="Device UDID.")
        subparser.add_argument("--local-port", type=int, default=8100, help="Local forwarded port.")
        subparser.add_argument("--device-port", type=int, default=8100, help="Device-side WDA port.")
        subparser.add_argument("--pid-file", required=require_files, help="PID file for iproxy.")
        subparser.add_argument("--log-file", required=require_files, help="Log file for iproxy.")

    def add_case_dir(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--case-dir", help="Optional testcase directory for automatic artifact persistence.")

    def add_case_summary(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--case-summary", help="Optional custom timeline summary for this action.")

    status_parser = subparsers.add_parser("status", help="Check whether WDA is installed and optionally probe HTTP status.")
    add_forward_args(status_parser, require_files=False)
    add_base_url(status_parser)
    add_case_dir(status_parser)
    status_parser.add_argument("--wda-bundle-id", default=None, help="Exact WDA bundle id to look for.")
    status_parser.add_argument("--probe-http", action="store_true", help="Probe GET /status through the local base URL.")
    status_parser.set_defaults(func=cmd_status)

    forward_parser = subparsers.add_parser("ensure-forward", help="Start or reuse iproxy for the device.")
    add_forward_args(forward_parser, require_files=False)
    add_case_dir(forward_parser)
    forward_parser.set_defaults(func=cmd_ensure_forward)

    stop_forward_parser = subparsers.add_parser("stop-forward", help="Stop the iproxy process tracked by a PID file.")
    stop_forward_parser.add_argument("--pid-file", required=True, help="PID file created by ensure-forward.")
    stop_forward_parser.set_defaults(func=cmd_stop_forward)

    prebuilt_parser = subparsers.add_parser("start-prebuilt", help="Launch a prebuilt WDA xctestrun bundle.")
    add_forward_args(prebuilt_parser, require_files=False)
    add_base_url(prebuilt_parser)
    add_case_dir(prebuilt_parser)
    prebuilt_parser.add_argument("--bootstrap-path", help="Directory containing one or more .xctestrun files.")
    prebuilt_parser.add_argument("--xctestrun-path", help="Specific .xctestrun file to use.")
    prebuilt_parser.add_argument("--runner-pid-file", help="PID file for the xcodebuild process.")
    prebuilt_parser.add_argument("--runner-log-file", help="Log file for xcodebuild output.")
    prebuilt_parser.add_argument("--wait-seconds", type=int, default=90, help="How long to wait for WDA to answer /status.")
    prebuilt_parser.set_defaults(func=cmd_start_prebuilt)

    source_parser = subparsers.add_parser("start-source", help="Build and run WDA from source with xcodebuild.")
    add_forward_args(source_parser, require_files=False)
    add_base_url(source_parser)
    add_case_dir(source_parser)
    source_parser.add_argument("--wda-repo", required=True, help="Path to a WebDriverAgent repository checkout.")
    source_parser.add_argument("--scheme", default="WebDriverAgentRunner", help="Xcode scheme to run.")
    source_parser.add_argument("--team-id", help="Apple Developer team ID for signing.")
    source_parser.add_argument("--code-sign-style", help="Optional CODE_SIGN_STYLE override such as Manual or Automatic.")
    source_parser.add_argument("--signing-cert", help="Optional CODE_SIGN_IDENTITY value.")
    source_parser.add_argument("--provisioning-profile-specifier", help="Optional PROVISIONING_PROFILE_SPECIFIER override.")
    source_parser.add_argument("--updated-wda-bundle-id", help="Override PRODUCT_BUNDLE_IDENTIFIER if signing requires it.")
    source_parser.add_argument("--derived-data", help="Optional derived data path.")
    source_parser.add_argument("--allow-provisioning-updates", action="store_true", help="Pass -allowProvisioningUpdates to xcodebuild.")
    source_parser.add_argument("--runner-pid-file", help="PID file for the xcodebuild process.")
    source_parser.add_argument("--runner-log-file", help="Log file for xcodebuild output.")
    source_parser.add_argument("--wait-seconds", type=int, default=120, help="How long to wait for WDA to answer /status.")
    source_parser.set_defaults(func=cmd_start_source)

    stop_runner_parser = subparsers.add_parser("stop-runner", help="Stop the xcodebuild/WDA runner tracked by a PID file.")
    stop_runner_parser.add_argument("--runner-pid-file", required=True, help="PID file created by start-prebuilt or start-source.")
    stop_runner_parser.set_defaults(func=cmd_stop_runner)

    launch_parser = subparsers.add_parser("launch-app", help="Launch a target app with devicectl.")
    launch_parser.add_argument("--udid", required=True, help="Device UDID.")
    launch_parser.add_argument("--bundle-id", required=True, help="Target application bundle identifier.")
    launch_parser.add_argument("--payload-url", help="Optional deep link to pass when launching.")
    add_case_dir(launch_parser)
    add_case_summary(launch_parser)
    launch_parser.set_defaults(func=cmd_launch_app)

    open_session_parser = subparsers.add_parser("open-session", help="Create a WDA session and persist it to disk.")
    add_base_url(open_session_parser)
    open_session_parser.add_argument("--udid", required=True, help="Device UDID.")
    add_case_dir(open_session_parser)
    add_case_summary(open_session_parser)
    open_session_parser.add_argument("--session-file", help="Where to persist the WDA session metadata. Defaults to <case-dir>/session/wda-session.json.")
    open_session_parser.add_argument("--bundle-id", help="Optional bundle id for bookkeeping in the session file.")
    open_session_parser.set_defaults(func=cmd_open_session)

    close_session_parser = subparsers.add_parser("close-session", help="Delete a WDA session.")
    add_case_dir(close_session_parser)
    add_case_summary(close_session_parser)
    close_session_parser.add_argument("--session-file", help="Session file created by open-session. Defaults to <case-dir>/session/wda-session.json.")
    close_session_parser.add_argument("--delete-session-file", action="store_true", help="Remove the session file after closing the session.")
    close_session_parser.set_defaults(func=cmd_close_session)

    snapshot_parser = subparsers.add_parser("snapshot", help="Dump the current XML hierarchy and screenshot.")
    add_case_dir(snapshot_parser)
    add_case_summary(snapshot_parser)
    snapshot_parser.add_argument("--session-file", help="Session file created by open-session. Defaults to <case-dir>/session/wda-session.json.")
    snapshot_parser.add_argument("--label", help="Step label used for automatic capture filenames when --case-dir is set.")
    snapshot_parser.add_argument("--xml-out", help="Write the page source XML to this path.")
    snapshot_parser.add_argument("--screenshot-out", help="Write the screenshot PNG to this path.")
    snapshot_parser.add_argument("--include-screenshot", action="store_true", help="Include screenshotBase64 in stdout if no file path is provided.")
    snapshot_parser.set_defaults(func=cmd_snapshot)

    tap_parser = subparsers.add_parser("tap", help="Tap the screen at a coordinate using W3C actions.")
    add_case_dir(tap_parser)
    add_case_summary(tap_parser)
    tap_parser.add_argument("--session-file", help="Session file created by open-session. Defaults to <case-dir>/session/wda-session.json.")
    tap_parser.add_argument("--x", required=True, type=int, help="Tap X coordinate.")
    tap_parser.add_argument("--y", required=True, type=int, help="Tap Y coordinate.")
    tap_parser.set_defaults(func=cmd_tap)

    swipe_parser = subparsers.add_parser("swipe", help="Swipe between two coordinates using W3C actions.")
    add_case_dir(swipe_parser)
    add_case_summary(swipe_parser)
    swipe_parser.add_argument("--session-file", help="Session file created by open-session. Defaults to <case-dir>/session/wda-session.json.")
    swipe_parser.add_argument("--x1", required=True, type=int)
    swipe_parser.add_argument("--y1", required=True, type=int)
    swipe_parser.add_argument("--x2", required=True, type=int)
    swipe_parser.add_argument("--y2", required=True, type=int)
    swipe_parser.add_argument("--duration-ms", type=int, default=500, help="Pointer move duration in milliseconds.")
    swipe_parser.set_defaults(func=cmd_swipe)

    type_parser = subparsers.add_parser("type-text", help="Type into the currently focused field.")
    add_case_dir(type_parser)
    add_case_summary(type_parser)
    type_parser.add_argument("--session-file", help="Session file created by open-session. Defaults to <case-dir>/session/wda-session.json.")
    type_parser.add_argument("--text", required=True, help="Text to type.")
    type_parser.set_defaults(func=cmd_type_text)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except CommandError as exc:
        print(json.dumps({"error": str(exc)}, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
