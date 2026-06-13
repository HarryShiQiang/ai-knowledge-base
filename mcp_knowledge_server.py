#!/usr/bin/env python3
"""MCP Knowledge Server — search local knowledge base over JSON-RPC 2.0 stdio.

Provides three tools to AI agents:
  - search_articles   : full-text search across title + summary
  - get_article       : fetch a single article by id
  - knowledge_stats   : summary statistics of the knowledge base

Usage:
    python mcp_knowledge_server.py           # serve over stdio
    python mcp_knowledge_server.py --help     # show usage
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ARTICLES_DIR = Path(__file__).resolve().parent / "knowledge" / "articles"

# ---------------------------------------------------------------------------
# JSON-RPC 2.0 helpers
# ---------------------------------------------------------------------------

JSONRPC_VERSION = "2.0"

# Standard JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INTERNAL_ERROR = -32603

MCP_SERVER_NAME = "knowledge-mcp"
MCP_SERVER_VERSION = "1.0.0"


def _rpc_response(msg_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": msg_id, "result": result}


def _rpc_error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": JSONRPC_VERSION,
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def _write(response: dict[str, Any]) -> None:
    """Write a JSON-RPC response to stdout followed by newline."""
    sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _log(message: str) -> None:
    """Write a log line to stderr (never mixed with stdout protocol)."""
    print(f"[mcp-kb] {message}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Knowledge base loader
# ---------------------------------------------------------------------------


def _load_articles(articles_dir: Path) -> list[dict[str, Any]]:
    """Load all JSON article files from the given directory."""
    articles: list[dict[str, Any]] = []
    if not articles_dir.exists() or not articles_dir.is_dir():
        _log(f"articles directory not found: {articles_dir}")
        return articles

    for filepath in sorted(articles_dir.glob("*.json")):
        try:
            data = json.loads(filepath.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                articles.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            _log(f"skipping {filepath.name}: {exc}")

    _log(f"loaded {len(articles)} articles from {articles_dir}")
    return articles


def _extract_source(article: dict[str, Any]) -> str:
    """Derive a short source label from source_url or source_type."""
    source_type = article.get("source_type", "")
    if source_type:
        return source_type.replace("_trending", "")
    url = article.get("source_url", "")
    if "github.com" in url:
        return "github"
    if "ycombinator.com" in url or "hackernews" in url:
        return "hackernews"
    return "unknown"


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------


def _search_articles(
    articles: list[dict[str, Any]], keyword: str, limit: int = 5
) -> dict[str, Any]:
    """Full-text search across title and summary fields."""
    if not keyword.strip():
        return {"results": [], "total": 0, "matched": 0, "query": keyword}

    kw_lower = keyword.lower()
    matches: list[dict[str, Any]] = []

    for article in articles:
        title = (article.get("title") or "").lower()
        summary = (article.get("summary") or "").lower()
        chinese_summary = (article.get("chinese_summary") or "").lower()
        tags = [t.lower() for t in article.get("tags", []) if isinstance(t, str)]

        score = 0
        if kw_lower in article.get("id", "").lower():
            score += 10
        if kw_lower in title:
            score += 5
        if kw_lower in summary:
            score += 3
        if kw_lower in chinese_summary:
            score += 2
        if any(kw_lower in tag for tag in tags):
            score += 4

        if score > 0:
            matches.append({
                "id": article.get("id", ""),
                "title": article.get("title", ""),
                "summary": (article.get("summary") or "")[:200],
                "score": article.get("score"),
                "tags": article.get("tags", []),
                "source": _extract_source(article),
                "match_score": score,
            })

    matches.sort(key=lambda m: m["match_score"], reverse=True)
    matches = matches[:max(1, limit)]

    return {
        "results": matches,
        "total": len(articles),
        "matched": len(matches),
        "query": keyword,
    }


def _get_article(articles: list[dict[str, Any]], article_id: str) -> dict[str, Any]:
    """Fetch a single article by its id field."""
    for article in articles:
        if article.get("id") == article_id:
            return {"found": True, "article": article}

    # Try fuzzy match: check if any ID contains the given string
    candidates = [a for a in articles if article_id.lower() in a.get("id", "").lower()]
    if candidates:
        return {
            "found": False,
            "article": None,
            "suggestion": f"Exact ID not found, but {len(candidates)} similar: "
            + ", ".join(c.get("id", "") for c in candidates[:5]),
        }

    return {"found": False, "article": None, "suggestion": "No matching article ID found."}


def _knowledge_stats(articles: list[dict[str, Any]]) -> dict[str, Any]:
    """Return aggregate statistics about the knowledge base."""
    if not articles:
        return {"total_articles": 0, "by_source": {}, "top_tags": [], "by_status": {}}

    sources = Counter(_extract_source(a) for a in articles)
    statuses = Counter(a.get("status", "unknown") for a in articles)

    all_tags: Counter[str] = Counter()
    scores: list[float] = []
    for article in articles:
        for tag in article.get("tags", []):
            if isinstance(tag, str):
                all_tags[tag] += 1
        score = article.get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            scores.append(float(score))

    return {
        "total_articles": len(articles),
        "by_source": dict(sources.most_common()),
        "by_status": dict(statuses.most_common()),
        "top_tags": [{"tag": t, "count": c} for t, c in all_tags.most_common(10)],
        "average_score": round(sum(scores) / len(scores), 2) if scores else None,
        "score_range": {
            "min": min(scores) if scores else None,
            "max": max(scores) if scores else None,
        },
    }


# ---------------------------------------------------------------------------
# MCP protocol handlers
# ---------------------------------------------------------------------------


def _handle_initialize(msg_id: Any, params: dict[str, Any]) -> None:
    """Respond to the MCP initialize request."""
    _log(f"initialize from {params.get('clientInfo', {}).get('name', 'unknown')}")
    _write(_rpc_response(msg_id, {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {
            "name": MCP_SERVER_NAME,
            "version": MCP_SERVER_VERSION,
        },
    }))


def _handle_tools_list(msg_id: Any, articles_count: int) -> None:
    """Respond with available tool definitions."""
    tools = [
        {
            "name": "search_articles",
            "description": "Search knowledge base articles by keyword across title, summary, and tags. Returns ranked results with match scores.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "description": "Search keyword (matched against title, summary, Chinese summary, tags)",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 5)",
                        "default": 5,
                    },
                },
                "required": ["keyword"],
            },
        },
        {
            "name": "get_article",
            "description": "Retrieve a single article's full content by its ID.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "article_id": {
                        "type": "string",
                        "description": "The unique article ID (e.g. '20260613-gh-dify')",
                    },
                },
                "required": ["article_id"],
            },
        },
        {
            "name": "knowledge_stats",
            "description": f"Get aggregate statistics about the knowledge base ({articles_count} articles loaded).",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
    ]
    _write(_rpc_response(msg_id, {"tools": tools}))


def _handle_tools_call(
    msg_id: Any,
    params: dict[str, Any],
    articles: list[dict[str, Any]],
) -> None:
    """Dispatch a tools/call request to the appropriate handler."""
    tool_name = params.get("name", "")
    arguments = params.get("arguments", {})

    try:
        if tool_name == "search_articles":
            keyword = str(arguments.get("keyword", ""))
            limit = int(arguments.get("limit", 5))
            result = _search_articles(articles, keyword, limit)
            content = json.dumps(result, ensure_ascii=False, indent=2)
        elif tool_name == "get_article":
            article_id = str(arguments.get("article_id", ""))
            result = _get_article(articles, article_id)
            content = json.dumps(result, ensure_ascii=False, indent=2)
        elif tool_name == "knowledge_stats":
            result = _knowledge_stats(articles)
            content = json.dumps(result, ensure_ascii=False, indent=2)
        else:
            _write(_rpc_error(msg_id, METHOD_NOT_FOUND, f"Unknown tool: {tool_name}"))
            return

        _write(_rpc_response(msg_id, {
            "content": [{"type": "text", "text": content}],
        }))
    except Exception as exc:
        _log(f"error calling {tool_name}: {exc}")
        _write(_rpc_response(msg_id, {
            "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
            "isError": True,
        }))


def _dispatch(
    request: dict[str, Any],
    articles: list[dict[str, Any]],
) -> None:
    """Route a JSON-RPC request to the correct handler."""
    method = request.get("method", "")
    msg_id = request.get("id")
    params = request.get("params", {})

    if method == "initialize":
        _handle_initialize(msg_id, params)
    elif method == "tools/list":
        _handle_tools_list(msg_id, len(articles))
    elif method == "tools/call":
        _handle_tools_call(msg_id, params, articles)
    elif method == "notifications/initialized":
        _log("client initialized, ready for requests")
        # No response for notifications
    else:
        _write(_rpc_error(msg_id, METHOD_NOT_FOUND, f"Unknown method: {method}"))


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main() -> int:
    """Run the MCP server on stdio."""
    _log(f"starting {MCP_SERVER_NAME} v{MCP_SERVER_VERSION}")

    articles = _load_articles(ARTICLES_DIR)

    if not articles:
        _log("warning: no articles loaded — serving with empty index")

    _log("listening on stdio...")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _log(f"parse error: {exc}")
            _write({"jsonrpc": JSONRPC_VERSION, "id": None,
                     "error": {"code": PARSE_ERROR, "message": str(exc)}})
            continue

        if not isinstance(request, dict):
            _write(_rpc_error(None, INVALID_REQUEST, "Request must be a JSON object"))
            continue

        _dispatch(request, articles)

    _log("stdin closed, exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
