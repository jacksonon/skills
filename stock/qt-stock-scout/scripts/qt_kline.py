#!/usr/bin/env python3
"""
Fetch Tencent daily K-line (historical bars) for China A-shares.

This complements qt_quote.py (realtime snapshot). The endpoint used here is
Tencent's "fqkline" API which commonly returns (adjusted) OHLCV series.

Examples:
  python3 qt-stock-scout/scripts/qt_kline.py sz000625 --days 15
  python3 qt-stock-scout/scripts/qt_kline.py 000625 --days 30 --json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import qt_quote


_DEFAULT_ENDPOINTS = [
    # Historically stable for many environments.
    "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    # Some networks redirect/require https; keep as fallback.
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
]


@dataclass
class Bar:
    trading_date: str  # YYYY-MM-DD
    open: Optional[float]
    close: Optional[float]
    high: Optional[float]
    low: Optional[float]
    volume: Optional[int]
    amount: Optional[float]  # optional (CNY)


def _to_int(x: Any) -> Optional[int]:
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    s = str(x).strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _normalize_date_str(s: str) -> str:
    # Common formats: YYYY-MM-DD or YYYYMMDD.
    s = s.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return s


def _iter_series_keys(obj: Dict[str, Any]) -> List[str]:
    # Tencent can return different keys depending on adjust settings.
    preferred = [
        "qfqday",
        "day",
        "hfqday",
        "qfq",
        "hfq",
    ]
    keys = [k for k in preferred if k in obj]
    if keys:
        return keys
    # Fall back to anything list-like.
    return [k for k, v in obj.items() if isinstance(v, list)]


def parse_fqkline_json(payload: Dict[str, Any], symbol: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Returns (bars, series_key). Bars are normalized dicts.
    """
    data = payload.get("data") or {}
    sym_obj = data.get(symbol) or data.get(symbol.lower()) or {}
    if not isinstance(sym_obj, dict) or not sym_obj:
        return ([], None)

    series_key: Optional[str] = None
    series: Optional[List[Any]] = None
    for k in _iter_series_keys(sym_obj):
        v = sym_obj.get(k)
        if isinstance(v, list) and v:
            series_key = k
            series = v
            break
    if not series:
        return ([], series_key)

    out: List[Dict[str, Any]] = []
    for row in series:
        if not isinstance(row, list) or len(row) < 6:
            continue
        d = _normalize_date_str(str(row[0]))
        o = _to_float(row[1])
        c = _to_float(row[2])
        h = _to_float(row[3])
        l = _to_float(row[4])
        vol = _to_int(row[5])
        amt = _to_float(row[6]) if len(row) >= 7 else None
        out.append(
            {
                "date": d,
                "open": o,
                "close": c,
                "high": h,
                "low": l,
                "volume": vol,
                "amount": amt,
            }
        )
    return (out, series_key)


def fetch_fqkline_json(
    symbol: str,
    days: int = 15,
    adjust: str = "qfq",
    timeout: float = 10.0,
    endpoints: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Fetch raw JSON from Tencent fqkline.

    We request a date window to be explicit (works well across variants).
    Note: "days" here is the requested count, not guaranteed trading days.
    """
    endpoints = endpoints or list(_DEFAULT_ENDPOINTS)
    today = date.today()
    # Over-fetch calendar days to increase chances of having enough trading days.
    start = today - timedelta(days=max(days * 3, 45))
    start_s = start.strftime("%Y-%m-%d")
    end_s = today.strftime("%Y-%m-%d")

    # Tencent expects: param=sym,day,start,end,count,adjust
    param = f"{symbol},day,{start_s},{end_s},{days},{adjust}"
    qs = urllib.parse.urlencode({"param": param})

    last_err: Optional[Exception] = None
    for base in endpoints:
        url = f"{base}?{qs}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
            # Response is UTF-8 JSON in most cases.
            return json.loads(raw.decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001
            last_err = e
            continue
    raise RuntimeError(f"failed to fetch fqkline for {symbol}: {last_err}")


def fetch_bars(symbol: str, days: int = 15, adjust: str = "qfq", timeout: float = 10.0) -> Dict[str, Any]:
    sym = qt_quote.normalize_symbol(symbol)
    payload = fetch_fqkline_json(sym, days=days, adjust=adjust, timeout=timeout)
    bars, series_key = parse_fqkline_json(payload, sym)
    return {
        "symbol": sym,
        "period": "day",
        "adjust": adjust,
        "series_key": series_key,
        "bars": bars,
    }


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("symbol", help="e.g. sz000625/sh600519/000625")
    p.add_argument("--days", type=int, default=15, help="requested number of bars (trading days)")
    p.add_argument("--adjust", default="qfq", help="qfq/hfq/none (depends on Tencent backend)")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    obj = fetch_bars(args.symbol, days=max(args.days, 0), adjust=args.adjust, timeout=args.timeout)
    if args.json:
        json.dump(obj, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    bars = obj.get("bars") or []
    sys.stdout.write(f"{obj.get('symbol')} {obj.get('period')} adjust={obj.get('adjust')} bars={len(bars)}\n")
    # Print last ~5 bars for quick eyeballing.
    tail = bars[-5:] if len(bars) > 5 else bars
    for b in tail:
        sys.stdout.write(
            f"{b.get('date','-')}  o={b.get('open','-')} c={b.get('close','-')} h={b.get('high','-')} l={b.get('low','-')} v={b.get('volume','-')}\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
