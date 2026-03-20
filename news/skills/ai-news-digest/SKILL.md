---
name: ai-news-digest
description: Fetch latest AI/tech news from Google News, 36kr, AIBase, AI-Bot (ai-bot.cn) and other configurable sources; deduplicate; output as title + short brief + link. Use when asked to “抓取/汇总/整理” AI 科技资讯、AI 新闻快讯、AI 行业动态，或需要 50-100+ 条去重资讯列表。
---

# AI News Digest

## Run

Use the bundled script (network required):

```bash
python3 skills/ai-news-digest/scripts/ai_news_digest.py --max-items 100
```

Output defaults to Markdown (title/brief/link). Use JSON when needed:

```bash
python3 skills/ai-news-digest/scripts/ai_news_digest.py --max-items 100 --format json
```

## Dedupe

- Deduplicate across sources within the run (normalized URL + normalized title).
- Persist a `seen` cache by default so repeated runs prefer “new” items first.

To ignore previous runs:

```bash
python3 skills/ai-news-digest/scripts/ai_news_digest.py --fresh
```

## Tuning

- Limit (0 = no limit): `--max-items 0`
- Keyword filter (repeatable or comma-separated): `--include "AI,大模型,OpenAI" --exclude "课程,培训"`
- Default filter: when you don't pass `--include`, the script enables a built-in AI keyword filter (disable via `--no-default-ai-filter`).
- Default noise filter: when you don't pass `--exclude`, the script excludes common ETF/market-noise keywords (disable via `--no-default-noise-filter`).
- Focus filter (default): keep all overseas AI news; for domestic LLM news only keep Ali/Qwen（通义/千问）、Kimi（月之暗面）、DeepSeek（深度求索）. Disable via `--no-china-llm-focus`.
- Google News queries: `--google-query "人工智能" --google-query "大模型"`
- Today only (local timezone): `--today`
  - Note: items without a reliable publish time may be dropped. By default the script will try to fetch article pages to infer publish time; disable with `--no-resolve-missing-dates`.
  - For speed, date-resolving is capped by `--max-date-resolve` (default 20).

## Sources

Default sources live in:

- `skills/ai-news-digest/references/sources.json`

Edit that file to add/remove sources, or to change URLs.

## Notes

- Running the script requires outbound network access. In restricted/sandboxed environments you may need to grant permission for network calls.
