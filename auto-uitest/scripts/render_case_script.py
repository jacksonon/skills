#!/usr/bin/env python3
"""Render a reusable replay script from one auto-uitest testcase bundle."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from testcase_artifacts import load_case, slugify


RELEVANT_KINDS = {
    "wda_status_checked",
    "app_launched",
    "wda_session_opened",
    "tap",
    "swipe",
    "type_text",
    "screen_captured",
    "wda_session_closed",
}

TEXT_INPUT_TAGS = {
    "XCUIElementTypeSearchField",
    "XCUIElementTypeTextField",
    "XCUIElementTypeSecureTextField",
    "XCUIElementTypeTextView",
}


def read_timeline(case_dir: Path) -> list[dict[str, Any]]:
    timeline_path = case_dir / "timeline.jsonl"
    events: list[dict[str, Any]] = []
    for line in timeline_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def next_relevant_ts(events: list[dict[str, Any]], index: int) -> datetime | None:
    current = parse_ts(events[index].get("ts"))
    if current is None:
        return None
    for candidate in events[index + 1 :]:
        if candidate.get("kind") in RELEVANT_KINDS:
            return parse_ts(candidate.get("ts"))
    return None


def extract_text_candidates(xml_path: Path) -> list[str]:
    if not xml_path.exists():
        return []
    try:
        root = ET.fromstring(xml_path.read_text(encoding="utf-8"))
    except ET.ParseError:
        return []
    candidates: list[str] = []
    for element in root.iter():
        if element.tag not in TEXT_INPUT_TAGS:
            continue
        value = (element.attrib.get("value") or "").strip()
        placeholder = (element.attrib.get("placeholderValue") or "").strip()
        if value and value != placeholder:
            candidates.append(value)
    unique: list[str] = []
    for item in candidates:
        if item not in unique:
            unique.append(item)
    return unique


def infer_text_from_capture(case_dir: Path, events: list[dict[str, Any]], index: int) -> str | None:
    expected_len = events[index].get("data", {}).get("textLength")
    for candidate in events[index + 1 :]:
        kind = candidate.get("kind")
        if kind == "screen_captured":
            xml_out = candidate.get("data", {}).get("xmlOut")
            if not xml_out:
                continue
            values = extract_text_candidates(Path(xml_out))
            if expected_len is not None:
                values = [value for value in values if len(value) == expected_len]
            if values:
                return values[0]
        if kind in {"type_text", "wda_session_closed", "app_launched"}:
            break
    return None


def infer_text_from_neighbor_actions(events: list[dict[str, Any]], index: int) -> str | None:
    for offset in (-2, -1, 1, 2):
        pos = index + offset
        if pos < 0 or pos >= len(events):
            continue
        event = events[pos]
        data = event.get("data", {})
        if isinstance(data.get("query"), str) and data["query"].strip():
            return data["query"].strip()
        summary = event.get("summary") or ""
        match = re.search(r"Searched for (.+)$", summary)
        if match:
            return match.group(1).strip()
    return None


def infer_text(case_dir: Path, events: list[dict[str, Any]], index: int) -> tuple[str | None, str | None]:
    event = events[index]
    explicit = event.get("data", {}).get("text")
    if isinstance(explicit, str) and explicit:
        return explicit, "timeline"
    capture_guess = infer_text_from_capture(case_dir, events, index)
    if capture_guess:
        return capture_guess, "capture"
    neighbor_guess = infer_text_from_neighbor_actions(events, index)
    if neighbor_guess:
        return neighbor_guess, "neighbor"
    return None, None


def derive_wait_seconds(events: list[dict[str, Any]], index: int) -> float | None:
    current = parse_ts(events[index].get("ts"))
    nxt = next_relevant_ts(events, index)
    if current is None or nxt is None:
        return None
    delta = (nxt - current).total_seconds()
    if delta < 1.5:
        return None
    return min(round(delta, 1), 5.0)


def render_sequence(case_dir: Path, events: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for index, event in enumerate(events):
        kind = event.get("kind")
        if kind not in RELEVANT_KINDS:
            continue
        summary = event.get("summary") or kind
        data = event.get("data") or {}
        if kind == "wda_status_checked":
            lines.append(f'    status(case_summary={summary!r})')
        elif kind == "app_launched":
            lines.append(f'    launch_app(case_summary={summary!r})')
        elif kind == "wda_session_opened":
            lines.append(f'    open_session(case_summary={summary!r})')
        elif kind == "tap":
            lines.append(f"    tap({int(data['x'])}, {int(data['y'])}, case_summary={summary!r})")
        elif kind == "swipe":
            lines.append(
                "    swipe("
                f"{int(data['x1'])}, {int(data['y1'])}, {int(data['x2'])}, {int(data['y2'])}, "
                f"case_summary={summary!r})"
            )
        elif kind == "type_text":
            text_value, source = infer_text(case_dir, events, index)
            if text_value is None:
                lines.append("    # TODO: fill the exact text for this step before reusing the script.")
                lines.append(f"    type_text('<FILL_TEXT>', case_summary={summary!r})")
            else:
                lines.append(f"    type_text({text_value!r}, case_summary={summary!r})")
                if source != "timeline":
                    lines.append(f"    # text inferred from {source} evidence")
        elif kind == "screen_captured":
            label = data.get("label") or f"step-{event.get('step', 0)}"
            lines.append(f"    last_capture = snapshot({label!r}, case_summary={summary!r})")
        elif kind == "wda_session_closed":
            continue

        wait_seconds = derive_wait_seconds(events, index)
        if wait_seconds:
            lines.append(f"    time.sleep({wait_seconds})")
    return lines


def render_script(
    case: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    assert_texts: list[str],
    skill_script: str,
) -> str:
    case_dir = Path(case["caseDir"])
    title = case.get("title") or case["caseId"]
    prompt = case.get("prompt") or ""
    sequence_lines = render_sequence(case_dir, events)
    indented_sequence_lines = ["        " + line[4:] if line.startswith("    ") else "        " + line for line in sequence_lines]
    assert_lines: list[str] = []
    if assert_texts:
        assert_lines.append("        assert last_capture is not None, 'No final capture was recorded during replay'")
        assert_lines.append("        assert_xml_contains(last_capture['xmlOut'], EXPECTED_TEXTS)")
    else:
        assert_lines.append("        # Add EXPECTED_TEXTS to verify the final screen if you want strict assertions.")
    return f"""#!/usr/bin/env python3
