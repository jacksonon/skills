---
name: qt-stock-scout
description: Fetch realtime China A-share quotes from Tencent qt.gtimg.cn (e.g., http://qt.gtimg.cn/q=sz000625), decode/parse quote + 5-level order book, fetch recent daily K-line bars (7-15 trading days) for stage analysis, pull the latest web news (Google News RSS), and (optionally) add broad market index context + simple style rotation signals (small vs large, growth vs value). Output is structured short-term commentary and condition-based scenarios (not investment advice).
---

# Qt Stock Scout (CN A-share Quote + Stage + News)

## Quick workflow

0) (Recommended) Get **market context**: broad indices snapshot + simple style rotation (small/large, growth/value).
1) Get a realtime quote snapshot (price, change%, OHLC, volume, order book).
2) Fetch recent daily K-line bars (default 15 trading days; summarize 7/15-day windows).
3) Fetch the latest news headlines for the company/ticker.
4) Write a **structured** analysis: what moved (today + stage), why it might have moved (market + news + flow), and realistic scenarios + risks.
5) Provide a **condition-based** buy/sell/hold suggestion derived from recent 7–15 trading days (prediction, for reference only; not investment advice).

Use the bundled scripts for fetching/parsing so the output is consistent and encoding-safe.

## Commands

Realtime quote (Tencent `qt.gtimg.cn`):

```bash
python3 qt-stock-scout/scripts/qt_quote.py sz000625
python3 qt-stock-scout/scripts/qt_quote.py sh600519 --json
```

Recent K-line (Tencent `fqkline`, daily bars):

```bash
python3 qt-stock-scout/scripts/qt_kline.py sz000625 --days 15 --json
python3 qt-stock-scout/scripts/qt_kline.py sh600519 --days 30 --json
```

Latest news (Google News RSS):

```bash
python3 qt-stock-scout/scripts/google_news_rss.py '长安汽车 000625' --limit 10
```

Combined digest (quote + stage + news):

```bash
python3 qt-stock-scout/scripts/stock_digest.py sz000625 --news-limit 10 --kline-days 15
python3 qt-stock-scout/scripts/stock_digest.py sz000625 --news-limit 10 --kline-days 15 --json
python3 qt-stock-scout/scripts/stock_digest.py sz000625 --news-limit 10 --kline-days 15 --mode intraday --md
python3 qt-stock-scout/scripts/stock_digest.py sz000625 --news-limit 10 --kline-days 15 --mode swing --md
```

Combined digest + market context (recommended for "板块/风格轮动" and "大盘怎么走"):

```bash
python3 qt-stock-scout/scripts/stock_digest.py sz000625 --news-limit 10 --kline-days 15 --mode swing --md --market
python3 qt-stock-scout/scripts/stock_digest.py sz000625 --news-limit 10 --kline-days 15 --json --market

# Customize benchmark / indices
python3 qt-stock-scout/scripts/stock_digest.py sz000625 --md --market --benchmark sh000300
python3 qt-stock-scout/scripts/stock_digest.py sz000625 --md --market --market-symbols sh000001,sh000300,sh000905,sh000852,sz399006,sh000016
```

## "轮动"与“高收益”需求的处理方式（给AI的内部准则）

- **先定义范围**：用户问“哪些票收益更好”，默认要先问：时间尺度（1-3天/1-4周/1-2年）、风格偏好（稳健/进攻）、资金体量（影响是否能做小盘）、以及候选池（某板块/自选股/指数成分/ETF）。
- **轮动根本原因（讲逻辑，不装神）**：大盘/风格轮动通常来自“流动性/风险偏好（risk-on vs risk-off）+ 政策预期 + 景气/业绩验证 + 估值/拥挤度”组合变化。回答里要把“驱动”和“不确定性”拆开说。
- **本工具能做的“轮动”**：基于指数相对强弱做**风格轮动**（小盘vs大盘、成长vs价值）+ 个股相对强弱（跑赢/跑输基准），这是可重复、可解释、可验证的。
- **本工具做不了的**：没有完整“板块/行业成分 + 全市场涨跌家数/资金流”的数据库时，不要假装能精准给出“全A板块轮动排行榜”。要么让用户给股票池/板块代理（ETF/龙头列表），要么明确能力边界。
- **“更高收益”只能用概率语言**：用“更可能/条件满足时倾向/风险是什么”表达。不要承诺收益、不要用“稳赚/必涨/庄家/内幕”叙事。

