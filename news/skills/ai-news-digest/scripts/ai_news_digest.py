#!/usr/bin/env python3
"""
Fetch latest AI/tech news from multiple sources (RSS/Atom + a few HTML lists),
dedupe, and output as title + brief + link.

No third-party deps (stdlib only).
"""

from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import html
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


_TRACKING_QUERY_PREFIXES = (
    "utm_",
    "spm",
    "from",
    "src",
    "ref",
    "refer",
    "share",
    "mkt_",
    "igshid",
)


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _write_text(path: str, s: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(s)


def _strip_html(s: str) -> str:
    s = html.unescape(s or "")
    # Fast path: remove tags
    s = re.sub(r"(?is)<(script|style)\b.*?>.*?</\1>", " ", s)
    s = re.sub(r"(?is)<br\s*/?>", "\n", s)
    s = re.sub(r"(?is)<.*?>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _truncate(s: str, n: int) -> str:
    s = (s or "").strip()
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip() + "…"


def _normalize_title(s: str) -> str:
    s = _strip_html(s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    # Remove common Google News “ - Source”
    s = re.sub(r"\s+-\s+[^-]{1,64}$", "", s).strip()
    return s


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url

    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return url

    # Normalize scheme/host case, strip fragments, remove tracking query params
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()

    query_pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    filtered: List[Tuple[str, str]] = []
    for k, v in query_pairs:
        lk = k.lower()
        if any(lk.startswith(p) for p in _TRACKING_QUERY_PREFIXES):
            continue
        filtered.append((k, v))
    query = urllib.parse.urlencode(filtered, doseq=True)

    # Some sites encode “redirect” URLs; keep as-is if no netloc.
    normalized = urllib.parse.urlunsplit((scheme, netloc, parts.path, query, ""))
    return normalized or url


def _parse_datetime(s: str) -> Optional[dt.datetime]:
    s = (s or "").strip()
    if not s:
        return None

    # RFC2822 (RSS pubDate)
    try:
        d = email.utils.parsedate_to_datetime(s)
        if d is not None:
            if d.tzinfo is None:
                d = d.replace(tzinfo=dt.timezone.utc)
            return d.astimezone(dt.timezone.utc)
    except Exception:
        pass

    # ISO8601 (Atom updated/published)
    try:
        ss = s.replace("Z", "+00:00")
        d = dt.datetime.fromisoformat(ss)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        return None


def _parse_datetime_with_default_tz(s: str, *, default_tz: dt.tzinfo) -> Optional[dt.datetime]:
    """
    Parse timestamps that may omit timezone (common in HTML meta tags).
    If timezone is missing, assume default_tz.
    Returns UTC datetime.
    """
    s = (s or "").strip()
    if not s:
        return None

    # Try the strict parser first (handles RFC2822 + ISO8601).
    d = _parse_datetime(s)
    if d is not None:
        return d

    # Date-only: YYYY-MM-DD or YYYY/MM/DD
    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$", s)
    if m:
        y, mo, da = m.groups()
        try:
            d0 = dt.datetime(int(y), int(mo), int(da), 0, 0, 0, tzinfo=default_tz)
            return d0.astimezone(dt.timezone.utc)
        except Exception:
            return None

    # Datetime without timezone: YYYY-MM-DD HH:MM[:SS]
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2}))?$", s)
    if m:
        y, mo, da, hh, mm, ss = m.groups()
        try:
            d0 = dt.datetime(
                int(y),
                int(mo),
                int(da),
                int(hh),
                int(mm),
                int(ss or "0"),
                tzinfo=default_tz,
            )
            return d0.astimezone(dt.timezone.utc)
        except Exception:
            return None

    return None


def _format_utc_iso(d_utc: dt.datetime) -> str:
    d_utc = d_utc.astimezone(dt.timezone.utc)
    return d_utc.isoformat().replace("+00:00", "Z")


