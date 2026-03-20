# Source-Driven Test Generation

Use this reference when the user provides iOS implementation or interface files and wants a matching UI testcase.

Supported inputs:

- `.m`
- `.mm`
- `.h`
- `.swift`
- `.xib`
- `.storyboard`

## Intent

The source file does not replace real-device validation. It gives the skill a better starting point:

- class and screen naming hints
- `IBAction` and `IBOutlet` names
- accessibility identifiers
- visible text or localization keys
- navigation hints such as push/present/segue/table/collection usage

## Command

```bash
python3 scripts/ios_source_hints.py /path/to/xxViewController.m --case-dir <case-dir>
```

For mixed source + interface input:

```bash
python3 scripts/ios_source_hints.py \
  /path/to/xxViewController.m \
  /path/to/xxViewController.xib \
  --case-dir <case-dir>
```

## What gets written back to the case

- `raw/source/` contains copied source artifacts
- `notes/source-hints.json` contains structured extraction output
- `notes/source-hints.md` contains a readable summary
- `timeline.jsonl` gets a `source_hints_extracted` event

## How to use the hints

1. Use the extracted class and action names to choose a testcase title.
2. Use extracted accessibility ids as preferred selectors in the final UI test.
3. Use extracted text/localization keys to define assertions.
4. Use navigation hints to decide which transitions must be validated on-device.
5. Still run the real-device exploration loop to confirm runtime hierarchy and behavior.

## Rule of thumb

- Source-only output is a draft testcase.
- Source + on-device XML/screenshot evidence is a validated testcase.
