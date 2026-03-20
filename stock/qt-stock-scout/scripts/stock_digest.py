#!/usr/bin/env python3
"""
Combine Tencent realtime quote + Google News RSS headlines into one digest.

Example:
  python3 qt-stock-scout/scripts/stock_digest.py sz000625 --news-limit 10
  python3 qt-stock-scout/scripts/stock_digest.py 000625 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional

import google_news_rss
import qt_kline
import qt_quote

# Market context defaults (A-share indices). Used to infer broad risk appetite / style rotation.
# NOTE: We keep this lightweight and deterministic; this tool does not (and cannot) "guarantee returns".
DEFAULT_MARKET_SYMBOLS: List[str] = [
    "sh000001",  # 上证指数
    "sz399001",  # 深证成指
    "sz399006",  # 创业板指
    "sh000300",  # 沪深300
    "sh000905",  # 中证500
    "sh000852",  # 中证1000
    "sh000016",  # 上证50
    "sh000688",  # 科创50
]
DEFAULT_BENCHMARK_SYMBOL = "sh000300"


def _parse_symbol_csv(s: str) -> List[str]:
    s = (s or "").strip()
    if not s:
        return []
    out: List[str] = []
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(qt_quote.normalize_symbol(part))
    return out


def _safe_float(x: Any) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) else None


def _market_ret(market_kline_signals: Dict[str, Any], sym: str, window: str) -> Optional[float]:
    if not isinstance(market_kline_signals, dict):
        return None
    s = market_kline_signals.get(sym) or {}
    if not isinstance(s, dict):
        return None
    w = s.get(window) or {}
    if not isinstance(w, dict):
        return None
    return _safe_float(w.get("ret_n_pct"))


def _compute_rotation_signals(market_kline_signals: Dict[str, Any]) -> Dict[str, Any]:
    """
    Coarse "style rotation" inference using index relative performance.
    This is not sector-level rotation (needs a proper sector universe).
    """
    out: Dict[str, Any] = {}
    # Small vs large: CSI1000 vs CSI300.
    small = _market_ret(market_kline_signals, "sh000852", "w15")
    large = _market_ret(market_kline_signals, "sh000300", "w15")
    if isinstance(small, (int, float)) and isinstance(large, (int, float)):
        out["small_minus_large_15d_pct"] = float(small) - float(large)
        if out["small_minus_large_15d_pct"] >= 1.0:
            out["small_vs_large"] = "small_outperform"
        elif out["small_minus_large_15d_pct"] <= -1.0:
            out["small_vs_large"] = "large_outperform"
        else:
            out["small_vs_large"] = "mixed"

    # Growth vs value proxy: ChiNext vs SSE50.
    growth = _market_ret(market_kline_signals, "sz399006", "w15")
    value = _market_ret(market_kline_signals, "sh000016", "w15")
    if isinstance(growth, (int, float)) and isinstance(value, (int, float)):
        out["growth_minus_value_15d_pct"] = float(growth) - float(value)
        if out["growth_minus_value_15d_pct"] >= 1.0:
            out["growth_vs_value"] = "growth_outperform"
        elif out["growth_minus_value_15d_pct"] <= -1.0:
            out["growth_vs_value"] = "value_outperform"
        else:
            out["growth_vs_value"] = "mixed"

    return out


def fetch_market_context(
    symbols: List[str],
    kline_days: int,
    kline_adjust: str,
    timeout: float,
) -> Dict[str, Any]:
    """
    Fetch a lightweight market context: quotes + (optional) 7/15d kline signals.
    """
    syms = [qt_quote.normalize_symbol(s) for s in symbols]
    ctx: Dict[str, Any] = {
        "symbols": syms,
        "quotes": {},
        "kline_signals": {},
        "rotation_signals": {},
        "errors": {},
    }

    try:
        raw = qt_quote.fetch_quotes(syms, timeout=timeout)
        parsed = qt_quote.parse_response(raw)
    except Exception as e:  # noqa: BLE001
        ctx["error"] = str(e)
        return ctx

    for s in syms:
        q = parsed.get(s)
        if not q:
            ctx["errors"][s] = "no quote data"
            continue
        ctx["quotes"][s] = q

        if kline_days and kline_days > 0:
            try:
                kobj = qt_kline.fetch_bars(s, days=kline_days, adjust=kline_adjust, timeout=timeout)
                bars = kobj.get("bars") or []
                if bars:
                    ctx["kline_signals"][s] = {
                        "w7": compute_window_signals(bars, 7),
                        "w15": compute_window_signals(bars, 15),
                    }
            except Exception as e:  # noqa: BLE001
                ctx["errors"][s] = f"kline_error: {e}"

    ctx["rotation_signals"] = _compute_rotation_signals(ctx.get("kline_signals") or {})
    return ctx


def compute_signals(q: Dict[str, Any]) -> Dict[str, Any]:
    last = q.get("last")
    prev_close = q.get("prev_close")
    high = q.get("high")
    low = q.get("low")

    out: Dict[str, Any] = {}
    if isinstance(last, (int, float)) and isinstance(prev_close, (int, float)) and prev_close:
        out["pct_vs_prev_close"] = (last - prev_close) / prev_close * 100.0

    if isinstance(high, (int, float)) and isinstance(low, (int, float)) and isinstance(prev_close, (int, float)) and prev_close:
        out["range_pct_vs_prev_close"] = (high - low) / prev_close * 100.0

    if isinstance(last, (int, float)) and isinstance(high, (int, float)) and isinstance(low, (int, float)) and (high - low):
        out["pos_in_range_0_1"] = (last - low) / (high - low)

    # Order book imbalance: positive means buy side is heavier.
    buy = q.get("buy") or []
    sell = q.get("sell") or []
    buy_vol = sum((lvl.get("vol") or 0) for lvl in buy if isinstance(lvl, dict))
    sell_vol = sum((lvl.get("vol") or 0) for lvl in sell if isinstance(lvl, dict))
    denom = buy_vol + sell_vol
    if denom:
        out["orderbook_imbalance_-1_1"] = (buy_vol - sell_vol) / denom
        out["orderbook_buy_vol"] = buy_vol
        out["orderbook_sell_vol"] = sell_vol

    return out


def _mean(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(xs) / float(len(xs))


def _stdev(xs: List[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = _mean(xs)
    if m is None:
        return None
    var = sum((x - m) ** 2 for x in xs) / float(len(xs) - 1)
    return var**0.5


def _max_drawdown(closes: List[float]) -> Optional[float]:
    # Returns drawdown in percent (negative number), e.g. -6.2
    if len(closes) < 2:
        return None
    peak = closes[0]
    mdd = 0.0
    for c in closes[1:]:
        if c > peak:
            peak = c
            continue
        dd = (c - peak) / peak * 100.0
        if dd < mdd:
            mdd = dd
    return mdd


def compute_kline_signals(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Stage-level signals from recent daily bars.
    bars are expected to be sorted ascending by date.
    """
    closes: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    vols: List[float] = []
    for b in bars:
        c = b.get("close")
        h = b.get("high")
        l = b.get("low")
        v = b.get("volume")
        if isinstance(c, (int, float)):
            closes.append(float(c))
        if isinstance(h, (int, float)):
            highs.append(float(h))
        if isinstance(l, (int, float)):
            lows.append(float(l))
        if isinstance(v, (int, float)):
            vols.append(float(v))

    out: Dict[str, Any] = {"bars": len(bars)}
    if not closes:
        return out

    last_close = closes[-1]
    out["last_close"] = last_close
    out["high_n"] = max(highs) if highs else None
    out["low_n"] = min(lows) if lows else None

    if closes[0]:
        out["ret_n_pct"] = (last_close - closes[0]) / closes[0] * 100.0

    # Daily return stats (simple pct).
    rets: List[float] = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev:
            rets.append((closes[i] - prev) / prev * 100.0)
    out["daily_ret_stdev_pct"] = _stdev(rets)
    out["max_drawdown_pct"] = _max_drawdown(closes)

    # Moving averages and simple trend label.
    def ma(n: int) -> Optional[float]:
        if len(closes) < n:
            return None
        return _mean(closes[-n:])

    ma5 = ma(5)
    ma10 = ma(10)
    out["ma5"] = ma5
    out["ma10"] = ma10
    if isinstance(ma5, (int, float)) and isinstance(ma10, (int, float)):
        if last_close > ma5 and ma5 > ma10:
            out["trend_label"] = "up"
        elif last_close < ma5 and ma5 < ma10:
            out["trend_label"] = "down"
        else:
            out["trend_label"] = "range"

    # Range position (0..1) within recent highs/lows.
    hi = out.get("high_n")
    lo = out.get("low_n")
    if isinstance(hi, (int, float)) and isinstance(lo, (int, float)) and (hi - lo):
        out["pos_in_range_0_1"] = (last_close - lo) / (hi - lo)

    # Volume: last vs average.
    if vols:
        out["vol_avg"] = _mean(vols)
        out["vol_last"] = vols[-1]
        if out["vol_avg"]:
            out["vol_last_vs_avg"] = out["vol_last"] / out["vol_avg"]

    # Simple tags for easier narrative generation.
    tags: List[str] = []
    pir = out.get("pos_in_range_0_1")
    if isinstance(pir, (int, float)):
        if pir <= 0.2:
            tags.append("near_range_low")
        if pir >= 0.8:
            tags.append("near_range_high")
    vratio = out.get("vol_last_vs_avg")
    if isinstance(vratio, (int, float)) and vratio >= 1.3:
        tags.append("volume_spike")
    rn = out.get("ret_n_pct")
    if isinstance(rn, (int, float)):
        if rn <= -5.0:
            tags.append("notable_drawdown")
        if rn >= 5.0:
            tags.append("notable_rally")
    dv = out.get("daily_ret_stdev_pct")
    if isinstance(dv, (int, float)) and dv >= 1.0:
        tags.append("high_vol")
    out["tags"] = tags
    return out