def _fetch(url: str, *, timeout_s: float, user_agent: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        return resp.read()


@dataclass(frozen=True)
class NewsItem:
    title: str
    brief: str
    url: str
    source: str
    published_at: Optional[str] = None  # ISO8601 UTC

    def dedupe_key(self) -> str:
        return f"{_normalize_url(self.url)}\n{_normalize_title(self.title)}"


def _rss_or_atom_items(xml_bytes: bytes, *, source: str) -> List[NewsItem]:
    items: List[NewsItem] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return items

    tag = root.tag.lower()
    is_atom = tag.endswith("feed")

    if not is_atom:
        # RSS: <rss><channel><item>...
        for it in root.findall(".//item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            desc = (it.findtext("description") or "").strip()
            pub = (it.findtext("pubDate") or "").strip()
            d = _parse_datetime(pub)
            items.append(
                NewsItem(
                    title=_strip_html(title) or title,
                    brief=_truncate(_strip_html(desc), 220),
                    url=link,
                    source=source,
                    published_at=_format_utc_iso(d) if d else None,
                )
            )
        return items

    # Atom: <feed><entry>...
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    for entry in root.findall(f".//{ns}entry"):
        title = (entry.findtext(f"{ns}title") or "").strip()
        summary = (entry.findtext(f"{ns}summary") or "").strip()
        content = (entry.findtext(f"{ns}content") or "").strip()
        updated = (entry.findtext(f"{ns}updated") or "").strip()
        published = (entry.findtext(f"{ns}published") or "").strip()

        link = ""
        for ln in entry.findall(f"{ns}link"):
            href = ln.attrib.get("href", "").strip()
            rel = (ln.attrib.get("rel") or "").strip().lower()
            if not href:
                continue
            if rel in ("", "alternate"):
                link = href
                break
        if not link:
            link = (entry.findtext(f"{ns}link") or "").strip()

        d = _parse_datetime(published) or _parse_datetime(updated)
        brief = summary or content
        items.append(
            NewsItem(
                title=_strip_html(title) or title,
                brief=_truncate(_strip_html(brief), 220),
                url=link,
                source=source,
                published_at=_format_utc_iso(d) if d else None,
            )
        )
    return items


def _google_news_rss_url(q: str, *, hl: str, gl: str, ceid: str) -> str:
    base = "https://news.google.com/rss/search"
    qs = {
        "q": q,
        "hl": hl,
        "gl": gl,
        "ceid": ceid,
    }
    return base + "?" + urllib.parse.urlencode(qs)


class _AnchorTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_a = False
        self._cur_href: Optional[str] = None
        self._buf: List[str] = []
        self.anchors: List[Tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag.lower() != "a":
            return
        self._in_a = True
        self._cur_href = None
        self._buf = []
        for k, v in attrs:
            if k.lower() == "href" and v:
                self._cur_href = v

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a":
            return
        if self._in_a:
            text = re.sub(r"\s+", " ", "".join(self._buf)).strip()
            href = (self._cur_href or "").strip()
            if href and text:
                self.anchors.append((href, text))
        self._in_a = False
        self._cur_href = None
        self._buf = []

    def handle_data(self, data: str) -> None:
        if self._in_a and data:
            self._buf.append(data)


def _absolutize(base_url: str, href: str) -> str:
    try:
        return urllib.parse.urljoin(base_url, href)
    except Exception:
        return href


def _aibase_html_list_items(html_bytes: bytes, *, base_url: str, source: str) -> List[NewsItem]:
    # The list page contains anchors with combined text:
    # "标题 摘要 刚刚 6.1K"
    parser = _AnchorTextParser()
    try:
        parser.feed(html_bytes.decode("utf-8", errors="ignore"))
    except Exception:
        return []

    out: List[NewsItem] = []
    for href, text in parser.anchors:
        if "/zh/news/" not in href and "/news/" not in href:
            continue
        url = _absolutize(base_url, href)
        # Remove trailing “刚刚 / xx分钟前 / xx小时前 / xx天前” + views.
        cleaned = re.sub(r"\s+(刚刚|\d+\s*(分钟|小时|天)前)\s+[\d.]+[KkMm]?\s*$", "", text).strip()
        cleaned = re.sub(r"\s+[\d.]+[KkMm]?\s*$", "", cleaned).strip()
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if not cleaned:
            continue

        parts = cleaned.split(" ", 1)
        title = parts[0].strip()
        brief = parts[1].strip() if len(parts) > 1 else ""
        out.append(
            NewsItem(
                title=title,
                brief=_truncate(brief, 220),
                url=url,
                source=source,
            )
        )
    return out


class _AiBotDailyParser(HTMLParser):
    """
    Parse ai-bot.cn/daily-ai-news page into (title, brief, hrefs).

    Heuristic:
    - Treat <h2> as a new item boundary.
    - Capture the first <a href> inside the <h2> as candidate link (if any).
    - Use the first <p> following that <h2> as brief and also collect any <a href> inside.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: List[Dict[str, Any]] = []
        self._in_h2 = False
        self._in_p = False
        self._cur_title_buf: List[str] = []
        self._cur_p_buf: List[str] = []
        self._cur_links: List[str] = []
        self._seen_p_for_item = False

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        t = tag.lower()
        if t == "h2":
            # Each <h2> starts a new news item on ai-bot.cn/daily-ai-news
            self.items.append({"title": "", "brief": "", "links": []})
            self._in_h2 = True
            self._cur_title_buf = []
            self._cur_links = []
            self._seen_p_for_item = False
            return
        if t == "p" and self.items and not self._seen_p_for_item:
            self._in_p = True
            self._cur_p_buf = []
            return
        if t == "a":
            href = ""
            for k, v in attrs:
                if k.lower() == "href" and v:
                    href = v.strip()
                    break
            if href and self.items:
                if self._in_h2:
                    # Prefer the first link inside h2.
                    if not self._cur_links:
                        self._cur_links.append(href)
                elif self._in_p:
                    self._cur_links.append(href)

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t == "h2":
            self._in_h2 = False
            if not self.items:
                return
            title = re.sub(r"\s+", " ", "".join(self._cur_title_buf)).strip()
            self.items[-1]["title"] = title
            self.items[-1]["links"] = list(self._cur_links)
            self._cur_title_buf = []
            return
        if t == "p":
            if self._in_p:
                self._in_p = False
                self._seen_p_for_item = True
                if not self.items:
                    return
                brief = re.sub(r"\s+", " ", "".join(self._cur_p_buf)).strip()
                self.items[-1]["brief"] = brief
                # Merge links discovered inside <p>
                cur = self.items[-1].get("links") or []
                self.items[-1]["links"] = cur + [x for x in self._cur_links if x not in cur]
                self._cur_p_buf = []
                return

    def handle_data(self, data: str) -> None:
        if not data:
            return
        if self._in_h2:
            self._cur_title_buf.append(data)
        elif self._in_p:
            self._cur_p_buf.append(data)


def _pick_best_link(base_url: str, links: Sequence[str]) -> str:
    # Prefer external http(s) links first (many “daily” pages are curated summaries).
    base_netloc = urllib.parse.urlsplit(base_url).netloc.lower()
    abs_links = [_absolutize(base_url, l) for l in links if l]
    for l in abs_links:
        if l.startswith("http://") or l.startswith("https://"):
            netloc = urllib.parse.urlsplit(l).netloc.lower()
            if netloc and netloc != base_netloc:
                return l
    # Otherwise, return first absolute link if present.
    if abs_links:
        return abs_links[0]
    return base_url


def _ai_bot_daily_ai_news_items(html_bytes: bytes, *, base_url: str, source: str) -> List[NewsItem]:
    parser = _AiBotDailyParser()
    try:
        parser.feed(html_bytes.decode("utf-8", errors="ignore"))
    except Exception:
        return []

    out: List[NewsItem] = []
    for it in parser.items:
        title = (it.get("title") or "").strip()
        brief = (it.get("brief") or "").strip()
        links = it.get("links") or []
        if not title:
            continue
        url = _pick_best_link(base_url, links)
        out.append(
            NewsItem(
                title=_strip_html(title),
                brief=_truncate(_strip_html(brief), 220),
                url=url,
                source=source,
            )
        )
    return out


def _load_sources(path: str) -> Dict[str, Any]:
    data = json.loads(_read_text(path))
    if not isinstance(data, dict):
        raise ValueError("sources.json must be an object")
    return data


def _default_sources_path() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    # skills/ai-news-digest/scripts -> skills/ai-news-digest/references/sources.json
    return os.path.join(os.path.dirname(here), "references", "sources.json")


def _default_seen_path() -> str:
    here = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(os.path.dirname(here), ".cache", "seen.json")


def _load_seen(path: str) -> Dict[str, float]:
    try:
        raw = json.loads(_read_text(path))
        if not isinstance(raw, dict):
            return {}
        out: Dict[str, float] = {}
        for k, v in raw.items():
            if isinstance(k, str) and isinstance(v, (int, float)):
                out[k] = float(v)
        return out
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _save_seen(path: str, seen: Dict[str, float]) -> None:
    _write_text(path, json.dumps(seen, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _matches_keywords(title: str, brief: str, include: Sequence[str], exclude: Sequence[str]) -> bool:
    hay = (title + "\n" + brief).lower()
    if include:
        if not any(k.lower() in hay for k in include if k.strip()):
            return False
    if exclude:
        if any(k.lower() in hay for k in exclude if k.strip()):
            return False
    return True


def _split_keywords(values: Sequence[str]) -> List[str]:
    # Allow both repeatable args and comma-separated lists for convenience.
    out: List[str] = []
    for v in values:
        if not v:
            continue
        parts = [p.strip() for p in str(v).split(",")]
        out.extend([p for p in parts if p])
    return out


_DEFAULT_AI_INCLUDE = [
    "ai",
    "人工智能",
    "大模型",
    "llm",
    "openai",
    "anthropic",
    "claude",
    "gpt",
    "gemini",
    "deepseek",
    "agent",
]

_DEFAULT_NOISE_EXCLUDE = [
    # Reduce market/ETF noise in Google News results by default.
    "etf",
    "成交额",
]

_CHINA_LLM_ALLOW_KEYWORDS = [
    # Alibaba / Qwen
    "qwen",
    "tongyi",
    "通义",
    "千问",
    # Kimi / Moonshot AI
    "kimi",
    "moonshot",
    "月之暗面",
    # DeepSeek
    "deepseek",
    "深度求索",
]

_OVERSEAS_AI_CORE_KEYWORDS = [
    # US / global AI companies & products (keep all overseas news)
    "openai",
    "anthropic",
    "claude",
    "gpt",
    "chatgpt",
    "gemini",
    "google",
    "deepmind",
    "microsoft",
    "copilot",
    "nvidia",
    "meta",
    "xai",
    "spacex",
    "mistral",
    "cohere",
    "perplexity",
    "huggingface",
    "hugging face",
    "stability",
    "midjourney",
    "runway",
    # Chinese mentions of overseas brands
    "英伟达",
    "微软",
    "谷歌",
    "脸书",
    "马斯克",
]

_OVERSEAS_SECONDARY_KEYWORDS = [
    # Keep when clearly the subject of the title (avoid domestic "serves Apple" noise).
    "apple",
    "苹果",
    "amazon",
    "亚马逊",
    "aws",
    "tesla",
    "特斯拉",
    "oracle",
    "snowflake",
    "databricks",
    "intel",
    "amd",
    "ibm",
    "tsmc",
    "台积电",
    "samsung",
    "三星",
    "sk hynix",
    "海力士",
]


def _has_cjk(s: str) -> bool:
    return re.search(r"[\u4e00-\u9fff]", s or "") is not None


def _contains_any(haystack: str, needles: Sequence[str]) -> bool:
    h = (haystack or "").lower()
    for n in needles:
        if not n:
            continue
        if str(n).lower() in h:
            return True
    return False


def _china_llm_brand_is_prominent_in_title(title: str) -> bool:
    """
    Heuristic: treat the item as *about* Ali/Qwen, Kimi, or DeepSeek only when the
    brand appears early in the title (to avoid keeping unrelated domestic news that
    merely mentions the brand in passing).
    """
    t = (title or "").strip()
    if not t:
        return False
    low = t.lower()
    for k in _CHINA_LLM_ALLOW_KEYWORDS:
        kk = str(k).lower()
        idx = low.find(kk)
        if idx == -1:
            continue
        # Allow prefixes like "#1" / "【】" / punctuation; but still require early position.
        if idx <= 20:
            return True
    return False


def _keyword_is_prominent_in_title(title: str, keywords: Sequence[str], *, max_idx: int) -> bool:
    t = (title or "").strip()
    if not t:
        return False
    low = t.lower()
    for k in keywords:
        kk = str(k).lower()
        idx = low.find(kk)
        if idx == -1:
            continue
        if idx <= max_idx:
            return True
    return False


def _passes_china_llm_focus(title: str, brief: str, url: str) -> bool:
    """
    Focus filter:
    - Keep ALL overseas AI news.
    - For China domestic "LLM news", only keep: Alibaba/Qwen, Kimi, DeepSeek.

    This is heuristic (keyword-based) by design.
    """
    title_text = title or ""
    body_text = (title or "") + "\n" + (brief or "")

    # If it's clearly overseas (brand keywords in TITLE) => keep.
    # Using title only avoids keeping domestic noise that just mentions e.g. "Apple" in the brief.
    if _contains_any(title_text, _OVERSEAS_AI_CORE_KEYWORDS):
        return True
    if _keyword_is_prominent_in_title(title_text, _OVERSEAS_SECONDARY_KEYWORDS, max_idx=12):
        return True

    # If the item is non-Chinese (likely overseas) => keep.
    if not _has_cjk(body_text):
        return True

    # Otherwise, treat as domestic and only keep whitelisted LLM brands.
    return _china_llm_brand_is_prominent_in_title(title_text)


def _local_tz() -> dt.tzinfo:
    return dt.datetime.now().astimezone().tzinfo or dt.timezone.utc


def _today_window_local() -> Tuple[dt.datetime, dt.datetime, dt.tzinfo]:
    tz = _local_tz()
    now = dt.datetime.now(tz)
    start = dt.datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=tz)
    end = start + dt.timedelta(days=1)
    return start, end, tz


def _is_in_local_day(d_utc: dt.datetime, *, start_local: dt.datetime, end_local: dt.datetime) -> bool:
    d_local = d_utc.astimezone(start_local.tzinfo)
    return start_local <= d_local < end_local


def _extract_published_time_from_html(html_bytes: bytes, *, default_tz: dt.tzinfo) -> Optional[dt.datetime]:
    """
    Best-effort publish-time extraction for pages that don't expose dates in RSS lists.
    Returns UTC datetime when found, else None.
    """
    s = html_bytes.decode("utf-8", errors="ignore")

    # meta: article:published_time / og:updated_time / publishdate / pubdate / date
    meta_patterns = [
        r'property=["\']article:published_time["\']\s+content=["\']([^"\']+)["\']',
        r'name=["\']publishdate["\']\s+content=["\']([^"\']+)["\']',
        r'name=["\']pubdate["\']\s+content=["\']([^"\']+)["\']',
        r'name=["\']date["\']\s+content=["\']([^"\']+)["\']',
        r'property=["\']og:updated_time["\']\s+content=["\']([^"\']+)["\']',
    ]

    candidates: List[str] = []
    for pat in meta_patterns:
        m = re.search(pat, s, flags=re.IGNORECASE)
        if m:
            candidates.append(m.group(1).strip())

    # <time datetime="...">
    m = re.search(r"<time[^>]+datetime=[\"']([^\"']+)[\"']", s, flags=re.IGNORECASE)
    if m:
        candidates.append(m.group(1).strip())

    # Inline date near the top: YYYY-MM-DD or YYYY/MM/DD (best-effort).
    m = re.search(r"(\d{4}[-/]\d{1,2}[-/]\d{1,2})", s[:12000])
    if m:
        candidates.append(m.group(1).strip())

    for cand in candidates:
        d = _parse_datetime_with_default_tz(cand, default_tz=default_tz)
        if d is not None:
            return d
    return None


def _resolve_published_at(
    url: str,
    *,
    timeout_s: float,
    user_agent: str,
    default_tz: dt.tzinfo,
) -> Optional[dt.datetime]:
    try:
        body = _fetch(url, timeout_s=timeout_s, user_agent=user_agent)
    except Exception:
        return None
    return _extract_published_time_from_html(body, default_tz=default_tz)


def _fetch_from_source(
    spec: Dict[str, Any],
    *,
    timeout_s: float,
    user_agent: str,
    google_queries: Sequence[str],
    verbose: bool,
) -> List[NewsItem]:
    stype = (spec.get("type") or "").strip()
    name = (spec.get("name") or stype or "source").strip()

    def log(msg: str) -> None:
        if verbose:
            print(f"[{name}] {msg}", file=sys.stderr)

    if stype == "rss":
        url = (spec.get("url") or "").strip()
        if not url:
            return []
        log(f"fetch {url}")
        body = _fetch(url, timeout_s=timeout_s, user_agent=user_agent)
        return _rss_or_atom_items(body, source=name)

    if stype == "google_news_rss_search":
        hl = (spec.get("hl") or "zh-CN").strip()
        gl = (spec.get("gl") or "CN").strip()
        ceid = (spec.get("ceid") or "CN:zh-Hans").strip()

        queries = list(spec.get("queries") or [])
        # Allow CLI overrides; if present, prefer them.
        if google_queries:
            queries = list(google_queries)

        out: List[NewsItem] = []
        for q in queries:
            q = str(q).strip()
            if not q:
                continue
            url = _google_news_rss_url(q, hl=hl, gl=gl, ceid=ceid)
            log(f"fetch {url}")
            body = _fetch(url, timeout_s=timeout_s, user_agent=user_agent)
            # Keep source stable so we can balance by source later.
            out.extend(_rss_or_atom_items(body, source=name))
        return out

    if stype == "aibase_html_list":
        url = (spec.get("url") or "").strip()
        if not url:
            return []
        log(f"fetch {url}")
        body = _fetch(url, timeout_s=timeout_s, user_agent=user_agent)
        return _aibase_html_list_items(body, base_url=url, source=name)

    if stype == "ai_bot_daily_ai_news":
        url = (spec.get("url") or "").strip()
        if not url:
            return []
        log(f"fetch {url}")
        body = _fetch(url, timeout_s=timeout_s, user_agent=user_agent)
        return _ai_bot_daily_ai_news_items(body, base_url=url, source=name)

    return []


def _sort_items(items: List[NewsItem]) -> List[NewsItem]:
    # Prefer items with published_at; sort newest-first. Fallback: keep stable order.
    with_ts: List[Tuple[float, NewsItem]] = []
    without_ts: List[NewsItem] = []
    for it in items:
        if not it.published_at:
            without_ts.append(it)
            continue
        d = _parse_datetime(it.published_at)
        if d is None:
            without_ts.append(it)
            continue
        with_ts.append((d.timestamp(), it))

    with_ts.sort(key=lambda x: x[0], reverse=True)
    return [it for _, it in with_ts] + without_ts


def _render_md(items: Sequence[NewsItem]) -> str:
    lines: List[str] = []
    for i, it in enumerate(items, 1):
        title = it.title.strip() or "(untitled)"
        brief = it.brief.strip()
        url = it.url.strip()
        if brief:
            lines.append(f"{i}. {title} — {brief} ({url})")
        else:
            lines.append(f"{i}. {title} ({url})")
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def _render_json(items: Sequence[NewsItem]) -> str:
    payload = [
        {
            "title": it.title,
            "brief": it.brief,
            "url": it.url,
            "source": it.source,
            "published_at": it.published_at,
        }
        for it in items
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description="AI news digest (dedupe; title/brief/link).")
    p.add_argument("--sources", default=_default_sources_path(), help="Path to sources.json")
    p.add_argument("--max-items", type=int, default=100, help="Max output items (0 = no limit)")
    p.add_argument("--format", choices=["md", "json"], default="md", help="Output format")
    p.add_argument("--timeout", type=float, default=12.0, help="Per-request timeout (seconds)")
    p.add_argument("--user-agent", default="Mozilla/5.0 (ai-news-digest; +https://example.invalid)", help="HTTP User-Agent")
    p.add_argument("--source", action="append", default=[], help="Only run a specific source by name (repeatable)")
    p.add_argument("--include", action="append", default=[], help="Include keyword filter (repeatable)")
    p.add_argument("--exclude", action="append", default=[], help="Exclude keyword filter (repeatable)")
    p.add_argument(
        "--no-default-ai-filter",
        action="store_true",
        help="Disable default AI keyword filter (when --include is not provided)",
    )
    p.add_argument(
        "--no-default-noise-filter",
        action="store_true",
        help="Disable default noise exclude filter (when --exclude is not provided)",
    )
    p.add_argument(
        "--no-china-llm-focus",
        action="store_true",
        help="Disable China LLM focus filter (default keeps overseas; domestic only keeps Ali/Qwen, Kimi, DeepSeek)",
    )
    p.add_argument("--google-query", action="append", default=[], help="Override Google News queries (repeatable)")
    p.add_argument("--seen-file", default=_default_seen_path(), help="Seen-cache JSON path")
    p.add_argument("--fresh", action="store_true", help="Ignore existing seen cache for this run")
    p.add_argument("--no-seen-update", action="store_true", help="Do not write back to seen cache")
    p.add_argument("--today", action="store_true", help="Only keep items published today (local timezone)")
    p.add_argument(
        "--no-resolve-missing-dates",
        action="store_true",
        help="With --today, do not fetch article pages to infer publish date when missing",
    )
    p.add_argument(
        "--max-date-resolve",
        type=int,
        default=20,
        help="With --today, max number of items to resolve publish dates for (default: 20)",
    )
    p.add_argument("--output", default="", help="Write output to a file (otherwise stdout)")
    p.add_argument("--verbose", action="store_true", help="Verbose logs to stderr")
    args = p.parse_args(argv)

    sources_path = os.path.abspath(args.sources)
    sources_cfg = _load_sources(sources_path)
    specs = sources_cfg.get("sources") or []
    if not isinstance(specs, list):
        raise SystemExit("sources.json: sources must be a list")

    enabled_specs: List[Dict[str, Any]] = []
    only = set([s.strip() for s in args.source if s.strip()])
    for s in specs:
        if not isinstance(s, dict):
            continue
        if not s.get("enabled", True):
            continue
        name = (s.get("name") or "").strip()
        if only and name not in only:
            continue
        enabled_specs.append(s)

    include = _split_keywords(args.include)
    exclude = _split_keywords(args.exclude)
    if not include and not args.no_default_ai_filter:
        include = list(_DEFAULT_AI_INCLUDE)
    if not exclude and not args.no_default_noise_filter:
        exclude = list(_DEFAULT_NOISE_EXCLUDE)
    focus_enabled = not args.no_china_llm_focus

    seen: Dict[str, float] = {}
    if not args.fresh:
        seen = _load_seen(args.seen_file)

    start_local, end_local, local_tz = _today_window_local() if args.today else (None, None, None)
    resolve_budget_box = [int(args.max_date_resolve or 0)]
    resolved_cache: Dict[str, Optional[str]] = {}
    resolve_missing = args.today and (not args.no_resolve_missing_dates)

    # Fetch per source, normalize/filter, then select in a balanced (round-robin) way.
    grouped: Dict[str, List[NewsItem]] = {}
    group_order: List[str] = []
    for spec in enabled_specs:
        name = (spec.get("name") or spec.get("type") or "source").strip()
        if name and name not in grouped:
            grouped[name] = []
            group_order.append(name)
        items = _fetch_from_source(
            spec,
            timeout_s=args.timeout,
            user_agent=args.user_agent,
            google_queries=args.google_query,
            verbose=args.verbose,
        )
        for it in items:
            title = (it.title or "").strip()
            url = (it.url or "").strip()
            if not title or not url:
                continue
            it2 = NewsItem(
                title=_strip_html(title),
                brief=_truncate(_strip_html(it.brief), 220),
                url=_normalize_url(url),
                source=name,
                published_at=it.published_at,
            )
            if not _matches_keywords(it2.title, it2.brief, include, exclude):
                continue
            if focus_enabled and not _passes_china_llm_focus(it2.title, it2.brief, it2.url):
                continue
            grouped.setdefault(name, []).append(it2)

    # Sort each group (newest first when publish timestamps exist).
    for k in list(grouped.keys()):
        grouped[k] = _sort_items(grouped[k])

    def select_once(*, allow_seen: bool) -> List[NewsItem]:
        local: Dict[str, List[NewsItem]] = {k: list(v) for k, v in grouped.items()}
        out: List[NewsItem] = []
        used_keys: set[str] = set()
        used_titles: set[str] = set()

        while True:
            progressed = False
            for g in group_order:
                lst = local.get(g) or []
                if not lst:
                    continue
                # Pop until we find an item that passes dedupe (and optionally seen).
                while lst:
                    it = lst.pop(0)
                    if args.today:
                        d = _parse_datetime(it.published_at or "")
                        if d is None and resolve_missing and resolve_budget_box[0] > 0:
                            nk = _normalize_url(it.url)
                            if nk in resolved_cache:
                                pub_iso = resolved_cache[nk]
                                d = _parse_datetime(pub_iso or "")
                            else:
                                d2 = _resolve_published_at(
                                    it.url,
                                    timeout_s=args.timeout,
                                    user_agent=args.user_agent,
                                    default_tz=local_tz or dt.timezone.utc,
                                )
                                pub_iso = _format_utc_iso(d2) if d2 else None
                                resolved_cache[nk] = pub_iso
                                d = d2
                                resolve_budget_box[0] -= 1
                            if pub_iso:
                                it = NewsItem(
                                    title=it.title,
                                    brief=it.brief,
                                    url=it.url,
                                    source=it.source,
                                    published_at=pub_iso,
                                )
                        if d is None:
                            continue
                        if not _is_in_local_day(d, start_local=start_local, end_local=end_local):  # type: ignore[arg-type]
                            continue

                    k = it.dedupe_key()
                    tk = _normalize_title(it.title)
                    if k in used_keys:
                        continue
                    if tk and tk in used_titles:
                        continue
                    if not allow_seen and (not args.fresh) and k in seen:
                        continue
                    used_keys.add(k)
                    if tk:
                        used_titles.add(tk)
                    out.append(it)
                    progressed = True
                    break
                local[g] = lst
                if args.max_items > 0 and len(out) >= args.max_items:
                    return out
            if not progressed:
                return out

    ts = time.time()
    out = select_once(allow_seen=False)
    if args.max_items > 0 and len(out) < min(10, args.max_items) and not args.fresh:
        # If we got too few items due to seen-cache, allow seen items as a fallback.
        out = select_once(allow_seen=True)

    if not args.no_seen_update:
        for it in out:
            seen[it.dedupe_key()] = ts
        _save_seen(args.seen_file, seen)

    if args.format == "json":
        rendered = _render_json(out)
    else:
        rendered = _render_md(out)

    if args.output:
        _write_text(args.output, rendered)
    else:
        sys.stdout.write(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
