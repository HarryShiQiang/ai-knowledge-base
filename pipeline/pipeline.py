#!/usr/bin/env python3
"""四步知识库自动化流水线。

Steps:
    1. Collect  — 从 GitHub Search API / RSS 源采集 AI 相关内容
    2. Analyze  — 调用 LLM 对每条内容进行摘要/评分/标签分析
    3. Organize — 去重、格式标准化、校验
    4. Save     — 将文章保存为独立 JSON 文件到 knowledge/articles/

Usage:
    python pipeline/pipeline.py --sources github,rss --limit 20
    python pipeline/pipeline.py --sources github --limit 5
    python pipeline/pipeline.py --sources rss --limit 10
    python pipeline/pipeline.py --sources github --limit 5 --dry-run
    python pipeline/pipeline.py --verbose
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from model_client import chat_with_retry, create_provider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 路径常量
# ---------------------------------------------------------------------------

_BASE_DIR = Path(__file__).resolve().parent.parent
_KNOWLEDGE_RAW = _BASE_DIR / "knowledge" / "raw"
_KNOWLEDGE_ARTICLES = _BASE_DIR / "knowledge" / "articles"
_RSS_SOURCES_FILE = Path(__file__).resolve().parent / "rss_sources.yaml"

# ---------------------------------------------------------------------------
# 采集常量
# ---------------------------------------------------------------------------

_GITHUB_SEARCH_URL = "https://api.github.com/search/repositories"
_GITHUB_SEARCH_QUERY = "ai OR llm OR agent OR rag OR nlp OR transformer"
_GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
_REQUEST_TIMEOUT = 30.0

# AI 关键词 — 用于 RSS 条目初筛
_AI_KEYWORDS: tuple[str, ...] = (
    "ai", "llm", "agent", "rag", "machine learning", "deep learning",
    "generative ai", "nlp", "transformer", "fine-tuning", "gpt",
    "open source llm", "langchain", "llama", "claude", "openai",
    "artificial intelligence", "neural network", "prompt engineering",
)

# ---------------------------------------------------------------------------
# LLM 分析常量
# ---------------------------------------------------------------------------

_LLM_MAX_TOKENS = 2048
_LLM_TEMPERATURE = 0.3
_LLM_CONCURRENCY = 3

_ANALYSIS_SYSTEM_PROMPT = """\
You are an AI technology analyst. Analyze the given article/repository and produce a structured JSON output.

Rules:
- summary: English summary, ≤200 characters, capture the core value proposition.
- chinese_summary: Chinese summary, ≤200 characters.
- tags: 2-6 lowercase tags selected from this pool: [agent, llm, rag, multimodal, fine-tuning, open-source, deployment, training, search, tool-use, prompt-engineering, orchestration, code-generation, chatbot, document-processing, deep-research, autonomous-agent, function-calling, nlp, vision, token-optimization, agentic-framework, multi-agent]
- difficulty: "beginner" for tutorials/intros, "medium" for most projects, "advanced" for research internals.
- score: 1-10 float rating. Distribution: most 5-8, exceptional 9-10, poor 1-4.

Output ONLY valid JSON, no markdown fences, no extra text:
{"summary": "...", "chinese_summary": "...", "tags": ["tag1", "tag2"], "difficulty": "medium", "score": 7.5}"""

_ANALYSIS_USER_TEMPLATE = """\
Title: {title}
URL: {url}
Description: {description}

