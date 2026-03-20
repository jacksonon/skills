---
name: auto-uitest
description: Drive a real iPhone or iPad through WebDriverAgent, including device discovery, device selection, installed-app lookup, WDA install/startup, UI tree capture, screenshot-assisted exploration, source-file hint extraction from iOS UI implementation files, and drafting iOS UI automation scripts. Use when Codex needs to explore a physical iOS device, pick from multiple connected phones, resolve a missing bundle identifier, bootstrap WDA from a prebuilt or source checkout, turn an observed flow into an Appium/XCUITest-style test script, or generate a UI testcase starting from iOS files such as ViewController.m, .swift, .xib, or storyboard files.
---

# Auto Uitest

## Overview

Use this skill to turn a vague “帮我在真机上探索并写 UI 自动化” request into a repeatable host-side workflow. It provides a fixed decision tree plus helper scripts for device/app discovery, testcase persistence inside the target project, and direct WDA control, so Codex does not have to rediscover the real-device plumbing every turn.

## Workflow

1. Create or load a testcase bundle under the target project.
2. Extract source-driven UI hints when the user provides iOS files.
3. Resolve the target device.
4. Resolve the target bundle identifier.
5. Ensure WDA is installed and reachable.
6. Explore the app with XML plus screenshots.
7. Export a reusable replay script into the testcase bundle, render a visual report page, and finalize the case.

## 1. Create Or Load A Testcase

Every conversation must map to one testcase bundle under the target project, not under the skill repo.

Default location:

```text
<target-project>/.auto-uitest/cases/<case-id>/
```

Start a new case:

```bash
python3 scripts/testcase_artifacts.py init \
  --project-root /path/to/target-project \
  --title "<short case title>" \
  --prompt "<original user prompt>" \
  --bundle-id <optional bundle id>
```

Reuse an older case:

```bash
python3 scripts/testcase_artifacts.py list --project-root /path/to/target-project
python3 scripts/testcase_artifacts.py show --project-root /path/to/target-project --selector "<case-id-or-title>"
```

Apply these rules:

- If the user asks to continue or reuse a known flow, load the existing case first.
- If multiple cases match a loose title, ask the user which one to reuse.
- If this is a new flow, create a fresh case before touching the device.
- Read [references/testcase-bundles.md](references/testcase-bundles.md) when you need the case directory schema or command patterns.

## 2. Extract Source-Driven Hints

If the user gives you `xxViewController.m`, `xxViewController.swift`, `.xib`, or `.storyboard` files, extract static hints before touching the device:

```bash
python3 scripts/ios_source_hints.py /path/to/xxViewController.m --case-dir <case-dir>
```

For paired implementation + interface files:

```bash
python3 scripts/ios_source_hints.py \
  /path/to/xxViewController.m \
  /path/to/xxViewController.xib \
  --case-dir <case-dir>
```

Apply these rules:

- Treat source-derived selectors and flows as hypotheses, not runtime truth.
- Prefer extracted accessibility ids and visible text when drafting the first test version.
- Use extracted `IBAction` names to guess primary interactions that deserve a testcase.
- If source hints are the only input, be explicit that the result is draft-quality until validated on-device.
- Read [references/source-driven-tests.md](references/source-driven-tests.md) when the request starts from source files.

## 3. Resolve Device

Run:

```bash
python3 scripts/device_inventory.py devices --json
```

Apply these rules:

- If exactly one `selectable=true` device is returned, use it without asking.
- If multiple selectable devices are returned, ask the user to choose one. Prefer `request_user_input` over a free-form multiple choice message.
- If no selectable device is returned, stop and tell the user what is missing: trust pairing, Developer Mode, DDI, cable/tunnel, or host permissions.
- Ignore paired-but-unusable devices unless the user explicitly wants to debug connection state. Use `--include-disconnected` only for diagnosis.

Log important decisions back to the testcase:

```bash
python3 scripts/testcase_artifacts.py log \
  --case-dir <case-dir> \
  --kind "device_selected" \
  --summary "Selected iPhone 13 mini for this case" \
  --data-json '{"udid":"<UDID>"}'
```

## 4. Resolve Bundle Identifier

If the user already gave a bundle id, trust it unless the app lookup proves it is absent.

If the prompt does not contain a bundle id, run:

```bash
python3 scripts/device_inventory.py apps --udid <UDID> --match "<app name guess>"
```

Apply these rules:

- If the user named the app clearly and the lookup returns one obvious match, use that bundle id.
- If the lookup is ambiguous, show the candidate bundle ids and ask the user to choose.
- If the user gave only a product concept and not an app name, ask for the bundle id or the installed app name before exploring.

## 5. Ensure WDA

Inspect the device first:

```bash
python3 scripts/ios_wda.py status --udid <UDID> --probe-http
```

Then branch:

- If `wdaInstalled=true` and `/status` is reachable, proceed.
- If WDA is installed but `/status` is not reachable, start or repair the runner first. Usually this means `iproxy` is missing, the runner is not active, or the device trust state changed.
- If WDA is not installed, ask the user which bootstrap path is available:
  - Preferred: a prebuilt `.xctestrun` bundle or `bootstrapPath`.
  - Fallback: a local `WebDriverAgent` source checkout plus a valid signing team.
- If source signing needs a provisioning profile, require the user to explicitly provide it. Do not assume the skill bundle carries a reusable `.mobileprovision` file.
- Do not pretend WDA can be installed without signing context. If neither a prebuilt bundle nor a signable source checkout exists, stop and ask for one.