\"\"\"Replay case: {title}.\"\"\"

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


DEFAULT_UDID = {case.get("udid")!r}
DEFAULT_BUNDLE_ID = {case.get("bundleId")!r}
DEFAULT_CASE_DIR = {str(case_dir)!r}
DEFAULT_SKILL_SCRIPT = {skill_script!r}
EXPECTED_TEXTS = {assert_texts!r}


def run_cmd(*parts: str) -> dict:
    completed = subprocess.run(
        [sys.executable, skill_script, *parts],
        check=True,
        capture_output=True,
        text=True,
    )
    stdout = completed.stdout.strip()
    return json.loads(stdout) if stdout else {{}}


def common_case_args() -> list[str]:
    return ["--case-dir", str(case_dir)]


def status(*, case_summary: str) -> dict:
    return run_cmd("status", *common_case_args(), "--udid", udid, "--probe-http")


def launch_app(*, case_summary: str) -> dict:
    return run_cmd(
        "launch-app",
        *common_case_args(),
        "--udid",
        udid,
        "--bundle-id",
        bundle_id,
        "--case-summary",
        case_summary,
    )


def open_session(*, case_summary: str) -> dict:
    return run_cmd(
        "open-session",
        *common_case_args(),
        "--udid",
        udid,
        "--bundle-id",
        bundle_id,
        "--case-summary",
        case_summary,
    )