Analyze this and output JSON."""


# ===================================================================
# 工具函数
# ===================================================================


def _slugify(text: str) -> str:
    """将文本转换为 URL 友好的 slug。

    Args:
        text: 原始文本。

    Returns:
        小写、仅含字母数字和连字符的 slug。
    """
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[-\s]+", "-", text)
    return text.strip("-")


def _extract_repo_slug(url: str) -> str:
    """从 GitHub URL 提取 owner/repo 并转为 slug。

    Args:
        url: GitHub 仓库 URL。

    Returns:
        owner-repo 的小写形式，无法提取时返回空字符串。
    """
    match = re.search(r"github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$", url)
    if match:
        return f"{match.group(1)}-{match.group(2)}".lower()
    return ""


def _strip_html(text: str) -> str:
    """去除 HTML 标签并解码常见实体。

    Args:
        text: 可能包含 HTML 的文本。

    Returns:
        纯文本。
    """
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&#39;", "'").replace("&apos;", "'")
    return text


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 8601 字符串。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ===================================================================
# Step 1: Collect
# ===================================================================


async def _collect_github(limit: int) -> list[dict[str, Any]]:
    """从 GitHub Search API 采集 AI 相关热门仓库。

    Args:
        limit: 最大获取数量（≤100）。

    Returns:
        采集条目列表，每项含 title / url / source / popularity / summary。
    """
    params: dict[str, str | int] = {
        "q": _GITHUB_SEARCH_QUERY,
        "sort": "stars",
        "order": "desc",
        "per_page": min(limit, 100),
    }
    headers: dict[str, str] = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "ai-knowledge-base/1.0",
    }
    if _GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {_GITHUB_TOKEN}"

    logger.info("Fetching GitHub Search API (limit=%d)", limit)

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=True) as client:
        resp = await client.get(_GITHUB_SEARCH_URL, params=params, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    items: list[dict[str, Any]] = []
    for repo in data.get("items", []):
        description = repo.get("description")
        items.append({
            "title": repo.get("full_name", ""),
            "url": repo.get("html_url", ""),
            "source": "github_trending",
            "popularity": repo.get("stargazers_count", 0),
            "summary": description if description else "",
        })

    logger.info("Collected %d GitHub repos", len(items))
    return items


async def _collect_rss(limit: int) -> list[dict[str, Any]]:
    """从 rss_sources.yaml 中配置的 RSS 源采集 AI 相关内容。

    Args:
        limit: 所有 RSS 源合计的最大条目数。

    Returns:
        采集条目列表。
    """
    if not _RSS_SOURCES_FILE.exists():
        logger.warning("RSS sources file not found: %s", _RSS_SOURCES_FILE)
        return []

    try:
        import yaml
    except ImportError:
        logger.error("pyyaml 未安装，请执行: pip install pyyaml")
        return []

    sources_config = yaml.safe_load(_RSS_SOURCES_FILE.read_text(encoding="utf-8"))
    sources: list[dict[str, Any]] = sources_config.get("sources", [])

    enabled_sources = [s for s in sources if s.get("enabled", True)]
    if not enabled_sources:
        logger.warning("No enabled RSS sources found")
        return []

    per_feed_limit = max(1, limit // len(enabled_sources))
    all_items: list[dict[str, Any]] = []

    for src in enabled_sources:
        try:
            feed_items = await _fetch_rss_feed(src, per_feed_limit)
            all_items.extend(feed_items)
        except Exception:
            logger.warning("Failed to fetch RSS feed: %s", src.get("name", ""), exc_info=True)

    ai_items = _filter_ai_relevant(all_items)
    ai_items.sort(key=lambda x: x.get("popularity", 0), reverse=True)

    logger.info("Collected %d RSS items (filtered from %d total)", len(ai_items), len(all_items))
    return ai_items[:limit]


async def _fetch_rss_feed(
    source: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    """抓取并解析单个 RSS/Atom feed。

    Args:
        source: rss_sources.yaml 中的源配置。
        limit: 该源的最大条目数。

    Returns:
        解析后的条目列表。
    """
    url = source["url"]
    category = source.get("category", "general_tech")

    async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT, follow_redirects=True) as client:
        try:
            resp = await client.get(url, headers={"User-Agent": "ai-knowledge-base/1.0"})
            resp.raise_for_status()
            text = resp.text
        except httpx.DecodingError:
            logger.debug("Brotli decode error for %s, retrying without brotli", url)
            resp = await client.get(
                url,
                headers={
                    "User-Agent": "ai-knowledge-base/1.0",
                    "Accept-Encoding": "gzip, deflate",
                },
            )
            resp.raise_for_status()
            text = resp.text

    items = _parse_rss_xml(text)
    for item in items:
        item["source"] = "rss"
        item["source_name"] = source.get("name", "")
        item["category"] = category
        item["popularity"] = 0
        item["summary"] = (item.get("description", "") or "")[:200]

    return items[:limit]


def _parse_rss_xml(xml_text: str) -> list[dict[str, Any]]:
    """用简易正则解析 RSS 2.0 / Atom XML。

    同时支持 <item> (RSS 2.0) 和 <entry> (Atom) 两种格式。

    Args:
        xml_text: 原始 XML 字符串。

    Returns:
        解析后的条目列表，每项含 title / url / description / pub_date。
    """
    items: list[dict[str, Any]] = []

    # RSS 2.0 <item> 块
    for match in re.finditer(r"<item>(.*?)</item>", xml_text, re.DOTALL | re.IGNORECASE):
        block = match.group(1)
        item = _parse_rss_item_block(block)
        if item.get("title") and item.get("url"):
            items.append(item)

    # Atom <entry> 块
    for match in re.finditer(r"<entry>(.*?)</entry>", xml_text, re.DOTALL | re.IGNORECASE):
        block = match.group(1)
        item = _parse_atom_entry_block(block)
        if item.get("title") and item.get("url"):
            # 避免与 RSS 解析结果重复
            if not any(e.get("url") == item["url"] for e in items):
                items.append(item)

    return items


def _parse_rss_item_block(block: str) -> dict[str, Any]:
    """解析单个 RSS 2.0 <item> 块。"""
    item: dict[str, Any] = {}

    m = re.search(r"<title>(.*?)</title>", block, re.DOTALL | re.IGNORECASE)
    if m:
        item["title"] = _strip_html(m.group(1)).strip()

    m = re.search(r"<link>(.*?)</link>", block, re.DOTALL | re.IGNORECASE)
    if m:
        item["url"] = m.group(1).strip()

    m = re.search(r"<description>(.*?)</description>", block, re.DOTALL | re.IGNORECASE)
    if m:
        item["description"] = _strip_html(m.group(1)).strip()[:500]

    m = re.search(r"<pubDate>(.*?)</pubDate>", block, re.DOTALL | re.IGNORECASE)
    if m:
        item["pub_date"] = m.group(1).strip()

    return item


def _parse_atom_entry_block(block: str) -> dict[str, Any]:
    """解析单个 Atom <entry> 块。"""
    item: dict[str, Any] = {}

    m = re.search(r"<title[^>]*>(.*?)</title>", block, re.DOTALL | re.IGNORECASE)
    if m:
        item["title"] = _strip_html(m.group(1)).strip()

    m = re.search(r'<link[^>]*href="([^"]+)"', block, re.IGNORECASE)
    if m:
        item["url"] = m.group(1).strip()

    m = re.search(r"<summary[^>]*>(.*?)</summary>", block, re.DOTALL | re.IGNORECASE)
    if m:
        item["description"] = _strip_html(m.group(1)).strip()[:500]

    m = re.search(
        r"<(?:published|updated)>(.*?)</(?:published|updated)>",
        block, re.DOTALL | re.IGNORECASE,
    )
    if m:
        item["pub_date"] = m.group(1).strip()

    return item


def _filter_ai_relevant(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """根据 AI 关键词过滤条目。

    Args:
        items: 待过滤条目列表。

    Returns:
        匹配 AI 关键词的条目列表。
    """
    relevant: list[dict[str, Any]] = []
    for item in items:
        text = " ".join([
            item.get("title", ""),
            item.get("summary", ""),
            item.get("description", ""),
        ]).lower()
        if any(kw in text for kw in _AI_KEYWORDS):
            relevant.append(item)
    return relevant


async def _collect(sources: list[str], limit: int) -> list[dict[str, Any]]:
    """Step 1: 从指定来源采集内容。

    Args:
        sources: 来源标识列表 ('github' / 'rss')。
        limit: 每个来源的最大条目数。

    Returns:
        合并去重后的采集条目列表。
    """
    tasks: list[asyncio.Task[list[dict[str, Any]]]] = []
    if "github" in sources:
        tasks.append(asyncio.create_task(_collect_github(limit)))
    if "rss" in sources:
        tasks.append(asyncio.create_task(_collect_rss(limit)))

    if not tasks:
        logger.warning("No valid sources to collect from")
        return []

    results = await asyncio.gather(*tasks)

    seen_urls: set[str] = set()
    unique: list[dict[str, Any]] = []
    for batch in results:
        for item in batch:
            url = item.get("url", "")
            if url and url not in seen_urls:
                seen_urls.add(url)
                unique.append(item)

    logger.info("Step 1 complete: %d unique items collected", len(unique))
    return unique


def _save_raw(items: list[dict[str, Any]], source: str) -> Path | None:
    """将采集的原始数据保存到 knowledge/raw/。

    Args:
        items: 采集条目列表。
        source: 来源标识，用于文件名。

    Returns:
        保存的文件路径，目录创建失败时返回 None。
    """
    try:
        _KNOWLEDGE_RAW.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("Failed to create raw directory: %s", _KNOWLEDGE_RAW)
        return None

    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    filepath = _KNOWLEDGE_RAW / f"{source}-{today}.json"

    payload = {
        "source": source,
        "collected_at": _now_iso(),
        "count": len(items),
        "items": items,
    }
    filepath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Raw data saved to %s", filepath)
    return filepath


# ===================================================================
# Step 2: Analyze
# ===================================================================


async def _analyze_item(
    item: dict[str, Any],
    provider: Any,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any] | None:
    """用 LLM 分析单条采集条目。

    Args:
        item: 采集条目。
        provider: LLM 提供商实例。
        semaphore: 并发控制信号量。

    Returns:
        分析后的文章字典，失败时返回 None。
    """
    async with semaphore:
        title = item.get("title", "")
        url = item.get("url", "")
        description = item.get("summary") or item.get("description") or ""

        user_prompt = _ANALYSIS_USER_TEMPLATE.format(
            title=title,
            url=url,
            description=description,
        )

        try:
            response = await chat_with_retry(
                messages=[
                    {"role": "system", "content": _ANALYSIS_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=_LLM_MAX_TOKENS,
                temperature=_LLM_TEMPERATURE,
                provider=provider,
            )
            content = response.content.strip()

            json_match = re.search(r"\{[\s\S]*\}", content)
            if json_match:
                content = json_match.group(0)

            analysis = json.loads(content)

            article: dict[str, Any] = {
                "title": title,
                "source_url": url,
                "source_type": item.get("source", ""),
                "summary": str(analysis.get("summary", "")),
                "chinese_summary": str(analysis.get("chinese_summary", "")),
                "tags": _normalize_tags(analysis.get("tags", [])),
                "difficulty": str(analysis.get("difficulty", "medium")),
                "score": _clamp_score(analysis.get("score", 5.0)),
            }
            logger.debug("Analyzed: %s (score=%.1f)", title[:60], article["score"])
            return article
        except (json.JSONDecodeError, KeyError) as exc:
            logger.warning("LLM response parse error for %s: %s", title[:60], exc)
            return None
        except Exception:
            logger.warning("Analysis failed for %s", title[:60], exc_info=True)
            return None


def _normalize_tags(tags: list[Any]) -> list[str]:
    """规范化标签列表：确保均为小写字符串、去重。

    Args:
        tags: 原始标签列表。

    Returns:
        规范化后的标签列表，最多 6 个。
    """
    result: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if not isinstance(tag, str):
            tag = str(tag)
        tag = tag.strip().lower()
        if tag and tag not in seen:
            seen.add(tag)
            result.append(tag)
    return result[:6]


def _clamp_score(score: Any) -> float:
    """将评分限制在 1.0-10.0 范围内。

    Args:
        score: 原始评分值。

    Returns:
        限制后的浮点评分。
    """
    try:
        value = float(score)
    except (TypeError, ValueError):
        return 5.0
    return max(1.0, min(10.0, value))


async def _analyze(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Step 2: 用 LLM 分析所有采集条目。

    Args:
        items: 采集条目列表。

    Returns:
        分析后的文章字典列表。
    """
    if not items:
        logger.warning("No items to analyze")
        return []

    provider = create_provider()
    semaphore = asyncio.Semaphore(_LLM_CONCURRENCY)

    tasks = [_analyze_item(item, provider, semaphore) for item in items]
    results = await asyncio.gather(*tasks)

    articles = [r for r in results if r is not None]
    logger.info("Step 2 complete: %d/%d items analyzed", len(articles), len(items))
    return articles


