#!/usr/bin/env python3
"""Persist one conversation as a reusable testcase inside a target project."""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def slugify(value: str, *, fallback: str = "case") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized[:48] or fallback


def case_root(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / ".auto-uitest" / "cases"


def ensure_case_dirs(root: Path) -> None:
    for name in ("captures", "generated", "logs", "notes", "raw", "report", "session"):
        (root / name).mkdir(parents=True, exist_ok=True)


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def append_timeline(case_dir: Path, event: dict[str, Any]) -> None:
    timeline = case_dir / "timeline.jsonl"
    with timeline.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def load_timeline(case_dir: Path) -> list[dict[str, Any]]:
    timeline = case_dir / "timeline.jsonl"
    if not timeline.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in timeline.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


def relpath_from_report(target: str | Path, case_dir: Path) -> str:
    report_dir = case_dir / "report"
    return str(Path(target).resolve().relative_to(case_dir.resolve()) if Path(target).is_absolute() and str(Path(target).resolve()).startswith(str(case_dir.resolve())) else Path(target))


def validate_generated_script(script_path: str | None) -> dict[str, Any]:
    if not script_path:
        return {"exists": False, "checked": False, "ok": False, "message": "No generated script recorded"}
    path = Path(script_path).expanduser().resolve()
    if not path.exists():
        return {"exists": False, "checked": False, "ok": False, "message": f"Missing script: {path}"}
    payload = {
        "exists": True,
        "checked": False,
        "ok": True,
        "message": "Script file exists",
        "path": str(path),
    }
    if path.suffix == ".py":
        completed = subprocess.run(
            [sys.executable, "-m", "py_compile", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        payload["checked"] = True
        payload["ok"] = completed.returncode == 0
        payload["message"] = "Python syntax check passed" if completed.returncode == 0 else (completed.stderr.strip() or "Python syntax check failed")
    return payload


def render_case_report(case_dir: Path, metadata: dict[str, Any]) -> Path:
    ensure_case_dirs(case_dir)
    report_dir = case_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "index.html"
    timeline = load_timeline(case_dir)
    summary_text = ""
    summary_path = case_dir / "notes" / "summary.md"
    if summary_path.exists():
        summary_text = summary_path.read_text(encoding="utf-8").strip()
    script_check = validate_generated_script(metadata.get("generatedScript"))
    step_cards: list[str] = []
    for event in timeline:
        data = event.get("data") or {}
        screenshot = data.get("screenshotOut")
        xml_path = data.get("xmlOut")
        links: list[str] = []
        if screenshot:
            shot_rel = "../" + str(Path(screenshot).resolve().relative_to(case_dir.resolve()))
            links.append(f'<a href="{html.escape(shot_rel)}">screenshot</a>')
        if xml_path:
            xml_rel = "../" + str(Path(xml_path).resolve().relative_to(case_dir.resolve()))
            links.append(f'<a href="{html.escape(xml_rel)}">xml</a>')
        data_json = html.escape(json.dumps(data, ensure_ascii=False, indent=2))
        image_html = ""
        if screenshot:
            image_html = f'<img loading="lazy" src="{html.escape(shot_rel)}" alt="step {event.get("step")} screenshot">'
        links_html = " | ".join(links) if links else "no attachments"
        step_cards.append(
            f"""
            <section class="step-card">
              <div class="step-head">
                <span class="step-no">Step {event.get("step")}</span>
                <span class="step-kind">{html.escape(str(event.get("kind") or ""))}</span>
                <span class="step-ts">{html.escape(str(event.get("ts") or ""))}</span>
              </div>
              <h3>{html.escape(str(event.get("summary") or ""))}</h3>
              <div class="step-body">
                <div class="step-shot">{image_html}</div>
                <div class="step-meta">
                  <div class="step-links">{links_html}</div>
                  <pre>{data_json}</pre>
                </div>
              </div>
            </section>
            """
        )
    generated_script_rel = None
    if metadata.get("generatedScript"):
        generated_script_rel = "../" + str(Path(metadata["generatedScript"]).resolve().relative_to(case_dir.resolve()))
    status_class = f"status-{metadata.get('status', 'unknown')}"
    html_text = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(metadata.get("title") or metadata.get("caseId") or "auto-uitest case")}</title>
  <style>
    :root {{
      --bg: #f4efe6;
      --panel: #fffdf8;
      --ink: #1c1b19;
      --muted: #5b564f;
      --line: #d9d0c3;
      --accent: #b85c38;
      --ok: #2f7d4a;
      --warn: #a36a00;
      --bad: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "SF Pro Display", "PingFang SC", "Helvetica Neue", sans-serif;
      background: linear-gradient(180deg, #f4efe6 0%, #efe7d9 100%);
      color: var(--ink);
    }}
    .wrap {{ max-width: 1280px; margin: 0 auto; padding: 32px 24px 80px; }}
    .hero, .panel {{
      background: rgba(255, 253, 248, 0.9);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: 0 20px 60px rgba(50, 35, 10, 0.08);
      backdrop-filter: blur(8px);
    }}
    .hero {{ padding: 28px; margin-bottom: 20px; }}
    h1 {{ margin: 0 0 12px; font-size: 34px; line-height: 1.1; }}
    .sub {{ color: var(--muted); font-size: 15px; }}
    .badges {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 18px; }}
    .badge {{
      display: inline-flex; align-items: center; padding: 8px 12px; border-radius: 999px;
      background: #efe4d4; color: #6a432b; font-size: 13px; font-weight: 600;
    }}
    .status-success {{ background: #dff3e4; color: var(--ok); }}
    .status-partial {{ background: #fff1cc; color: var(--warn); }}
    .status-blocked {{ background: #fee4e2; color: var(--bad); }}
    .grid {{ display: grid; grid-template-columns: 1.1fr 0.9fr; gap: 20px; margin-bottom: 20px; }}
    .panel {{ padding: 22px; }}
    .kv {{ display: grid; grid-template-columns: 140px 1fr; gap: 8px 16px; font-size: 14px; }}
    .kv div:nth-child(odd) {{ color: var(--muted); }}
    .summary {{
      white-space: pre-wrap; font-size: 14px; line-height: 1.7; background: #faf5ed;
      border: 1px solid var(--line); border-radius: 18px; padding: 16px;
    }}
    .steps {{ display: grid; gap: 16px; }}
    .step-card {{
      background: rgba(255, 253, 248, 0.92);
      border: 1px solid var(--line);
      border-radius: 22px;
      padding: 18px;
    }}
    .step-head {{
      display: flex; gap: 10px; flex-wrap: wrap; color: var(--muted); font-size: 12px; margin-bottom: 10px;
      text-transform: uppercase; letter-spacing: 0.04em;
    }}
    .step-body {{ display: grid; grid-template-columns: minmax(280px, 520px) 1fr; gap: 16px; align-items: start; }}
    .step-shot img {{ width: 100%; border-radius: 16px; border: 1px solid var(--line); display: block; }}
    .step-meta pre {{
      margin: 10px 0 0; padding: 14px; background: #1f1d1a; color: #f7f3eb; border-radius: 14px;
      overflow: auto; font-size: 12px; line-height: 1.5;
    }}
    .step-links a {{ color: var(--accent); text-decoration: none; font-weight: 600; }}
    .step-links a:hover {{ text-decoration: underline; }}
    .cta {{
      display: inline-flex; align-items: center; gap: 8px; margin-top: 12px; padding: 10px 14px;
      border-radius: 999px; border: 1px solid var(--line); background: #fff7ee; color: var(--ink);
      text-decoration: none; font-weight: 600;
    }}
    @media (max-width: 960px) {{
      .grid, .step-body {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <section class="hero">
      <h1>{html.escape(metadata.get("title") or metadata.get("caseId") or "")}</h1>
      <div class="sub">{html.escape(metadata.get("prompt") or "")}</div>
      <div class="badges">
        <span class="badge {status_class}">status: {html.escape(metadata.get("status") or "unknown")}</span>
        <span class="badge">device: {html.escape(metadata.get("udid") or "n/a")}</span>
        <span class="badge">bundle: {html.escape(metadata.get("bundleId") or "n/a")}</span>
        <span class="badge">steps: {len(timeline)}</span>
      </div>
    </section>

    <div class="grid">
      <section class="panel">
        <h2>Overview</h2>
        <div class="kv">
          <div>Case ID</div><div>{html.escape(metadata.get("caseId") or "")}</div>
          <div>Project Root</div><div>{html.escape(metadata.get("projectRoot") or "")}</div>
          <div>Created</div><div>{html.escape(metadata.get("createdAt") or "")}</div>
          <div>Updated</div><div>{html.escape(metadata.get("updatedAt") or "")}</div>
        </div>
        {f'<a class="cta" href="{html.escape(generated_script_rel)}">Open generated script</a>' if generated_script_rel else ''}
      </section>
      <section class="panel">
        <h2>Execution Result</h2>
        <div class="kv">
          <div>Generated Script</div><div>{html.escape(script_check.get("path") or "n/a")}</div>
          <div>Script Valid</div><div>{'yes' if script_check.get('ok') else 'no'}</div>
          <div>Validation</div><div>{html.escape(script_check.get("message") or "")}</div>
        </div>
      </section>
    </div>

    <section class="panel" style="margin-bottom: 20px;">
      <h2>Summary</h2>
      <div class="summary">{html.escape(summary_text or "No summary yet.")}</div>
    </section>

    <section class="steps">
      {''.join(step_cards)}
    </section>
  </div>
</body>
</html>
"""
    report_path.write_text(html_text, encoding="utf-8")
    return report_path


def create_index_entry(project_root: Path, metadata: dict[str, Any]) -> None:
    index_path = case_root(project_root) / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    if index_path.exists():
        index = read_json(index_path)
    else:
        index = {"cases": []}
    cases = [item for item in index.get("cases", []) if item.get("caseId") != metadata["caseId"]]
    cases.append(
        {
            "caseId": metadata["caseId"],
            "title": metadata["title"],
            "bundleId": metadata.get("bundleId"),
            "appName": metadata.get("appName"),
            "status": metadata.get("status", "in_progress"),
            "createdAt": metadata["createdAt"],
            "updatedAt": metadata["updatedAt"],
            "caseDir": metadata["caseDir"],
        }
    )
    cases.sort(key=lambda item: (item.get("updatedAt") or "", item.get("caseId") or ""), reverse=True)
    write_json(index_path, {"cases": cases})


def update_case(project_root: Path, metadata: dict[str, Any]) -> None:
    write_json(Path(metadata["caseDir"]) / "case.json", metadata)
    create_index_entry(project_root, metadata)


def load_case(case_dir: str | Path) -> dict[str, Any]:
    path = Path(case_dir).expanduser().resolve()
    case_json = path / "case.json"
    if not case_json.exists():
        raise SystemExit(f"Missing case.json under {path}")
    return read_json(case_json)


def resolve_case(project_root: str | Path, selector: str) -> dict[str, Any]:
    root = case_root(project_root)
    direct = root / selector
    if (direct / "case.json").exists():
        return load_case(direct)
    index_path = root / "index.json"
    if not index_path.exists():
        raise SystemExit(f"No case index under {root}")
    index = read_json(index_path)
    matches = []
    for item in index.get("cases", []):
        if item.get("caseId") == selector:
            return load_case(item["caseDir"])
        if selector.lower() in (item.get("title") or "").lower():
            matches.append(item)
    if len(matches) == 1:
        return load_case(matches[0]["caseDir"])
    if matches:
        raise SystemExit(
            "Ambiguous case selector. Matches: "
            + ", ".join(f"{item['caseId']} ({item['title']})" for item in matches[:8])
        )
    raise SystemExit(f"Case not found: {selector}")


def next_step_number(case_dir: Path) -> int:
    timeline = case_dir / "timeline.jsonl"
    if not timeline.exists():
        return 1
    count = sum(1 for _ in timeline.open("r", encoding="utf-8"))
    return count + 1


def cmd_init(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root).expanduser().resolve()
    root = case_root(project_root)
    root.mkdir(parents=True, exist_ok=True)
    prefix = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = slugify(args.title)
    case_id = args.case_id or f"{prefix}-{slug}"
    case_dir = root / case_id
    if case_dir.exists():
        raise SystemExit(f"Case already exists: {case_dir}")
    ensure_case_dirs(case_dir)
    prompt_path = case_dir / "prompt.txt"
    prompt_path.write_text(args.prompt.strip() + "\n", encoding="utf-8")
    metadata = {
        "caseId": case_id,
        "title": args.title,
        "prompt": args.prompt.strip(),
        "projectRoot": str(project_root),
        "caseDir": str(case_dir),
        "bundleId": args.bundle_id,
        "appName": args.app_name,
        "udid": args.udid,
        "status": "in_progress",
        "createdAt": now_iso(),
        "updatedAt": now_iso(),
        "tags": args.tag or [],
    }
    update_case(project_root, metadata)
    append_timeline(
        case_dir,
        {
            "ts": now_iso(),
            "step": 1,
            "kind": "case_initialized",
            "summary": "Created testcase bundle",
            "data": {
                "bundleId": args.bundle_id,
                "appName": args.app_name,
                "udid": args.udid,
            },
        },
    )
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


def cmd_log(args: argparse.Namespace) -> int:
    metadata = load_case(args.case_dir)
    case_dir = Path(metadata["caseDir"])
    data: dict[str, Any] = {}
    if args.data_json:
        data.update(json.loads(args.data_json))
    if args.path:
        data["path"] = str(Path(args.path).expanduser().resolve())
    event = {
        "ts": now_iso(),
        "step": next_step_number(case_dir),
        "kind": args.kind,
        "summary": args.summary,
        "data": data,
    }
    append_timeline(case_dir, event)
    metadata["updatedAt"] = now_iso()
    update_case(Path(metadata["projectRoot"]), metadata)
    print(json.dumps(event, indent=2, sort_keys=True))
    return 0


def cmd_paths(args: argparse.Namespace) -> int:
    metadata = load_case(args.case_dir)
    case_dir = Path(metadata["caseDir"])
    step = next_step_number(case_dir)
    slug = slugify(args.label, fallback=f"step-{step:03d}")
    prefix = f"{step:03d}-{slug}"
    payload = {
        "caseDir": str(case_dir),
        "step": step,
        "label": args.label,
        "xmlOut": str(case_dir / "captures" / f"{prefix}.xml"),
        "screenshotOut": str(case_dir / "captures" / f"{prefix}.png"),
        "noteOut": str(case_dir / "notes" / f"{prefix}.md"),
        "logOut": str(case_dir / "logs" / f"{prefix}.log"),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def cmd_attach(args: argparse.Namespace) -> int:
    metadata = load_case(args.case_dir)
    case_dir = Path(metadata["caseDir"])
    source = Path(args.source).expanduser().resolve()
    if not source.exists():
        raise SystemExit(f"Source does not exist: {source}")
    subdir = args.subdir
    target_dir = case_dir / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target_name = args.name or source.name
    target = target_dir / target_name
    shutil.copy2(source, target)
    event = {
        "ts": now_iso(),
        "step": next_step_number(case_dir),
        "kind": args.kind or "file_attached",
        "summary": args.summary or f"Attached {source.name}",
        "data": {
            "source": str(source),
            "target": str(target),
            "subdir": subdir,
        },
    }
    append_timeline(case_dir, event)
    metadata["updatedAt"] = now_iso()
    update_case(Path(metadata["projectRoot"]), metadata)
    print(json.dumps({"target": str(target), "event": event}, indent=2, sort_keys=True))
    return 0


def cmd_finalize(args: argparse.Namespace) -> int:
    metadata = load_case(args.case_dir)
    case_dir = Path(metadata["caseDir"])
    if args.script:
        source = Path(args.script).expanduser().resolve()
        if not source.exists():
            raise SystemExit(f"Script does not exist: {source}")
        target_name = args.script_name or source.name
        target = case_dir / "generated" / target_name
        if source != target:
            shutil.copy2(source, target)
        metadata["generatedScript"] = str(target)
    if args.summary:
        summary_path = case_dir / "notes" / "summary.md"
        summary_path.write_text(args.summary.strip() + "\n", encoding="utf-8")
        metadata["summaryPath"] = str(summary_path)
    metadata["status"] = args.status
    report_path = render_case_report(case_dir, metadata)
    metadata["reportPath"] = str(report_path)
    metadata["updatedAt"] = now_iso()
    update_case(Path(metadata["projectRoot"]), metadata)
    event = {
        "ts": now_iso(),
        "step": next_step_number(case_dir),
        "kind": "case_finalized",
        "summary": f"Case finalized with status={args.status}",
        "data": {
            "status": args.status,
            "generatedScript": metadata.get("generatedScript"),
        },
    }
    append_timeline(case_dir, event)
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    metadata = load_case(args.case_dir)
    case_dir = Path(metadata["caseDir"])
    report_path = render_case_report(case_dir, metadata)
    metadata["reportPath"] = str(report_path)
    metadata["updatedAt"] = now_iso()
    update_case(Path(metadata["projectRoot"]), metadata)
    print(json.dumps({"caseDir": str(case_dir), "reportPath": str(report_path)}, indent=2, sort_keys=True))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    root = case_root(args.project_root)
    index_path = root / "index.json"
    if not index_path.exists():
        print(json.dumps({"cases": []}, indent=2, sort_keys=True))
        return 0
    index = read_json(index_path)
    cases = index.get("cases", [])
    if args.limit:
        cases = cases[: args.limit]
    print(json.dumps({"cases": cases}, indent=2, sort_keys=True))
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    metadata = resolve_case(args.project_root, args.selector)
    print(json.dumps(metadata, indent=2, sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage reusable auto-uitest testcase bundles inside a target project.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Create a new testcase bundle under <project>/.auto-uitest/cases.")
    init_parser.add_argument("--project-root", required=True, help="Target project root.")
    init_parser.add_argument("--title", required=True, help="Human-readable testcase title.")
    init_parser.add_argument("--prompt", required=True, help="Original user prompt or distilled case goal.")
    init_parser.add_argument("--case-id", help="Optional stable case id. Defaults to timestamp-title slug.")
    init_parser.add_argument("--bundle-id", help="Target app bundle identifier.")
    init_parser.add_argument("--app-name", help="Target app display name.")
    init_parser.add_argument("--udid", help="Selected device UDID.")
    init_parser.add_argument("--tag", action="append", help="Optional tag. Repeatable.")
    init_parser.set_defaults(func=cmd_init)

    log_parser = subparsers.add_parser("log", help="Append one step to a testcase timeline.")
    log_parser.add_argument("--case-dir", required=True, help="Case directory path.")
    log_parser.add_argument("--kind", required=True, help="Machine-readable event kind.")
    log_parser.add_argument("--summary", required=True, help="Human-readable one-line summary.")
    log_parser.add_argument("--data-json", help="Optional JSON object payload.")
    log_parser.add_argument("--path", help="Optional filesystem path to include in the event.")
    log_parser.set_defaults(func=cmd_log)

    paths_parser = subparsers.add_parser("paths", help="Return the next canonical artifact paths for a step.")
    paths_parser.add_argument("--case-dir", required=True, help="Case directory path.")
    paths_parser.add_argument("--label", required=True, help="Step label used in filenames.")
    paths_parser.set_defaults(func=cmd_paths)

    attach_parser = subparsers.add_parser("attach", help="Copy an output file into a testcase bundle and log it.")
    attach_parser.add_argument("--case-dir", required=True, help="Case directory path.")
    attach_parser.add_argument("--source", required=True, help="Existing file to copy into the testcase.")
    attach_parser.add_argument("--subdir", default="attachments", choices=["attachments", "captures", "generated", "logs", "notes", "raw", "session"], help="Destination subdirectory inside the case.")
    attach_parser.add_argument("--name", help="Optional destination filename.")
    attach_parser.add_argument("--kind", help="Optional event kind. Defaults to file_attached.")
    attach_parser.add_argument("--summary", help="Optional event summary.")
    attach_parser.set_defaults(func=cmd_attach)

    finalize_parser = subparsers.add_parser("finalize", help="Mark a testcase as reusable, attach its final summary/script, and render a case report page.")
    finalize_parser.add_argument("--case-dir", required=True, help="Case directory path.")
    finalize_parser.add_argument("--status", required=True, choices=["success", "partial", "blocked"], help="Final testcase status.")
    finalize_parser.add_argument("--summary", help="Final markdown summary stored in notes/summary.md.")
    finalize_parser.add_argument("--script", help="Path to the final generated test script to copy into generated/.")
    finalize_parser.add_argument("--script-name", help="Optional destination filename for the copied script.")
    finalize_parser.set_defaults(func=cmd_finalize)

    report_parser = subparsers.add_parser("report", help="Render or refresh the HTML report page for one testcase.")
    report_parser.add_argument("--case-dir", required=True, help="Case directory path.")
    report_parser.set_defaults(func=cmd_report)

    list_parser = subparsers.add_parser("list", help="List known cases for a target project.")
    list_parser.add_argument("--project-root", required=True, help="Target project root.")
    list_parser.add_argument("--limit", type=int, default=20, help="Maximum number of cases to print.")
    list_parser.set_defaults(func=cmd_list)

    show_parser = subparsers.add_parser("show", help="Load one case by case id or title substring.")
    show_parser.add_argument("--project-root", required=True, help="Target project root.")
    show_parser.add_argument("--selector", required=True, help="Case id or unique title substring.")
    show_parser.set_defaults(func=cmd_show)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
