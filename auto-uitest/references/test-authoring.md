# Test Authoring Notes

Use this reference after the device and WDA are already working.

## Exploration loop

1. Launch the target app with `scripts/ios_wda.py launch-app`.
2. Create a session with `scripts/ios_wda.py open-session`.
3. Capture both XML and screenshot with `scripts/ios_wda.py snapshot`.
4. Prefer XML accessibility data first; use screenshot analysis only when labels, hierarchy, or visual state are missing from XML.
5. Keep a short action log while exploring so the final test script reflects the real path, not a reconstructed guess.

## Selector strategy

- Prefer accessibility id / name / label / value from the XML tree.
- Prefer predicates or class chain selectors over coordinates when writing the final test.
- Use coordinate taps only for temporary exploration or when the app exposes no stable accessibility hooks.
- Record unstable areas explicitly in comments so follow-up product work can add accessibility identifiers.

## Default output format

- Match the target repo's existing automation stack if it already uses Appium/WebdriverIO/Python.
- If there is no existing stack, default to a minimal Appium Python script because it is readable and easy to patch.
- When the flow was discovered through `ios_wda.py` exploration, prefer exporting a replay script immediately after validation so the same conversation becomes a reusable case without a second manual pass.
- After finalization, use the generated report page as the first handoff artifact because it combines the action timeline, screenshots, and script validation in one place.

## Replay export

After the final screen is validated, render a reusable replay script from the case timeline:

```bash
python3 scripts/render_case_script.py \
  --case-dir /path/to/case \
  --assert-text "用户中心" \
  --assert-text "其它设置"
```

Use this pattern:

- Pass one or more `--assert-text` values whenever you know the final success markers.
- The generated script lands in `generated/` by default and reuses `ios_wda.py`, so it can replay the flow without re-authoring every step.
- If a `type_text` step was not safely persisted in the timeline, the generator infers it from the next capture when possible; otherwise it leaves a `TODO` placeholder in the script.

## Minimal Python skeleton

```python
from appium import webdriver
from appium.options.ios import XCUITestOptions

options = XCUITestOptions()
options.platform_name = "iOS"
options.automation_name = "XCUITest"
options.udid = "<UDID>"
options.bundle_id = "<BUNDLE_ID>"
options.use_preinstalled_wda = True

driver = webdriver.Remote("http://127.0.0.1:4723", options=options)
try:
    # TODO: replace with stable selectors captured during exploration
    pass
finally:
    driver.quit()
```

## What to preserve in the final script

- The chosen device assumptions: physical device vs simulator, iOS version, and target bundle id.
- The WDA assumptions: prebuilt vs source-built, expected local server URL, and any custom WDA bundle id.
- The discovered selector evidence: where each selector came from in XML or screenshot analysis.
- Clear setup/teardown so another agent can rerun the flow without re-reading the entire investigation.
