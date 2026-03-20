# WDA Setup Notes

Use this reference when the task needs real-device bootstrapping details rather than test-authoring guidance.

## Real device prerequisites

- Keep the phone paired and trusted with the host Mac.
- Require Developer Mode to be enabled on the device.
- Expect real-device WDA signing to need a valid Apple Developer team and provisioning profile.
- Never bundle a user's `.mobileprovision` inside the skill itself. If a profile is needed, the user must explicitly provide the profile name or file path for the current environment.
- Prefer automatic signing unless the environment already carries a known-good manual signing setup.
- Treat a device with `ddiServicesAvailable=false` or no install/launch capabilities as not ready for automation yet.

## Preferred startup order

1. Prefer a prebuilt WDA bundle when one already exists for the target signing/team setup.
2. Fall back to building WDA from source only when no reusable prebuilt artifact is available.
3. Keep `iproxy` running while driving the device so local `http://127.0.0.1:8100` stays bound to device port `8100`.
4. Probe `GET /status` before creating any session.

## Mapping to Appium guidance

- Appium's real-device setup guidance centers on signing correctness, trusted host/device pairing, and a launchable `WebDriverAgentRunner`.
- Appium's prebuilt-WDA guidance centers on reusing a precompiled `.xctestrun` bundle from a `bootstrapPath` instead of rebuilding on every run.
- In Appium capability terms, the reusable/prebuilt route maps to concepts such as `useXctestrunFile` and `bootstrapPath`.
- In this skill, the equivalent host-side commands are:
  - `scripts/ios_wda.py start-prebuilt ...`
  - `scripts/ios_wda.py start-source ...`

## Practical command patterns

### Probe device + WDA install state

```bash
python3 scripts/device_inventory.py devices --json
python3 scripts/ios_wda.py status --udid <UDID> --probe-http
```

### Start a prebuilt WDA

```bash
python3 scripts/ios_wda.py start-prebuilt \
  --udid <UDID> \
  --bootstrap-path /path/to/prebuilt-wda \
  --pid-file .tmp/wda-iproxy.pid \
  --log-file .tmp/wda-iproxy.log \
  --runner-pid-file .tmp/wda-runner.pid \
  --runner-log-file .tmp/wda-runner.log
```

### Build and run WDA from source

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

## Failure routing

- If `status` shows no matching WDA app, install/start WDA before trying to inspect UI.
- If the app is installed but `/status` is unreachable, check `iproxy`, trust pairing, and the runner log.
- If `xcodebuild` fails during source mode, fix signing first instead of papering over it in the skill.
- If the task environment lacks `appium`, continue with direct WDA + `devicectl`; do not block on installing the whole Appium stack unless the final generated test actually needs it.

## Upstream references

- https://appium.github.io/appium-xcuitest-driver/5.12/real-device-config/
- https://appium.github.io/appium-xcuitest-driver/5.12/run-prebuilt-wda/
