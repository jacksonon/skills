#!/usr/bin/env python3
"""
Normalize a JIRA browse URL or raw issue key into machine-friendly fields.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import urlparse, unquote


ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]+-\d+$")
BROWSE_RE = re.compile(r"/browse/([A-Za-z][A-Za-z0-9]+-\d+)(?:[/?#]|$)")
SEARCH_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9]+-\d+)\b")


def extract_issue_key(raw_ref: str) -> str:
    ref = raw_ref.strip()
    if ISSUE_KEY_RE.fullmatch(ref):
        return ref.upper()

    parsed = urlparse(ref)
    decoded_path = unquote(parsed.path or "")
    browse_match = BROWSE_RE.search(decoded_path)
    if browse_match:
        return browse_match.group(1).upper()

    haystack = " ".join(
        part for part in [decoded_path, unquote(parsed.query or ""), unquote(parsed.fragment or "")] if part
    )
    candidates = sorted({match.upper() for match in SEARCH_RE.findall(haystack)})
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise ValueError(f"multiple issue keys found in input: {', '.join(candidates)}")
    raise ValueError("no JIRA issue key found in input")


def normalize_ref(raw_ref: str) -> dict[str, str]:
    ref = raw_ref.strip()
    issue_key = extract_issue_key(ref)

    if "://" not in ref:
        return {
            "input_ref": ref,
            "issue_key": issue_key,
            "input_kind": "issue_key",
        }

    parsed = urlparse(ref)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("expected a full http(s) JIRA URL or a raw issue key")

    base_url = f"{parsed.scheme}://{parsed.netloc}"
    return {
        "input_ref": ref,
        "input_kind": "url",
        "scheme": parsed.scheme,
        "host": parsed.netloc,
        "issue_key": issue_key,
        "browse_url": f"{base_url}/browse/{issue_key}",
        "api_v2_url": f"{base_url}/rest/api/2/issue/{issue_key}",
        "api_v3_url": f"{base_url}/rest/api/3/issue/{issue_key}",
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Parse a JIRA issue reference into normalized fields."
    )
    parser.add_argument(
        "jira_ref",
        help="JIRA browse URL or raw issue key, for example https://jira.example.com/browse/ABC-123 or ABC-123",
    )
    args = parser.parse_args()

    try:
        payload = normalize_ref(args.jira_ref)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    json.dump(payload, sys.stdout, ensure_ascii=True, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
