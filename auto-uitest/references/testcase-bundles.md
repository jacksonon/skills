# Testcase Bundles

Persist every conversation as one reusable testcase bundle under the target project:

```text
<project-root>/
  .auto-uitest/
    cases/
      index.json
      <case-id>/
        case.json
        prompt.txt
        timeline.jsonl
        captures/
        generated/
        logs/
        notes/
        report/
        raw/
        session/
```

## Why this shape

- The bundle lives inside the target project, so the case travels with the codebase instead of the skill repo.
- `case.json` is the stable metadata entry point.
- `timeline.jsonl` is append-only and easy to audit or diff.
- `captures/` holds PNG/XML evidence per explored screen.
- `generated/` holds the final reusable test script.
- `notes/summary.md` holds the condensed replay instructions and known caveats.
- `report/index.html` is the human-readable case playback page with timeline, screenshots, and script validation status.
- `index.json` lets later turns load a case by id or title without scanning the whole tree.

## Required commands

### Start a case

```bash
python3 scripts/testcase_artifacts.py init \
  --project-root /path/to/target-project \
  --title "Login happy path" \
  --prompt "Open the app and automate the phone login happy path" \
  --bundle-id com.example.app
```

### Automatic capture mode

```bash
python3 scripts/ios_wda.py open-session \
  --case-dir /path/to/target-project/.auto-uitest/cases/<case-id> \
  --udid <UDID> \
  --bundle-id com.example.app
```

```bash
python3 scripts/ios_wda.py snapshot \
  --case-dir /path/to/target-project/.auto-uitest/cases/<case-id> \
  --label "login-home"
```

When `--case-dir` is present, `ios_wda.py` now defaults to:

- `session/wda-session.json` for the WDA session file
- `captures/<step>-<label>.xml` and `captures/<step>-<label>.png` for snapshots
- automatic `timeline.jsonl` entries for `open-session`, `snapshot`, `tap`, `swipe`, `type-text`, and `close-session`

### Manual path reservation

If a custom wrapper still wants explicit filenames, keep using:

```bash
python3 scripts/testcase_artifacts.py paths \
  --case-dir /path/to/target-project/.auto-uitest/cases/<case-id> \
  --label "login-home"
```

### Log significant steps

```bash
python3 scripts/testcase_artifacts.py log \
  --case-dir /path/to/case \
  --kind "bundle_resolved" \
  --summary "Resolved target bundle id" \
  --data-json '{"bundleId":"com.example.app"}'
```

### Finalize the reusable case

```bash
python3 scripts/render_case_script.py \
  --case-dir /path/to/case \
  --assert-text "Success marker"
```

```bash
python3 scripts/testcase_artifacts.py finalize \
  --case-dir /path/to/case \
  --status success \
  --script /path/to/case/generated/replay_login_happy_path.py \
  --summary "Validated on iPhone 13 mini. Uses accessibility ids for login fields."
```

Recommended sequence:

1. Validate the last screen with XML plus screenshot.
2. Run `render_case_script.py` to export the replay script into `generated/`.
3. Finalize the case and point `--script` at the exported file.
4. Open `report/index.html` for a visual walkthrough of the execution.

You can also refresh the report explicitly:

```bash
python3 scripts/testcase_artifacts.py report \
  --case-dir /path/to/case
```

## Reuse model

- `list` shows all known cases for a project.
- `show --selector <case-id>` loads one exact case.
- `show --selector <title substring>` loads a case if the title match is unique.
- A later turn can start from the case bundle instead of rediscovering the flow:
  - read `case.json`
  - read `notes/summary.md`
  - review `timeline.jsonl`
  - inspect the latest files in `captures/`
  - patch or rerun the script in `generated/`