def close_session(*, case_summary: str) -> dict:
    return run_cmd(
        "close-session",
        *common_case_args(),
        "--case-summary",
        case_summary,
    )


def tap(x: int, y: int, *, case_summary: str) -> dict:
    return run_cmd(
        "tap",
        *common_case_args(),
        "--x",
        str(x),
        "--y",
        str(y),
        "--case-summary",
        case_summary,
    )


def swipe(x1: int, y1: int, x2: int, y2: int, *, case_summary: str) -> dict:
    return run_cmd(
        "swipe",
        *common_case_args(),
        "--x1",
        str(x1),
        "--y1",
        str(y1),
        "--x2",
        str(x2),
        "--y2",
        str(y2),
        "--case-summary",
        case_summary,
    )


def type_text(text: str, *, case_summary: str) -> dict:
    return run_cmd(
        "type-text",
        *common_case_args(),
        "--text",
        text,
        "--case-summary",
        case_summary,
    )


def snapshot(label: str, *, case_summary: str) -> dict:
    return run_cmd(
        "snapshot",
        *common_case_args(),
        "--label",
        label,
        "--case-summary",
        case_summary,
    )


def assert_xml_contains(xml_path: str, expected_texts: list[str]) -> None:
    xml_text = Path(xml_path).read_text(encoding="utf-8")
    missing = [text for text in expected_texts if text not in xml_text]
    if missing:
        raise AssertionError(f"Missing expected UI texts: {{missing}}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay one auto-uitest case through ios_wda.py.")
    parser.add_argument("--udid", default=DEFAULT_UDID, help="Target device UDID.")
    parser.add_argument("--bundle-id", default=DEFAULT_BUNDLE_ID, help="Target app bundle id.")
    parser.add_argument("--case-dir", default=DEFAULT_CASE_DIR, help="Artifact case directory to append replay evidence to.")
    parser.add_argument("--skill-script", default=DEFAULT_SKILL_SCRIPT, help="Absolute path to the auto-uitest ios_wda.py helper.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    udid = args.udid
    bundle_id = args.bundle_id
    case_dir = Path(args.case_dir).expanduser().resolve()
    skill_script = str(Path(args.skill_script).expanduser().resolve())
    if not udid or not bundle_id:
        raise SystemExit("Both --udid and --bundle-id are required")

    last_capture = None
    try:
        # Original prompt: {prompt!r}
{chr(10).join(indented_sequence_lines)}
{chr(10).join(assert_lines)}
    finally:
        try:
            close_session(case_summary="Closed replay WDA session")
        except subprocess.CalledProcessError:
            pass
"""


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a reusable replay script from one auto-uitest testcase.")
    parser.add_argument("--case-dir", required=True, help="Existing testcase directory.")
    parser.add_argument("--output", help="Destination path. Defaults to <case-dir>/generated/replay_<slug>.py")
    parser.add_argument("--assert-text", action="append", default=[], help="Expected text that must exist in the final XML.")
    parser.add_argument(
        "--skill-script",
        default=str((Path.home() / ".codex" / "skills" / "auto-uitest" / "scripts" / "ios_wda.py").resolve()),
        help="Absolute path to the installed ios_wda.py helper used by the replay script.",
    )
    args = parser.parse_args()

    case = load_case(args.case_dir)
    case_dir = Path(case["caseDir"])
    events = read_timeline(case_dir)
    output_path = Path(args.output).expanduser().resolve() if args.output else case_dir / "generated" / f"replay_{slugify(case['title'])}.py"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    script_text = render_script(case, events, assert_texts=args.assert_text, skill_script=args.skill_script)
    output_path.write_text(script_text, encoding="utf-8")
    output_path.chmod(0o755)
    print(
        json.dumps(
            {
                "caseDir": str(case_dir),
                "output": str(output_path),
                "assertTexts": args.assert_text,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