# ===================================================================
# Step 3: Organize
# ===================================================================


def _load_existing_articles() -> dict[str, dict[str, Any]]:
    """加载 knowledge/articles/ 中已有的文章，按 source_url 索引。

    Returns:
        source_url -> 文章数据的映射。
    """
    existing: dict[str, dict[str, Any]] = {}
    if not _KNOWLEDGE_ARTICLES.exists():
        return existing

    for filepath in _KNOWLEDGE_ARTICLES.glob("*.json"):
        try:
            article = json.loads(filepath.read_text(encoding="utf-8"))
            url = article.get("source_url", "")
            if url:
                existing[url] = article
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to read existing article: %s", filepath.name)

    return existing


def _generate_id(source_type: str, url: str, title: str) -> str:
    """生成唯一文章 ID。

    格式: YYYYMMDD-{source_prefix}-{slug}

    Args:
        source_type: 来源类型 (github_trending / rss / hackernews)。
        url: 原文 URL。
        title: 文章标题。

    Returns:
        生成的 ID 字符串。
    """
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    source_map: dict[str, str] = {
        "github_trending": "gh",
        "hackernews": "hn",
    }
    source_prefix = source_map.get(source_type, source_type[:2].lower())

    slug = ""
    if "github.com" in url:
        slug = _extract_repo_slug(url)
    if not slug:
        slug = _slugify(title)[:40]
    if not slug:
        slug = "unknown"

    return f"{today}-{source_prefix}-{slug}"