Use these commands:

```bash
python3 scripts/ios_wda.py start-prebuilt \
  --udid <UDID> \
  --bootstrap-path /path/to/prebuilt-wda \
  --pid-file .tmp/wda-iproxy.pid \
  --log-file .tmp/wda-iproxy.log \
  --runner-pid-file .tmp/wda-runner.pid \
  --runner-log-file .tmp/wda-runner.log
```

```bash
python3 scripts/ios_wda.py start-source \
  --udid <UDID> \
  --wda-repo /path/to/WebDriverAgent \
  --team-id <TEAM_ID> \
  --provisioning-profile-specifier "<user-provided profile name if needed>" \
  --allow-provisioning-updates \
  --pid-file .tmp/wda-iproxy.pid \
  --log-file .tmp/wda-iproxy.log \
  --runner-pid-file .tmp/wda-runner.pid \
  --runner-log-file .tmp/wda-runner.log
```

Read [references/wda-setup.md](references/wda-setup.md) when you need the real-device signing or prebuilt-WDA rationale.

Log the chosen bootstrap path and attach any runner logs into the testcase with `testcase_artifacts.py attach`.

## 6. Explore The App

Launch the app and create a session:

```bash
python3 scripts/ios_wda.py launch-app --udid <UDID> --bundle-id <BUNDLE_ID>
python3 scripts/ios_wda.py open-session --udid <UDID> --session-file .tmp/wda-session.json
```

Prefer the automatic case-aware mode:

```bash
python3 scripts/ios_wda.py open-session --case-dir <case-dir> --udid <UDID> --bundle-id <BUNDLE_ID>
python3 scripts/ios_wda.py snapshot --case-dir <case-dir> --label "login-home"
```

This automatically uses `<case-dir>/session/wda-session.json`, writes captures under `<case-dir>/captures/`, and appends timeline events.

If an external wrapper needs explicit filenames, you can still reserve them first:

```bash
python3 scripts/testcase_artifacts.py paths --case-dir <case-dir> --label "login-home"
```

Apply these rules:

- Prefer the XML tree first. It exposes stable accessibility ids, labels, names, values, and hierarchy.
- If the XML is incomplete or misleading, inspect the screenshot with `view_image` or another image-capable tool before guessing.
- Use `tap`, `swipe`, and `type-text` only to explore the real flow. Do not ship coordinate-only logic as the final test unless the app truly exposes no stable accessibility hooks.
- Keep a terse action log while exploring. The final test should reflect the observed flow, not a reconstructed narrative.
- After each important capture or action cluster, append one timeline event with `testcase_artifacts.py log`.

Common exploration commands:

```bash
python3 scripts/ios_wda.py tap --session-file .tmp/wda-session.json --x 180 --y 640
python3 scripts/ios_wda.py swipe --session-file .tmp/wda-session.json --x1 200 --y1 700 --x2 200 --y2 250
python3 scripts/ios_wda.py type-text --session-file .tmp/wda-session.json --text "hello"
```

Close the session when done:

```bash
python3 scripts/ios_wda.py close-session --session-file .tmp/wda-session.json --delete-session-file
```

## 7. Export The Final Test

Read [references/test-authoring.md](references/test-authoring.md) before writing the final script.

Apply these rules:

- If the flow was explored through `ios_wda.py`, first export a replay script from the timeline:

```bash
python3 scripts/render_case_script.py \
  --case-dir <case-dir> \
  --assert-text "<final success text>"
```

- By default this writes a reusable Python replay script into `<case-dir>/generated/`.
- `testcase_artifacts.py finalize` now also renders `<case-dir>/report/index.html`, which shows the timeline, screenshots, and whether the generated script passed an execution check.
- If the target repo already has its own UI automation stack, you can still replace or adapt the generated replay script afterward.
- When final evidence is ready, mark the case reusable:

```bash
python3 scripts/testcase_artifacts.py finalize \
  --case-dir <case-dir> \
  --status success \
  --script <case-dir>/generated/<rendered-script>.py \
  --summary "Validated on device and exported replay script."
```

- If the final result is `partial` or `blocked`, do not stop at the report alone. Summarize the failure from the evidence, then ask the user whether Codex should patch the target project and rerun the same case until it passes.
- When the user agrees to remediation and the target project is available locally, use the case evidence (`report/index.html`, `captures/`, `timeline.jsonl`, generated script) to drive the fix -> rerun -> verify loop.
- Promote stable selectors from the XML tree into the final test.
- If source hints exist, merge them with runtime XML instead of choosing one source blindly.
- Downgrade coordinate taps to comments or temporary TODOs unless they are the only workable interaction.
- Keep the setup notes in the test or adjacent documentation: device assumptions, bundle id, and WDA assumptions.
- Copy the final script into `<case-dir>/generated/` even if the target repo also stores it elsewhere.

## 8. Validate And Finalize

- Re-run the explored flow or at least the key checkpoints after authoring the script.
- Report exactly which parts were validated on-device and which parts were only inferred from XML or screenshots.
- Report when any selector or assertion came only from source-file analysis.
- If WDA bootstrap was the blocker, report that explicitly instead of claiming the UI test script is validated.
- Finalize the case so later turns can reuse it directly:

```bash
python3 scripts/testcase_artifacts.py finalize \
  --case-dir <case-dir> \
  --status success \
  --script /path/to/final_test.py \
  --summary "<validated scope, selector notes, and known caveats>"
```
