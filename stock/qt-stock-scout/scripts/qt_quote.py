#!/usr/bin/env python3
"""
Fetch and parse Tencent qt.gtimg.cn realtime quote strings.

Example:
  python3 qt-stock-scout/scripts/qt_quote.py sz000625
  python3 qt-stock-scout/scripts/qt_quote.py 000625 --json
  python3 qt-stock-scout/scripts/qt_quote.py sh600519 sz000625 --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


_LINE_RE = re.compile(r'^v_(?P<sym>[a-z0-9]+)=\"(?P<body>.*)\";?$')


def _guess_market(code6: str) -> str:
    # Heuristic for A-share tickers when user provides only 6 digits.
    if code6.startswith("6"):
        return "sh"
    if code6.startswith(("0", "3")):
        return "sz"
    if code6.startswith(("4", "8")):
        return "bj"
    return "sz"


def normalize_symbol(s: str) -> str:
    s = s.strip()
    if not s:
        raise ValueError("empty symbol")
    s = s.lower()
    if re.fullmatch(r"\d{6}", s):
        return f"{_guess_market(s)}{s}"
    if re.fullmatch(r"(sh|sz|bj)\d{6}", s):
        return s
    raise ValueError(f"unsupported symbol format: {s!r} (use sz000625/sh600519 or 6 digits)")


def fetch_quotes(symbols: List[str], timeout: float = 10.0) -> str:
    q = ",".join(symbols)
    url = f"http://qt.gtimg.cn/q={q}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    # qt.gtimg.cn is typically GBK.
    return raw.decode("gbk", errors="replace")


def _to_int(x: str) -> Optional[int]:
    x = x.strip()
    if not x:
        return None
    try:
        return int(float(x))
    except ValueError:
        return None


def _to_float(x: str) -> Optional[float]:
    x = x.strip()
    if not x:
        return None
    try:
        return float(x)
    except ValueError:
        return None


@dataclass
class Level:
    price: Optional[float]
    vol: Optional[int]


def parse_line(line: str) -> Tuple[str, Dict[str, Any]]:
    line = line.strip()
    m = _LINE_RE.match(line)
    if not m:
        raise ValueError(f"unrecognized line: {line!r}")
    sym = m.group("sym")
    fields = m.group("body").split("~")

    def safe(idx: int) -> str:
        return fields[idx] if idx < len(fields) else ""

    buy = [
        Level(_to_float(safe(9)), _to_int(safe(10))),
        Level(_to_float(safe(11)), _to_int(safe(12))),
        Level(_to_float(safe(13)), _to_int(safe(14))),
        Level(_to_float(safe(15)), _to_int(safe(16))),
        Level(_to_float(safe(17)), _to_int(safe(18))),
    ]
    sell = [
        Level(_to_float(safe(19)), _to_int(safe(20))),
        Level(_to_float(safe(21)), _to_int(safe(22))),
        Level(_to_float(safe(23)), _to_int(safe(24))),
        Level(_to_float(safe(25)), _to_int(safe(26))),
        Level(_to_float(safe(27)), _to_int(safe(28))),
    ]

    parsed: Dict[str, Any] = {
        "symbol": sym,
        "name": safe(1) or None,
        "code": safe(2) or None,
        "last": _to_float(safe(3)),
        "prev_close": _to_float(safe(4)),
        "open": _to_float(safe(5)),
        "volume": _to_int(safe(6)),
        "outer_volume": _to_int(safe(7)),
        "inner_volume": _to_int(safe(8)),
        "change": _to_float(safe(31)),
        "change_pct": _to_float(safe(32)),
        "high": _to_float(safe(33)),
        "low": _to_float(safe(34)),
        "timestamp": safe(30) or None,  # yyyymmddHHMMSS
        "buy": [lvl.__dict__ for lvl in buy],
        "sell": [lvl.__dict__ for lvl in sell],
    }

    # Helpful extras when present; keep them optional because field layouts can vary.
    parsed["turnover_pct"] = _to_float(safe(38))
    parsed["pe"] = _to_float(safe(39))
    parsed["amount_text"] = safe(35) or None
    parsed["amount_wan"] = _to_float(safe(37))

    parsed["_fields_len"] = len(fields)
    return sym, parsed


def parse_response(text: str) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        sym, parsed = parse_line(line)
        out[sym] = parsed
    return out


def format_human(q: Dict[str, Any]) -> str:
    name = q.get("name") or ""
    sym = q.get("symbol") or ""
    ts = q.get("timestamp") or ""
    last = q.get("last")
    chg = q.get("change")
    pct = q.get("change_pct")
    hi = q.get("high")
    lo = q.get("low")
    vol = q.get("volume")

    b1 = (q.get("buy") or [{}])[0]
    s1 = (q.get("sell") or [{}])[0]

    def fnum(x: Any) -> str:
        return "-" if x is None else str(x)

    return (
        f"{name} {sym} @ {ts}\n"
        f"last={fnum(last)}  change={fnum(chg)}  change_pct={fnum(pct)}\n"
        f"open={fnum(q.get('open'))}  prev_close={fnum(q.get('prev_close'))}  high={fnum(hi)}  low={fnum(lo)}\n"
        f"volume={fnum(vol)}  amount_wan={fnum(q.get('amount_wan'))}\n"
        f"bid1={fnum(b1.get('price'))} x {fnum(b1.get('vol'))}  "
        f"ask1={fnum(s1.get('price'))} x {fnum(s1.get('vol'))}"
    )


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("symbols", nargs="+", help="e.g. sz000625 / sh600519 / 000625")
    p.add_argument("--json", action="store_true", help="output JSON")
    p.add_argument("--raw", action="store_true", help="include raw text in JSON output")
    p.add_argument("--timeout", type=float, default=10.0)
    args = p.parse_args(argv)

    syms = [normalize_symbol(s) for s in args.symbols]
    text = fetch_quotes(syms, timeout=args.timeout)
    parsed = parse_response(text)

    if args.json:
        obj: Dict[str, Any] = {"quotes": parsed}
        if args.raw:
            obj["raw"] = text
        json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    # Human output: one block per symbol, preserve input order.
    for i, s in enumerate(syms):
        q = parsed.get(s)
        if not q:
            sys.stdout.write(f"{s}: no data\n")
        else:
            sys.stdout.write(format_human(q) + "\n")
        if i != len(syms) - 1:
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