def _validate_article(article: dict[str, Any]) -> list[str]:
    """校验文章字段完整性与合法性。

    Args:
        article: 待校验的文章字典。

    Returns:
        错误消息列表（空列表表示通过）。
    """
    errors: list[str] = []

    required: dict[str, type] = {
        "id": str,
        "title": str,
        "source_url": str,
        "source_type": str,
        "summary": str,
        "tags": list,
        "status": str,
    }
    for field, expected_type in required.items():
        if field not in article:
            errors.append(f"Missing required field: {field!r}")
        elif not isinstance(article[field], expected_type):
            actual = type(article[field]).__name__
            errors.append(f"Field {field!r}: expected {expected_type.__name__}, got {actual}")

    status = article.get("status", "")
    if status not in ("draft", "review", "published", "archived"):
        errors.append(f"Invalid status: {status!r}")

    tags = article.get("tags", [])
    if not isinstance(tags, list) or len(tags) < 1:
        errors.append("Tags must be a non-empty list")

    score = article.get("score")
    if score is not None:
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            errors.append(f"Score must be int or float, got {type(score).__name__}")
        elif not (1 <= score <= 10):
            errors.append(f"Score {score} out of range [1, 10]")

    return errors


def _is_duplicate_24h(url: str, existing: dict[str, dict[str, Any]]) -> bool:
    """检查 URL 是否在 24 小时内已被采集。

    Args:
        url: 原文 URL。
        existing: 已有文章索引。

    Returns:
        True 表示需要跳过。
    """
    if url not in existing:
        return False

    fetched_str = existing[url].get("fetched_at", "")
    if not fetched_str:
        return False

    try:
        fetched_dt = datetime.fromisoformat(fetched_str.replace("Z", "+00:00"))
        hours_ago = (datetime.now(timezone.utc) - fetched_dt).total_seconds() / 3600
        return hours_ago < 24
    except (ValueError, TypeError):
        return False


