#!/usr/bin/env python3
"""Extract UI-test-relevant hints from iOS source and interface files."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from testcase_artifacts import append_timeline, load_case, now_iso, update_case


def uniq(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def humanize(identifier: str) -> str:
    value = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", identifier)
    value = value.replace("_", " ").replace("-", " ").strip()
    return re.sub(r"\s+", " ", value).strip().lower()


def scan_objc_or_swift(path: Path, text: str) -> dict[str, Any]:
    classes = re.findall(r"@interface\s+([A-Za-z_][A-Za-z0-9_]*)", text)
    classes += re.findall(r"@implementation\s+([A-Za-z_][A-Za-z0-9_]*)", text)
    classes += re.findall(r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\b", text)

    outlets = re.findall(
        r"(?:@property\s*\([^)]+\)\s*)?IBOutlet\s+[A-Za-z_][A-Za-z0-9_<>\s]*\*+\s*([A-Za-z_][A-Za-z0-9_]*)\s*;",
        text,
    )
    outlets += re.findall(r"@IBOutlet[^\n]*\bvar\s+([A-Za-z_][A-Za-z0-9_]*)", text)

    actions = re.findall(r"IBAction\s*\)\s*([A-Za-z_][A-Za-z0-9_]*)", text)
    actions += re.findall(r"@IBAction[^\n]*\bfunc\s+([A-Za-z_][A-Za-z0-9_]*)", text)

    accessibility_ids = re.findall(r'accessibilityIdentifier\s*=\s*@"([^"]+)"', text)
    accessibility_ids += re.findall(r'accessibilityIdentifier\s*=\s*"([^"]+)"', text)
    accessibility_ids += re.findall(r'forKey:@"accessibilityIdentifier"\s*object:@"([^"]+)"', text)

    localized_keys = re.findall(r'NSLocalizedString\s*\(\s*@"([^"]+)"', text)
    localized_keys += re.findall(r'NSLocalizedString\s*\(\s*"([^"]+)"', text)

    ui_texts = re.findall(r'(?:setTitle|setText|setPlaceholder):@"([^"]+)"', text)
    ui_texts += re.findall(r'\b(?:text|title|placeholder|message)\s*=\s*@"([^"]+)"', text)
    ui_texts += re.findall(r'\b(?:text|title|placeholder|message)\s*=\s*"([^"]+)"', text)

    navigation = []
    if "pushViewController" in text:
        navigation.append("pushViewController")
    if "presentViewController" in text or ".present(" in text:
        navigation.append("presentViewController")
    if "popViewController" in text or "dismissViewController" in text:
        navigation.append("dismiss")
    if "tableView" in text:
        navigation.append("tableView")
    if "collectionView" in text:
        navigation.append("collectionView")

    return {
        "path": str(path),
        "kind": path.suffix.lstrip(".") or "source",
        "classes": uniq(classes),
        "outlets": uniq(outlets),
        "actions": uniq(actions),
        "accessibilityIds": uniq(accessibility_ids),
        "localizedKeys": uniq(localized_keys),
        "uiTexts": uniq(ui_texts),
        "navigationSignals": uniq(navigation),
    }


def scan_interface_xml(path: Path, text: str) -> dict[str, Any]:
    classes: list[str] = []
    accessibility_ids: list[str] = []
    ui_texts: list[str] = []
    placeholders: list[str] = []
    reuse_ids: list[str] = []
    custom_keys: dict[str, list[str]] = defaultdict(list)

    root = ET.fromstring(text)
    for elem in root.iter():
        for attr in ("customClass", "customModuleProvider", "sceneMemberID"):
            if elem.get(attr):
                custom_keys[attr].append(elem.get(attr, ""))
        if elem.get("customClass"):
            classes.append(elem.get("customClass", ""))
        for attr in ("accessibilityIdentifier", "reuseIdentifier", "placeholder", "text", "title", "label"):
            value = elem.get(attr)
            if not value:
                continue
            if attr == "accessibilityIdentifier":
                accessibility_ids.append(value)
            elif attr == "reuseIdentifier":
                reuse_ids.append(value)
            elif attr == "placeholder":
                placeholders.append(value)
            else:
                ui_texts.append(value)

    navigation = []
    if root.findall(".//segue"):
        navigation.append("segue")
    if root.findall(".//tableViewCell"):
        navigation.append("tableView")
    if root.findall(".//collectionViewCell"):
        navigation.append("collectionView")

    return {
        "path": str(path),
        "kind": path.suffix.lstrip(".") or "interface",
        "classes": uniq(classes),
        "outlets": [],
        "actions": [],
        "accessibilityIds": uniq(accessibility_ids),
        "localizedKeys": [],
        "uiTexts": uniq(ui_texts),
        "placeholders": uniq(placeholders),
        "reuseIdentifiers": uniq(reuse_ids),
        "navigationSignals": uniq(navigation),
        "xmlAttributes": {key: uniq(values) for key, values in custom_keys.items() if values},
    }


def scan_path(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.suffix.lower() in {".xib", ".storyboard"}:
        return scan_interface_xml(path, text)
    return scan_objc_or_swift(path, text)


def suggest_test_ideas(results: list[dict[str, Any]]) -> list[str]:
    ideas: list[str] = []
    all_actions: list[str] = []
    all_accessibility: list[str] = []
    all_classes: list[str] = []
    all_texts: list[str] = []
    all_navigation: list[str] = []

    for result in results:
        all_actions.extend(result.get("actions", []))
        all_accessibility.extend(result.get("accessibilityIds", []))
        all_classes.extend(result.get("classes", []))
        all_texts.extend(result.get("uiTexts", []))
        all_navigation.extend(result.get("navigationSignals", []))

    for action in uniq(all_actions):
        ideas.append(f"Exercise action '{action}' by locating the related control and verifying its outcome.")
    for identifier in uniq(all_accessibility)[:6]:
        ideas.append(f"Prefer accessibility id '{identifier}' as a stable selector in the generated UI test.")
    for text in uniq(all_texts)[:4]:
        ideas.append(f"Verify visible text '{text}' after the relevant interaction or state change.")
    if "tableView" in all_navigation:
        ideas.append("Add at least one scrolling or cell-selection assertion because the source references a table view.")
    if "collectionView" in all_navigation:
        ideas.append("Add at least one collection view item selection or visibility assertion.")
    if "segue" in all_navigation or "pushViewController" in all_navigation or "presentViewController" in all_navigation:
        ideas.append("Validate the navigation transition triggered by the screen, not just the tap itself.")
    for cls in uniq(all_classes)[:3]:
        ideas.append(f"Use screen class '{cls}' as a naming hint for the testcase title and final script.")
    return uniq(ideas)


def render_markdown(summary: dict[str, Any]) -> str:
    lines = ["# Source Hints", ""]
    lines.append(f"- Files scanned: {len(summary['files'])}")
    if summary["classes"]:
        lines.append(f"- Classes: {', '.join(summary['classes'])}")
    if summary["accessibilityIds"]:
        lines.append(f"- Accessibility ids: {', '.join(summary['accessibilityIds'])}")
    if summary["actions"]:
        lines.append(f"- Actions: {', '.join(summary['actions'])}")
    if summary["outlets"]:
        lines.append(f"- Outlets: {', '.join(summary['outlets'])}")
    if summary["localizedKeys"]:
        lines.append(f"- Localized keys: {', '.join(summary['localizedKeys'])}")
    if summary["uiTexts"]:
        lines.append(f"- UI texts: {', '.join(summary['uiTexts'][:10])}")
    if summary["navigationSignals"]:
        lines.append(f"- Navigation signals: {', '.join(summary['navigationSignals'])}")
    lines.append("")
    lines.append("## Suggested Test Ideas")
    for idea in summary["suggestedTestIdeas"]:
        lines.append(f"- {idea}")
    lines.append("")
    lines.append("## Per File")
    for file_result in summary["files"]:
        lines.append(f"- {file_result['path']}")
        for key in ("classes", "actions", "outlets", "accessibilityIds", "uiTexts", "localizedKeys", "navigationSignals"):
            values = file_result.get(key) or []
            if values:
                lines.append(f"  - {key}: {', '.join(values[:12])}")
    lines.append("")
    return "\n".join(lines)


def summarize(paths: list[Path]) -> dict[str, Any]:
    file_results = [scan_path(path) for path in paths]
    summary = {
        "files": file_results,
        "classes": uniq([item for result in file_results for item in result.get("classes", [])]),
        "actions": uniq([item for result in file_results for item in result.get("actions", [])]),
        "outlets": uniq([item for result in file_results for item in result.get("outlets", [])]),
        "accessibilityIds": uniq([item for result in file_results for item in result.get("accessibilityIds", [])]),
        "localizedKeys": uniq([item for result in file_results for item in result.get("localizedKeys", [])]),
        "uiTexts": uniq([item for result in file_results for item in result.get("uiTexts", [])]),
        "navigationSignals": uniq([item for result in file_results for item in result.get("navigationSignals", [])]),
    }
    summary["suggestedTestIdeas"] = suggest_test_ideas(file_results)
    return summary


def persist_case_outputs(case_dir: Path, summary: dict[str, Any], paths: list[Path], markdown: str) -> dict[str, str]:
    raw_dir = case_dir / "raw" / "source"
    raw_dir.mkdir(parents=True, exist_ok=True)
    copied_files = []
    for path in paths:
        target = raw_dir / path.name
        shutil.copy2(path, target)
        copied_files.append(str(target))

    hints_json = case_dir / "notes" / "source-hints.json"
    hints_md = case_dir / "notes" / "source-hints.md"
    hints_json.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    hints_md.write_text(markdown, encoding="utf-8")

    metadata = load_case(case_dir)
    metadata["sourceHints"] = str(hints_json)
    metadata["updatedAt"] = now_iso()
    update_case(Path(metadata["projectRoot"]), metadata)
    append_timeline(
        case_dir,
        {
            "ts": now_iso(),
            "step": sum(1 for _ in (case_dir / "timeline.jsonl").open("r", encoding="utf-8")) + 1,
            "kind": "source_hints_extracted",
            "summary": "Extracted UI-test hints from iOS source files",
            "data": {
                "files": [str(path) for path in paths],
                "copiedFiles": copied_files,
                "hintsJson": str(hints_json),
                "hintsMarkdown": str(hints_md),
            },
        },
    )
    return {"hintsJson": str(hints_json), "hintsMarkdown": str(hints_md)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract UI automation hints from iOS source/interface files.")
    parser.add_argument("paths", nargs="+", help="Source/interface files: .m .mm .h .swift .xib .storyboard")
    parser.add_argument("--case-dir", help="Optional testcase directory to attach source files and hints.")
    parser.add_argument("--markdown-out", help="Optional markdown output path.")
    parser.add_argument("--json-out", help="Optional JSON output path.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    paths = [Path(raw).expanduser().resolve() for raw in args.paths]
    for path in paths:
        if not path.exists():
            raise SystemExit(f"Path does not exist: {path}")
        if path.suffix.lower() not in {".m", ".mm", ".h", ".swift", ".xib", ".storyboard"}:
            raise SystemExit(f"Unsupported file type for {path}")

    summary = summarize(paths)
    markdown = render_markdown(summary)

    if args.json_out:
        Path(args.json_out).expanduser().resolve().write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    if args.markdown_out:
        Path(args.markdown_out).expanduser().resolve().write_text(markdown, encoding="utf-8")
    if args.case_dir:
        persist_case_outputs(Path(args.case_dir).expanduser().resolve(), summary, paths, markdown)

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