def compute_window_signals(bars: List[Dict[str, Any]], window: int) -> Dict[str, Any]:
    if window <= 0 or len(bars) < window:
        return {"window": window, "bars": 0}
    win = bars[-window:]
    sig = compute_kline_signals(win)
    sig["window"] = window
    return sig


def _round_to_tick(x: float, tick: float = 0.01) -> float:
    if tick <= 0:
        return x
    return round(round(x / tick) * tick, 2)


def _fmt_ts_compact(ts: Any) -> str:
    """
    Tencent quote timestamp often looks like YYYYMMDDhhmmss.
    Return "YYYY-MM-DD HH:MM:SS" if parseable, otherwise str(ts).
    """
    s = str(ts or "").strip()
    if len(s) == 14 and s.isdigit():
        try:
            dt = datetime.strptime(s, "%Y%m%d%H%M%S")
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return s
    return s or "-"


def _true_ranges(bars: List[Dict[str, Any]]) -> List[float]:
    """
    True range list aligned to bars[1:].
    bars are expected to be ascending by date.
    """
    trs: List[float] = []
    prev_close: Optional[float] = None
    for b in bars:
        h = b.get("high")
        l = b.get("low")
        c = b.get("close")
        if not isinstance(h, (int, float)) or not isinstance(l, (int, float)):
            prev_close = float(c) if isinstance(c, (int, float)) else prev_close
            continue
        hi = float(h)
        lo = float(l)
        tr = hi - lo
        if isinstance(prev_close, (int, float)):
            tr = max(tr, abs(hi - float(prev_close)), abs(lo - float(prev_close)))
        trs.append(tr)
        prev_close = float(c) if isinstance(c, (int, float)) else prev_close
    return trs