def _organize(
    articles: list[dict[str, Any]],
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Step 3: 去重、格式标准化、校验。

    Args:
        articles: 分析后的文章列表。
        dry_run: 干跑模式，校验失败不丢弃条目。

    Returns:
        可保存的最终文章列表。
    """
    existing = _load_existing_articles()
    final: list[dict[str, Any]] = []
    skipped_dup = 0
    skipped_invalid = 0

    for article in articles:
        url = article.get("source_url", "")

        if _is_duplicate_24h(url, existing):
            logger.debug("Skipping duplicate (within 24h): %s", url)
            skipped_dup += 1
            continue

        article_id = _generate_id(
            article.get("source_type", ""),
            url,
            article.get("title", ""),
        )

        final_article: dict[str, Any] = {
            "id": article_id,
            "title": article.get("title", ""),
            "source_url": url,
            "source_type": article.get("source_type", ""),
            "summary": article.get("summary", ""),
            "tags": article.get("tags", []),
            "chinese_summary": article.get("chinese_summary", ""),
            "difficulty": article.get("difficulty", "medium"),
            "published_at": _now_iso(),
            "fetched_at": _now_iso(),
            "status": "published",
            "distributed_to": [],
            "score": article.get("score", 5.0),
        }

        errors = _validate_article(final_article)
        if errors:
            skipped_invalid += 1
            logger.warning("Validation errors for %s: %s", url, "; ".join(errors))
            if not dry_run:
                continue

        final.append(final_article)

    logger.info(
        "Step 3 complete: %d articles (skipped_dup=%d, skipped_invalid=%d)",
        len(final), skipped_dup, skipped_invalid,
    )
    return final


# ===================================================================
# Step 4: Save
# ===================================================================


def _save_articles(
    articles: list[dict[str, Any]],
    dry_run: bool = False,
) -> list[Path]:
    """Step 4: 将文章保存为独立 JSON 文件。

    Args:
        articles: 最终文章列表。
        dry_run: 干跑模式，仅打印不写入。

    Returns:
        保存（或即将保存）的文件路径列表。
    """
    try:
        _KNOWLEDGE_ARTICLES.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.error("Failed to create articles directory: %s", _KNOWLEDGE_ARTICLES)
        return []

    saved: list[Path] = []
    for article in articles:
        filepath = _KNOWLEDGE_ARTICLES / f"{article['id']}.json"

        if dry_run:
            logger.info("[DRY-RUN] Would save: %s", filepath.name)
            saved.append(filepath)
            continue

        try:
            filepath.write_text(
                json.dumps(article, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            saved.append(filepath)
            logger.debug("Saved: %s", filepath.name)
        except OSError:
            logger.exception("Failed to save: %s", filepath.name)

    logger.info("Step 4 complete: %d articles saved", len(saved))
    return saved


# ===================================================================
# CLI & Main
# ===================================================================


def _setup_logging(verbose: bool) -> None:
    """配置日志系统。

    Args:
        verbose: True 时启用 DEBUG 级别。
    """
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。

    Args:
        argv: 参数列表，默认使用 sys.argv[1:]。

    Returns:
        解析后的命名空间。
    """
    parser = argparse.ArgumentParser(
        description="AI Knowledge Base Pipeline — 四步自动化流水线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python pipeline/pipeline.py --sources github,rss --limit 20
  python pipeline/pipeline.py --sources github --limit 5
  python pipeline/pipeline.py --sources rss --limit 10
  python pipeline/pipeline.py --sources github --limit 5 --dry-run
  python pipeline/pipeline.py --verbose""",
    )
    parser.add_argument(
        "--sources",
        type=str,
        default="github,rss",
        help="逗号分隔的来源: github, rss (默认: github,rss)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="每个来源的最大条目数 (默认: 20)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="干跑模式：执行全流程但不写入文件",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="启用 DEBUG 级别日志",
    )
    return parser.parse_args(argv)


async def _run_pipeline(
    sources: list[str],
    limit: int,
    dry_run: bool,
) -> dict[str, Any]:
    """执行完整的四步流水线。

    Args:
        sources: 来源标识列表。
        limit: 每个来源的最大条目数。
        dry_run: 干跑模式。

    Returns:
        执行统计字典。
    """
    stats: dict[str, Any] = {
        "started_at": _now_iso(),
        "sources": sources,
        "limit": limit,
        "dry_run": dry_run,
        "collected_count": 0,
        "analyzed_count": 0,
        "organized_count": 0,
        "saved_count": 0,
    }

    # Step 1
    logger.info("=" * 50)
    logger.info("Step 1: Collect")
    logger.info("=" * 50)
    raw_items = await _collect(sources, limit)
    stats["collected_count"] = len(raw_items)

    if not raw_items:
        logger.warning("No items collected, pipeline stopping.")
        return stats

    for src in set(item.get("source", "") for item in raw_items):
        src_items = [it for it in raw_items if it.get("source") == src]
        if src_items and not dry_run:
            _save_raw(src_items, src)

    # Step 2
    logger.info("=" * 50)
    logger.info("Step 2: Analyze")
    logger.info("=" * 50)
    articles = await _analyze(raw_items)
    stats["analyzed_count"] = len(articles)

    if not articles:
        logger.warning("No articles analyzed, pipeline stopping.")
        return stats

    # Step 3
    logger.info("=" * 50)
    logger.info("Step 3: Organize")
    logger.info("=" * 50)
    final_articles = _organize(articles, dry_run=dry_run)
    stats["organized_count"] = len(final_articles)

    # Step 4
    logger.info("=" * 50)
    logger.info("Step 4: Save")
    logger.info("=" * 50)
    saved = _save_articles(final_articles, dry_run=dry_run)
    stats["saved_count"] = len(saved)

    stats["finished_at"] = _now_iso()
    return stats


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。

    Args:
        argv: 命令行参数列表。

    Returns:
        退出码（0 = 成功，1 = 失败）。
    """
    args = _parse_args(argv)
    _setup_logging(args.verbose)

    sources = [s.strip().lower() for s in args.sources.split(",")]
    valid_sources = {"github", "rss"}
    sources = [s for s in sources if s in valid_sources]
    invalid = set(args.sources.split(",")) - valid_sources
    if invalid:
        logger.warning("Ignored invalid sources: %s", invalid)

    if not sources:
        logger.error("No valid sources specified. Choose from: %s", valid_sources)
        return 1

    logger.info(
        "Pipeline starting: sources=%s, limit=%d, dry_run=%s",
        sources, args.limit, args.dry_run,
    )

    try:
        stats = asyncio.run(_run_pipeline(sources, args.limit, args.dry_run))
    except Exception:
        logger.exception("Pipeline failed with unhandled exception")
        return 1

    logger.info("=" * 50)
    logger.info("Pipeline Summary")
    logger.info("=" * 50)
    for key, value in stats.items():
        logger.info("  %s: %s", key, value)

    return 0


if __name__ == "__main__":
    sys.exit(main())