## Output expectations (when answering users)

- Always include (today): ticker, name, timestamp, last price, change + change%, day high/low, volume, and best bid/ask.
- When user asks about "大盘/轮动/环境": include market indices snapshot and a **coarse** style rotation inference (small vs large, growth vs value).
- Always include (stage, based on recent bars):
  - last 7 / 15 trading days: return%, high/low range, position in range, max drawdown (rough), MA5/MA10 (if available), volume vs recent average.
- When market context is enabled: include **relative strength** vs benchmark (stock 7/15d return minus benchmark 7/15d return), and explain what that means.
- Always include (advice, condition-based): a rational "buy/sell/watch" bias that references 7–15d trend/levels, with **explicit triggers** (what confirms / what invalidates).
- Summarize news into: (1) theme/category, (2) likely impact horizon (intraday / days / weeks), (3) uncertainty.
- Provide scenarios instead of certainty:
  - Bull / base / bear cases with 1–3 drivers each.
  - What would invalidate each case using concrete levels (e.g., “跌破近15日低点且放量” / “回到MA10上方并站稳”).
- Add a brief disclaimer: informational only, not financial advice. Avoid “稳赚/必涨/庄家/内幕” style wording.
  - If you need quick, deterministic features, use `stock_digest.py --json` and read `signals` + `kline_signals` (e.g. position in range, orderbook imbalance, 7/15d trend_label).
  - Strategy helpers: `stock_digest.py` also emits `levels` + `trade_plan` (condition-based setups) + `trade_advice` (buy/sell/watch summary) in JSON, and `--md` produces a Notion-friendly Markdown note.

## Recommended answer structure (for the AI)

0) 大盘/风格：指数强弱 + 风格轮动（小盘vs大盘、成长vs价值）。把“环境”讲清楚：是 risk-on 还是 risk-off。
1) 个股今日盘面：涨跌幅、振幅、收盘/最新价在日内位置（`signals.pos_in_range_0_1`）、五档量差（`orderbook_imbalance_-1_1`）。
2) 相对强弱：相对基准（默认沪深300）近7/15日强弱（`relative_strength.*`），说明“跑赢/跑输大盘”的含义与风险。
3) 阶段性（近7/15交易日）：趋势标签（`kline_signals.w7/w15.trend_label`）、区间位置（`pos_in_range_0_1`）、回撤（`max_drawdown_pct`）、量能（`vol_last_vs_avg`）、关键位距离（`pct_to_high/pct_to_low`）、标签（`tags`）。
4) 新闻线索：优先根据 `news_signals.theme_rank` 抽取 1-3 个主题，说明影响周期与不确定性；避免把“资金净流入/净流出快报”当作确定因果。
5) 情景推演：bull/base/bear，每个给出 1-2 个“触发条件/失效条件”（最好引用近7/15日高低点或 MA10 等具体价位/区域）。
6) 交易建议（预测，供参考；不要用“必涨/必跌”口吻）：
   - 先给一个“偏向”：`buy_dips_or_hold` / `watch_to_buy` / `watch` / `sell_rallies_or_wait` / `sell_or_avoid`（可标注低/中/高置信度）
   - 再给出 2–4 条**买入触发**（例如“站上MA10并站稳/收复昨收/回踩区间承接”）
   - 给出 2–4 条**卖出/止损触发**（例如“有效跌破近15日低点且放量/反抽不回MA10”）
   - 明确“失效条件/风险提示”，并保留“不构成投资建议”免责声明

## Notes

- `qt.gtimg.cn` responses are typically GBK encoded; scripts handle decoding.
- If the user provides only `000625`, assume `sz000625` unless they specify otherwise.
