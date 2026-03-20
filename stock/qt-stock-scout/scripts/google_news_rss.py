#!/usr/bin/env python3
"""
Fetch latest headlines via Google News RSS (no API key required).

Example:
  python3 qt-stock-scout/scripts/google_news_rss.py '长安汽车 000625' --limit 10
  python3 qt-stock-scout/scripts/google_news_rss.py 'sz000625 长安汽车' --json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional


def fetch_rss(query: str, timeout: float = 10.0, hl: str = "zh-CN", gl: str = "CN", ceid: str = "CN:zh-Hans") -> bytes:
    q = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _text(el: Optional[ET.Element]) -> Optional[str]:
    if el is None or el.text is None:
        return None
    t = el.text.strip()
    return t or None


def parse_rss(xml_bytes: bytes) -> Dict[str, Any]:
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        raise ValueError("invalid RSS: missing channel")

    title = _text(channel.find("title"))
    items: List[Dict[str, Any]] = []
    for item in channel.findall("item"):
        source_el = item.find("source")
        source = _text(source_el)
        source_url = source_el.get("url") if source_el is not None else None

        items.append(
            {
                "title": _text(item.find("title")),
                "link": _text(item.find("link")),
                "pubDate": _text(item.find("pubDate")),
                "source": source,
                "source_url": source_url,
            }
        )

    return {"feed_title": title, "items": items}


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("query", help="news query, e.g. '长安汽车 000625'")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--json", action="store_true")
    p.add_argument("--hl", default="zh-CN")
    p.add_argument("--gl", default="CN")
    p.add_argument("--ceid", default="CN:zh-Hans")
    args = p.parse_args(argv)

    xml_bytes = fetch_rss(args.query, timeout=args.timeout, hl=args.hl, gl=args.gl, ceid=args.ceid)
    parsed = parse_rss(xml_bytes)

    items = parsed["items"][: max(args.limit, 0)]
    if args.json:
        json.dump({"query": args.query, "feed_title": parsed["feed_title"], "items": items}, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    if parsed.get("feed_title"):
        sys.stdout.write(f"{parsed['feed_title']}\n\n")
    for it in items:
        t = it.get("title") or "-"
        src = it.get("source") or ""
        pd = it.get("pubDate") or ""
        link = it.get("link") or ""
        sys.stdout.write(f"- {t}\n")
        if src or pd:
            sys.stdout.write(f"  {src}  {pd}\n")
        if link:
            sys.stdout.write(f"  {link}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