def _atr(bars: List[Dict[str, Any]], n: int = 14) -> Optional[float]:
    trs = _true_ranges(bars)
    if len(trs) < max(2, n):
        return None
    return _mean(trs[-n:])


def compute_levels(
    q: Dict[str, Any],
    kline_bars: List[Dict[str, Any]],
    kline_signals: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Produce concrete levels for strategy narration.
    """
    last = q.get("last")
    prev_close = q.get("prev_close")
    open_px = q.get("open")
    high = q.get("high")
    low = q.get("low")

    w15 = (kline_signals.get("w15") or {}) if isinstance(kline_signals, dict) else {}
    stage_hi = w15.get("high_n")
    stage_lo = w15.get("low_n")
    ma5 = w15.get("ma5") or (kline_signals.get("w7") or {}).get("ma5")
    ma10 = w15.get("ma10") or (kline_signals.get("w7") or {}).get("ma10")
    atr14 = _atr(kline_bars, 14) if kline_bars else None

    out: Dict[str, Any] = {
        "tick": 0.01,
        "intraday": {
            "open": open_px,
            "high": high,
            "low": low,
            "prev_close": prev_close,
        },
        "stage_15d": {
            "high": stage_hi,
            "low": stage_lo,
            "ma5": ma5,
            "ma10": ma10,
            "atr14": atr14,
        },
    }

    # Candidate levels for narration: combine intraday + stage + round numbers.
    cands: List[float] = []
    for x in (low, prev_close, high, stage_lo, stage_hi, ma5, ma10):
        if isinstance(x, (int, float)):
            cands.append(float(x))

    anchor = None
    if isinstance(last, (int, float)):
        anchor = float(last)
    elif isinstance(prev_close, (int, float)):
        anchor = float(prev_close)
    if isinstance(anchor, (int, float)):
        # Add nearby 0.10 grid and integer level (commonly watched).
        base_0p1 = round(anchor, 1)
        for i in range(-2, 3):
            cands.append(base_0p1 + i * 0.1)
        cands.append(float(int(anchor)))  # e.g. 11.00 for 11.xx

    # De-dup + sort.
    def _uniq(xs: List[float]) -> List[float]:
        seen: set[float] = set()
        out2: List[float] = []
        for x in xs:
            rx = _round_to_tick(float(x), 0.01)
            if rx in seen:
                continue
            seen.add(rx)
            out2.append(rx)
        return out2

    lvls = sorted(_uniq(cands))

    # If last is known, split to below/above for readability.
    if isinstance(last, (int, float)):
        lp = float(last)
        out["supports"] = [x for x in lvls if x <= lp + 1e-9]
        out["resistances"] = [x for x in lvls if x >= lp - 1e-9]
    else:
        out["supports"] = lvls
        out["resistances"] = lvls

    return out


def generate_trade_plan(
    q: Dict[str, Any],
    levels: Dict[str, Any],
    signals: Dict[str, Any],
    kline_signals: Dict[str, Any],
    mode: str,
) -> Dict[str, Any]:
    """
    Generate deterministic, condition-based trade plan fields.
    This is NOT financial advice; it's a structured way to express scenarios.
    """
    last = q.get("last")
    prev_close = q.get("prev_close")
    day_high = q.get("high")
    day_low = q.get("low")

    w15 = (kline_signals.get("w15") or {}) if isinstance(kline_signals, dict) else {}
    trend = w15.get("trend_label") or "-"
    stage_low = (levels.get("stage_15d") or {}).get("low")
    stage_ma10 = (levels.get("stage_15d") or {}).get("ma10")
    atr14 = (levels.get("stage_15d") or {}).get("atr14")

    out: Dict[str, Any] = {"mode": mode, "trend_15d": trend, "setups": []}

    # Bias: simple rule-based label.
    if isinstance(last, (int, float)) and isinstance(stage_low, (int, float)) and float(last) < float(stage_low) - 1e-9:
        out["bias"] = "breakdown_below_15d_low"
    elif trend == "down":
        out["bias"] = "downtrend"
    elif trend == "up":
        out["bias"] = "uptrend"
    else:
        out["bias"] = "range"

    tick = float(levels.get("tick") or 0.01)
    atr = float(atr14) if isinstance(atr14, (int, float)) else None
    stop_pad_intraday = max(2 * tick, (0.25 * atr) if atr else 2 * tick)
    stop_pad_swing = max(5 * tick, (1.0 * atr) if atr else 5 * tick)

    # Setup 1: intraday bounce / defense near day low.
    if mode == "intraday" and isinstance(day_low, (int, float)) and isinstance(day_high, (int, float)):
        entry_zone = (float(day_low), float(day_low) + max(5 * tick, stop_pad_intraday))
        stop = _round_to_tick(float(day_low) - stop_pad_intraday, tick)
        tps: List[float] = []
        tps.append(_round_to_tick(float(day_high), tick))
        if isinstance(prev_close, (int, float)):
            tps.append(_round_to_tick(float(prev_close), tick))
        tps = sorted(list({round(x, 2) for x in tps}))
        out["setups"].append(
            {
                "name": "intraday_defense_bounce",
                "entry_zone": [round(entry_zone[0], 2), round(entry_zone[1], 2)],
                "stop": stop,
                "take_profit": tps,
                "invalidation": "break_day_low_with_momentum_or_close_below_support",
            }
        )

    # Setup 2: reclaim key level (prev_close or MA10) as right-side confirmation.
    if mode in ("intraday", "swing") and isinstance(prev_close, (int, float)):
        trigger = _round_to_tick(float(prev_close), tick)
        stop = _round_to_tick(trigger - (stop_pad_intraday if mode == "intraday" else stop_pad_swing), tick)
        tps: List[float] = []
        if isinstance(day_high, (int, float)):
            tps.append(_round_to_tick(max(float(day_high), trigger + 5 * tick), tick))
        if isinstance(stage_ma10, (int, float)):
            tps.append(_round_to_tick(float(stage_ma10), tick))
        out["setups"].append(
            {
                "name": "reclaim_prev_close",
                "trigger_above": trigger,
                "stop": stop,
                "take_profit": sorted(list({round(x, 2) for x in tps})) if tps else None,
                "invalidation": "failed_reclaim_and_fall_back_below_trigger",
            }
        )

    # Setup 3: swing only - reclaim MA10 (trend repair).
    if mode == "swing" and isinstance(stage_ma10, (int, float)):
        trigger = _round_to_tick(float(stage_ma10), tick)
        stop = _round_to_tick(trigger - stop_pad_swing, tick)
        out["setups"].append(
            {
                "name": "reclaim_ma10_trend_repair",
                "trigger_above": trigger,
                "stop": stop,
                "take_profit": None,
                "invalidation": "close_back_below_ma10",
            }
        )

    # Attach quick risk note.
    out["risk_notes"] = [
        "Keep position small if bias is downtrend/breakdown; wait for confirmation if uncertain.",
        "Use stop-loss (price-based) + time stop; avoid averaging down in a breakdown.",
        "Treat financing/flow headlines as noisy; focus on price+volume response after announcements.",
    ]

    # Add a couple contextual fields useful for narration.
    out["context"] = {
        "pct_vs_prev_close": signals.get("pct_vs_prev_close"),
        "orderbook_imbalance": signals.get("orderbook_imbalance_-1_1"),
    }
    return out


def generate_trade_advice(
    q: Dict[str, Any],
    levels: Dict[str, Any],
    signals: Dict[str, Any],
    kline_signals: Dict[str, Any],
    trade_plan: Dict[str, Any],
    mode: str,
) -> Dict[str, Any]:
    """
    Produce a concise "buy/sell/watch" style suggestion derived from 7/15d stage data.
    This is NOT financial advice; it's a deterministic summary of technical conditions.
    """
    w7 = (kline_signals.get("w7") or {}) if isinstance(kline_signals, dict) else {}
    w15 = (kline_signals.get("w15") or {}) if isinstance(kline_signals, dict) else {}

    last = q.get("last")
    prev_close = q.get("prev_close")
    bias = (trade_plan or {}).get("bias") or "-"
    trend_15d = (trade_plan or {}).get("trend_15d") or (w15.get("trend_label") or "-")

    stage = levels.get("stage_15d") or {}
    stage_hi = stage.get("high")
    stage_lo = stage.get("low")
    ma5 = stage.get("ma5")
    ma10 = stage.get("ma10")

    # Base recommendation: derived from 15d bias, then fine-tuned by proximity to key levels.
    rec = "watch"
    confidence = "low"
    if bias == "breakdown_below_15d_low":
        rec = "sell_or_avoid"
        confidence = "high"
    elif bias == "downtrend":
        rec = "sell_rallies_or_wait"
        confidence = "medium"
    elif bias == "uptrend":
        rec = "buy_dips_or_hold"
        confidence = "medium"
    elif bias == "range":
        rec = "range_trade_or_watch"
        confidence = "low"

    rationale: List[str] = []
    if isinstance(w15.get("ret_n_pct"), (int, float)):
        rationale.append(f"15日涨跌 {float(w15.get('ret_n_pct')):.2f}%（趋势：{trend_15d}）")
    if isinstance(w7.get("ret_n_pct"), (int, float)):
        rationale.append(f"7日涨跌 {float(w7.get('ret_n_pct')):.2f}%")
    if isinstance(w15.get("max_drawdown_pct"), (int, float)):
        rationale.append(f"15日最大回撤 {float(w15.get('max_drawdown_pct')):.2f}%")

    if isinstance(last, (int, float)) and isinstance(ma10, (int, float)):
        if float(last) >= float(ma10):
            rationale.append("价格在MA10上方（阶段修复）")
            if rec in ("sell_rallies_or_wait", "sell_or_avoid"):
                rec = "watch_to_buy"
                confidence = "low"
        else:
            rationale.append("价格在MA10下方（阶段偏弱）")

    pct_to_low = w15.get("pct_to_low")
    pct_to_high = w15.get("pct_to_high")
    if isinstance(pct_to_low, (int, float)):
        if float(pct_to_low) <= 1.0:
            rationale.append("接近15日低位（支撑/破位风险并存）")
            if rec == "sell_rallies_or_wait":
                rec = "watch"
                confidence = "low"
    if isinstance(pct_to_high, (int, float)) and float(pct_to_high) <= 1.0:
        rationale.append("接近15日高位（上方空间有限，易震荡）")

    # Convert trade_plan setups to user-facing triggers.
    buy_triggers: List[str] = []
    sell_triggers: List[str] = []

    for s in (trade_plan.get("setups") or []):
        name = s.get("name") or ""
        if name in ("reclaim_ma10_trend_repair", "reclaim_prev_close"):
            trig = s.get("trigger_above")
            if isinstance(trig, (int, float)):
                buy_triggers.append(f"站上并站稳 {float(trig):.2f}（{name}）")
        if name == "intraday_defense_bounce":
            ez = s.get("entry_zone") or []
            if isinstance(ez, list) and len(ez) == 2:
                buy_triggers.append(f"回踩 {float(ez[0]):.2f}–{float(ez[1]):.2f} 获得承接（盘中防守）")

    if isinstance(stage_lo, (int, float)):
        sell_triggers.append(f"有效跌破并放量失守 15日低点 {float(stage_lo):.2f}")
    if isinstance(ma10, (int, float)):
        sell_triggers.append(f"反抽不回 MA10（约 {float(ma10):.2f}）且再度走弱")
    if isinstance(prev_close, (int, float)):
        sell_triggers.append(f"跌破昨收 {float(prev_close):.2f} 后无法收复（偏短线）")

    # Add a simple tape read hint for intraday.
    if mode == "intraday":
        obi = signals.get("orderbook_imbalance_-1_1")
        pir = signals.get("pos_in_range_0_1")
        if isinstance(obi, (int, float)) and isinstance(pir, (int, float)):
            if float(pir) <= 0.3 and float(obi) >= 0.15:
                rationale.append("日内靠近低位且买盘挂单略占优（仅供观察）")
            if float(pir) <= 0.3 and float(obi) <= -0.15:
                rationale.append("日内靠近低位但卖盘挂单占优（谨慎）")

    key_levels: Dict[str, Any] = {
        "stage_15d_low": stage_lo,
        "stage_15d_high": stage_hi,
        "ma5": ma5,
        "ma10": ma10,
    }

    return {
        "mode": mode,
        "recommendation": rec,
        "confidence": confidence,
        "rationale": rationale,
        "buy_triggers": buy_triggers,
        "sell_triggers": sell_triggers,
        "key_levels": key_levels,
        "disclaimer": "Deterministic technical summary only; not financial advice.",
    }


def classify_news_title(title: str) -> Dict[str, str]:
    """
    Very lightweight keyword classifier for headlines.
    Returns {theme, horizon, uncertainty}.
    """
    t = (title or "").strip()
    if not t:
        return {"theme": "other", "horizon": "unknown", "uncertainty": "high"}

    rules = [
        (("主力资金", "资金净", "净买入", "净卖出", "龙虎榜"), ("flow", "intraday", "high")),
        (("定增", "向特定对象发行", "发行股票", "受理", "审核", "深交所", "上交所"), ("financing", "days-weeks", "medium")),
        (("战略合作", "达成合作", "签署", "合作", "协议"), ("partnership", "weeks", "medium")),
        (("回购", "增持"), ("capital_actions", "days-weeks", "medium")),
        (("解禁", "限售股", "解除限售"), ("unlock", "days", "medium")),
        (("业绩", "预告", "年报", "季报", "净利润", "营收"), ("earnings", "days-weeks", "medium")),
        (("辞职", "独立董事", "董事", "高管", "更换", "章程"), ("governance", "weeks", "medium")),
        (("政策", "补贴", "监管", "征求意见", "规划"), ("policy", "weeks-months", "high")),
    ]
    for kws, (theme, horizon, uncertainty) in rules:
        if any(kw in t for kw in kws):
            return {"theme": theme, "horizon": horizon, "uncertainty": uncertainty}
    return {"theme": "other", "horizon": "unknown", "uncertainty": "high"}


def aggregate_news(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts: Dict[str, int] = {}
    enriched: List[Dict[str, Any]] = []
    for it in items:
        title = it.get("title") or ""
        meta = classify_news_title(title)
        theme = meta["theme"]
        counts[theme] = counts.get(theme, 0) + 1
        obj = dict(it)
        obj.update(meta)
        enriched.append(obj)
    top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {"theme_counts": counts, "theme_rank": [{"theme": k, "count": v} for k, v in top], "items": enriched}


def main(argv: List[str]) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("symbol", help="e.g. sz000625/sh600519/000625")
    p.add_argument("--timeout", type=float, default=10.0)
    p.add_argument("--news-limit", type=int, default=10)
    p.add_argument("--kline-days", type=int, default=15, help="fetch recent daily bars for stage analysis (0 to disable)")
    p.add_argument("--kline-adjust", default="qfq", help="qfq/hfq/none (depends on Tencent backend)")
    p.add_argument("--mode", choices=["intraday", "swing"], default="intraday", help="emit strategy fields for intraday or swing")
    p.add_argument("--market", action="store_true", help="include market index context (broad indices + simple style rotation)")
    p.add_argument(
        "--market-symbols",
        default="",
        help="comma-separated symbols for market context (default: built-in indices like sh000001,sh000300,sz399006...)",
    )
    p.add_argument(
        "--market-kline-days",
        type=int,
        default=15,
        help="fetch daily bars for market indices to compute 7/15d trend (0 to disable, default 15)",
    )
    p.add_argument(
        "--benchmark",
        default="",
        help="benchmark index symbol for relative strength vs market (default sh000300)",
    )
    p.add_argument("--md", action="store_true", help="print Notion-friendly Markdown (Chinese)")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    sym = qt_quote.normalize_symbol(args.symbol)
    raw = qt_quote.fetch_quotes([sym], timeout=args.timeout)
    quotes = qt_quote.parse_response(raw)
    q = quotes.get(sym)
    if not q:
        sys.stderr.write(f"{sym}: no quote data\n")
        return 2

    # Prefer "Name 000625" as the news query; fall back to symbol.
    name = q.get("name") or ""
    code = q.get("code") or ""
    news_query = f"{name} {code}".strip() or sym

    rss = google_news_rss.fetch_rss(news_query, timeout=args.timeout)
    news = google_news_rss.parse_rss(rss)
    items = news.get("items", [])[: max(args.news_limit, 0)]
    news_signals = aggregate_news(items) if items else {"theme_counts": {}, "theme_rank": [], "items": []}

    kline_obj: Dict[str, Any] = {}
    kline_bars: List[Dict[str, Any]] = []
    if args.kline_days and args.kline_days > 0:
        try:
            kline_obj = qt_kline.fetch_bars(sym, days=args.kline_days, adjust=args.kline_adjust, timeout=args.timeout)
            kline_bars = kline_obj.get("bars") or []
        except Exception as e:  # noqa: BLE001
            kline_obj = {"symbol": sym, "error": str(e)}

    kline_signals: Dict[str, Any] = {}
    if kline_bars:
        kline_signals = {
            "w7": compute_window_signals(kline_bars, 7),
            "w15": compute_window_signals(kline_bars, 15),
        }
        # Add a couple "today vs stage" levels for easier narrative.
        last_px = q.get("last")
        if isinstance(last_px, (int, float)):
            for k in ("w7", "w15"):
                w = kline_signals.get(k) or {}
                hi = w.get("high_n")
                lo = w.get("low_n")
                if isinstance(hi, (int, float)) and last_px:
                    w["pct_to_high"] = (float(hi) - float(last_px)) / float(last_px) * 100.0
                if isinstance(lo, (int, float)) and last_px:
                    w["pct_to_low"] = (float(last_px) - float(lo)) / float(last_px) * 100.0
                kline_signals[k] = w

    levels = compute_levels(q, kline_bars, kline_signals)
    sigs = compute_signals(q)
    trade_plan = generate_trade_plan(q, levels, sigs, kline_signals, mode=args.mode)
    trade_advice = generate_trade_advice(q, levels, sigs, kline_signals, trade_plan, mode=args.mode)

    market_ctx: Optional[Dict[str, Any]] = None
    rel_strength: Optional[Dict[str, Any]] = None
    if args.market:
        bench = qt_quote.normalize_symbol(args.benchmark) if args.benchmark else DEFAULT_BENCHMARK_SYMBOL
        market_syms = _parse_symbol_csv(args.market_symbols) or list(DEFAULT_MARKET_SYMBOLS)
        if bench not in market_syms:
            market_syms.append(bench)
        market_ctx = fetch_market_context(
            market_syms,
            kline_days=max(int(args.market_kline_days or 0), 0),
            kline_adjust=args.kline_adjust,
            timeout=args.timeout,
        )
        rel_strength = {"benchmark": bench}
        sw7 = ((kline_signals.get("w7") or {}).get("ret_n_pct")) if isinstance(kline_signals, dict) else None
        sw15 = ((kline_signals.get("w15") or {}).get("ret_n_pct")) if isinstance(kline_signals, dict) else None
        bw7 = _market_ret((market_ctx.get("kline_signals") or {}) if isinstance(market_ctx, dict) else {}, bench, "w7")
        bw15 = _market_ret((market_ctx.get("kline_signals") or {}) if isinstance(market_ctx, dict) else {}, bench, "w15")
        if isinstance(sw7, (int, float)) and isinstance(bw7, (int, float)):
            rel_strength["w7_stock_minus_bench_pct"] = float(sw7) - float(bw7)
        if isinstance(sw15, (int, float)) and isinstance(bw15, (int, float)):
            rel_strength["w15_stock_minus_bench_pct"] = float(sw15) - float(bw15)

    digest: Dict[str, Any] = {
        "quote": q,
        "signals": sigs,
        "kline": kline_obj or None,
        "kline_signals": kline_signals or None,
        "market": market_ctx,
        "relative_strength": rel_strength,
        "levels": levels,
        "trade_plan": trade_plan,
        "trade_advice": trade_advice,
        "news_query": news_query,
        "news": news_signals.get("items") if isinstance(news_signals, dict) else items,
        "news_signals": {k: v for k, v in (news_signals or {}).items() if k != "items"} if isinstance(news_signals, dict) else None,
    }

    if args.json:
        json.dump(digest, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 0

    if args.md:
        # Keep it deterministic and sectioned for easy copy/paste into Notion.
        ts_h = _fmt_ts_compact(q.get("timestamp"))
        sys.stdout.write(f"# {q.get('name','-')}（{sym}）盘中/日更\n")
        sys.stdout.write(f"- 时间：{ts_h}\n")
        sys.stdout.write(f"- 最新：{q.get('last','-')}（{q.get('change','-')}，{q.get('change_pct','-')}%）\n")
        sys.stdout.write(f"- 今开/高/低/昨收：{q.get('open','-')} / {q.get('high','-')} / {q.get('low','-')} / {q.get('prev_close','-')}\n")
        sys.stdout.write(f"- 成交：{q.get('volume','-')}（约{q.get('amount_wan','-')}万） 换手：{q.get('turnover_pct','-')}%\n")
        buy1 = (q.get('buy') or [{}])[0] if isinstance(q.get('buy'), list) else {}
        sell1 = (q.get('sell') or [{}])[0] if isinstance(q.get('sell'), list) else {}
        sys.stdout.write(f"- 五档：买一 {buy1.get('price','-')}/{buy1.get('vol','-')}；卖一 {sell1.get('price','-')}/{sell1.get('vol','-')}\n")

        if isinstance(market_ctx, dict) and (market_ctx.get("quotes") or market_ctx.get("error")):
            sys.stdout.write("\n## 大盘/风格（参考）\n")
            if market_ctx.get("error"):
                sys.stdout.write(f"- market_error：{market_ctx.get('error')}\n")
            quotes_m = market_ctx.get("quotes") or {}
            ksig_m = market_ctx.get("kline_signals") or {}
            for msym in (market_ctx.get("symbols") or []):
                mq = quotes_m.get(msym) or {}
                nm = mq.get("name") or msym
                pct = mq.get("change_pct")
                lastm = mq.get("last")
                w15 = (ksig_m.get(msym) or {}).get("w15") if isinstance(ksig_m, dict) else {}
                t15 = w15.get("trend_label") if isinstance(w15, dict) else None
                r15 = w15.get("ret_n_pct") if isinstance(w15, dict) else None
                extra: List[str] = []
                if isinstance(r15, (int, float)):
                    extra.append(f"15d {float(r15):.2f}%")
                if t15:
                    extra.append(f"trend {t15}")
                extra_s = ("；" + "；".join(extra)) if extra else ""
                if isinstance(pct, (int, float)) and isinstance(lastm, (int, float)):
                    sys.stdout.write(f"- {nm}（{msym}）：{float(lastm):.2f}（{float(pct):+.2f}%）{extra_s}\n")
                else:
                    sys.stdout.write(f"- {nm}（{msym}）{extra_s}\n")

            rot = market_ctx.get("rotation_signals") or {}
            if isinstance(rot, dict) and rot:
                parts: List[str] = []
                svl = rot.get("small_vs_large")
                gvv = rot.get("growth_vs_value")
                if svl:
                    parts.append(f"小盘vs大盘：{svl}")
                if gvv:
                    parts.append(f"成长vs价值：{gvv}")
                if parts:
                    sys.stdout.write(f"- 风格轮动：{'；'.join(parts)}\n")

            if isinstance(rel_strength, dict):
                rs15 = rel_strength.get("w15_stock_minus_bench_pct")
                rs7 = rel_strength.get("w7_stock_minus_bench_pct")
                rs_parts: List[str] = []
                if isinstance(rs7, (int, float)):
                    rs_parts.append(f"7d相对强弱 {float(rs7):+.2f}%")
                if isinstance(rs15, (int, float)):
                    rs_parts.append(f"15d相对强弱 {float(rs15):+.2f}%")
                if rs_parts:
                    sys.stdout.write(f"- 相对基准（{rel_strength.get('benchmark')}）：{'；'.join(rs_parts)}\n")

        sys.stdout.write("\n## 关键价位\n")
        sys.stdout.write(f"- 支撑：{', '.join(f'{x:.2f}' for x in (levels.get('supports') or [])[:5]) or '-'}\n")
        sys.stdout.write(f"- 压力：{', '.join(f'{x:.2f}' for x in (levels.get('resistances') or [])[:6]) or '-'}\n")
        atr14 = ((levels.get('stage_15d') or {}).get('atr14'))
        if isinstance(atr14, (int, float)):
            sys.stdout.write(f"- ATR14（参考波动）：{float(atr14):.2f}\n")
        sys.stdout.write("\n## 策略（条件化，不构成投资建议）\n")
        sys.stdout.write(f"- 模式：{trade_plan.get('mode','-')}；阶段偏向：{trade_plan.get('bias','-')}（15日趋势：{trade_plan.get('trend_15d','-')}）\n")
        for s in (trade_plan.get("setups") or []):
            name = s.get("name") or "-"
            if "entry_zone" in s:
                ez = s.get("entry_zone") or []
                sys.stdout.write(f"- {name}：入场区间 {ez}；止损 {s.get('stop','-')}；止盈 {s.get('take_profit','-')}\n")
            else:
                sys.stdout.write(f"- {name}：触发 {s.get('trigger_above','-')} 上方确认；止损 {s.get('stop','-')}；止盈 {s.get('take_profit','-')}\n")

        sys.stdout.write("\n## 交易建议（预测，供参考；条件化表达）\n")
        sys.stdout.write(f"- 偏向：{trade_advice.get('recommendation','-')}（置信度：{trade_advice.get('confidence','-')}）\n")
        if trade_advice.get("rationale"):
            sys.stdout.write(f"- 依据：{'；'.join(trade_advice.get('rationale') or [])}\n")
        if trade_advice.get("buy_triggers"):
            sys.stdout.write(f"- 可能的买入触发：{'；'.join(trade_advice.get('buy_triggers') or [])}\n")
        if trade_advice.get("sell_triggers"):
            sys.stdout.write(f"- 可能的卖出/止损触发：{'；'.join(trade_advice.get('sell_triggers') or [])}\n")

        sys.stdout.write("\n## 新闻（近10条）\n")
        for it in (news_signals.get("items") or []):
            title = it.get("title") or "-"
            src = it.get("source") or ""
            pd = it.get("pubDate") or ""
            theme = it.get("theme") or ""
            horizon = it.get("horizon") or ""
            sys.stdout.write(f"- {title}\n")
            sys.stdout.write(f"  {theme}  {horizon}  {src}  {pd}\n")
        return 0

    sys.stdout.write(qt_quote.format_human(q) + "\n\n")
    if kline_bars:
        w7 = (kline_signals.get("w7") or {}) if isinstance(kline_signals, dict) else {}
        w15 = (kline_signals.get("w15") or {}) if isinstance(kline_signals, dict) else {}
        def _fmt_pct(x: Any) -> str:
            return "-" if not isinstance(x, (int, float)) else f"{float(x):.2f}%"
        sys.stdout.write("stage:\n")
        if w7.get("bars"):
            sys.stdout.write(
                f"- 7d: ret={_fmt_pct(w7.get('ret_n_pct'))}  mdd={_fmt_pct(w7.get('max_drawdown_pct'))}  "
                f"trend={w7.get('trend_label','-')}  to_hi={_fmt_pct(w7.get('pct_to_high'))}  to_lo={_fmt_pct(w7.get('pct_to_low'))}\n"
            )
        if w15.get("bars"):
            sys.stdout.write(
                f"- 15d: ret={_fmt_pct(w15.get('ret_n_pct'))}  mdd={_fmt_pct(w15.get('max_drawdown_pct'))}  "
                f"trend={w15.get('trend_label','-')}  to_hi={_fmt_pct(w15.get('pct_to_high'))}  to_lo={_fmt_pct(w15.get('pct_to_low'))}\n"
            )
        sys.stdout.write("\n")
    sys.stdout.write("levels:\n")
    sys.stdout.write(f"- supports: {levels.get('supports')}\n")
    sys.stdout.write(f"- resistances: {levels.get('resistances')}\n")
    atr14 = ((levels.get("stage_15d") or {}).get("atr14"))
    if isinstance(atr14, (int, float)):
        sys.stdout.write(f"- atr14: {float(atr14):.4f}\n")
    sys.stdout.write("\n")
    sys.stdout.write("trade_plan:\n")
    sys.stdout.write(f"- mode={trade_plan.get('mode')} bias={trade_plan.get('bias')} trend_15d={trade_plan.get('trend_15d')}\n")
    for s in (trade_plan.get("setups") or []):
        sys.stdout.write(f"- {s}\n")
    sys.stdout.write("\n")
    sys.stdout.write(f"news_query: {news_query}\n\n")
    for it in (news_signals.get("items") or []):
        title = it.get("title") or "-"
        src = it.get("source") or ""
        pd = it.get("pubDate") or ""
        link = it.get("link") or ""
        theme = it.get("theme") or ""
        horizon = it.get("horizon") or ""
        sys.stdout.write(f"- {title}\n")
        if theme or horizon:
            sys.stdout.write(f"  {theme}  {horizon}\n")
        if src or pd:
            sys.stdout.write(f"  {src}  {pd}\n")
        if link:
            sys.stdout.write(f"  {link}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
