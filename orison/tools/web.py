"""Web search and fetch tools."""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, quote_plus

import httpx

from orison.tools.base import Tool, object_schema
from orison.tools.common import (
    _UNTRUSTED_BANNER,
    _USER_AGENT,
    _html_to_markdown,
    _html_to_text,
    _normalize,
    _strip_tags,
    _validate_resolved_url,
    _validate_url_safe,
)

class WebSearchTool(Tool):
    @property
    def name(self) -> str:
        return "web_search"

    @property
    def description(self) -> str:
        return (
            "Search the web. Supports ORISON_WEB_SEARCH_PROVIDER/BRAVE_API_KEY/TAVILY_API_KEY/"
            "SEARXNG_BASE_URL/JINA_API_KEY/KAGI_API_KEY, with DuckDuckGo HTML fallback."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def exclusive(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "query": {"type": "string", "minLength": 1},
                "count": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            required=["query"],
        )

    async def execute(self, query: str, count: int = 5, **_: Any) -> str:
        provider = os.environ.get("ORISON_WEB_SEARCH_PROVIDER", "brave").strip().lower() or "brave"
        n = min(max(count or 5, 1), 10)
        if provider == "brave" and os.environ.get("BRAVE_API_KEY"):
            return await _search_brave(query, n)
        if provider == "tavily" and os.environ.get("TAVILY_API_KEY"):
            return await _search_tavily(query, n)
        if provider == "searxng" and os.environ.get("SEARXNG_BASE_URL"):
            return await _search_searxng(query, n)
        if provider == "jina" and os.environ.get("JINA_API_KEY"):
            return await _search_jina(query, n)
        if provider == "kagi" and os.environ.get("KAGI_API_KEY"):
            return await _search_kagi(query, n)
        return await _search_duckduckgo_html(query, n)


async def _search_duckduckgo_html(query: str, count: int) -> str:
    url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            response = await client.get(url, headers={"User-Agent": _USER_AGENT})
            response.raise_for_status()
    except Exception as exc:
        return f"Error searching web: {exc}"
    items = _parse_duckduckgo_html(response.text)[: min(max(count, 1), 10)]
    if not items:
        return f"No results for: {query}"
    lines = [f"Results for: {query}"]
    for idx, item in enumerate(items, 1):
        lines.append(f"{idx}. {item.title}\n   {item.url}")
        if item.snippet:
            lines.append(f"   {item.snippet}")
    return "\n".join(lines)


class WebFetchTool(Tool):
    @property
    def name(self) -> str:
        return "web_fetch"

    @property
    def description(self) -> str:
        return (
            "Fetch an http(s) URL with SSRF protection and return JSON containing readable "
            "text extracted from HTML/plain/json, or image metadata for images."
        )

    @property
    def read_only(self) -> bool:
        return True

    @property
    def parameters(self) -> dict[str, Any]:
        return object_schema(
            {
                "url": {"type": "string", "minLength": 1},
                "extractMode": {"type": "string", "enum": ["markdown", "text", "html"]},
                "maxChars": {"type": "integer", "minimum": 100, "maximum": 100000},
            },
            required=["url"],
        )

    async def execute(self, url: str, extractMode: str = "markdown", maxChars: int = 50000, **_: Any) -> Any:
        valid, error = _validate_url_safe(url)
        if not valid:
            return json.dumps({"error": f"URL validation failed: {error}", "url": url}, ensure_ascii=False)
        if extractMode != "html":
            jina = await _fetch_jina_reader(url, maxChars)
            if jina is not None:
                return jina
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, max_redirects=5) as client:
                response = await client.get(url, headers={"User-Agent": _USER_AGENT})
                response.raise_for_status()
        except Exception as exc:
            return json.dumps({"error": str(exc), "url": url}, ensure_ascii=False)
        redir_ok, redir_error = _validate_resolved_url(str(response.url))
        if not redir_ok:
            return json.dumps({"error": f"Redirect blocked: {redir_error}", "url": url}, ensure_ascii=False)
        content_type = response.headers.get("content-type", "")
        if content_type.startswith("image/"):
            return {
                "type": "image",
                "mime_type": content_type.split(";", 1)[0],
                "url": url,
                "finalUrl": str(response.url),
                "data": base64.b64encode(response.content).decode("ascii"),
            }
        if "application/json" in content_type:
            try:
                text = json.dumps(response.json(), indent=2, ensure_ascii=False)
            except Exception:
                text = response.text
            extractor = "json"
        elif extractMode != "html" and ("html" in content_type.lower() or response.text[:256].lower().startswith(("<!doctype", "<html"))):
            text = _html_to_markdown(response.text) if extractMode == "markdown" else _html_to_text(response.text)
            extractor = "html"
        else:
            text = response.text
            extractor = "raw"
        truncated = len(text) > maxChars
        if truncated:
            text = text[:maxChars]
        text = f"{_UNTRUSTED_BANNER}\n\n{text.strip()}"
        return json.dumps(
            {
                "url": url,
                "finalUrl": str(response.url),
                "status": response.status_code,
                "extractor": extractor,
                "truncated": truncated,
                "length": len(text),
                "untrusted": True,
                "text": text,
            },
            ensure_ascii=False,
        )



class _SearchResult:
    title: str
    url: str
    snippet: str


async def _search_brave(query: str, count: int) -> str:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://api.search.brave.com/res/v1/web/search",
                params={"q": query, "count": count},
                headers={"Accept": "application/json", "X-Subscription-Token": os.environ["BRAVE_API_KEY"]},
            )
            response.raise_for_status()
        items = [
            {"title": item.get("title", ""), "url": item.get("url", ""), "content": item.get("description", "")}
            for item in response.json().get("web", {}).get("results", [])
        ]
        return _format_provider_results(query, items, count)
    except Exception:
        return await _search_duckduckgo_html(query, count)


async def _search_tavily(query: str, count: int) -> str:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {os.environ['TAVILY_API_KEY']}"},
                json={"query": query, "max_results": count},
            )
            response.raise_for_status()
        return _format_provider_results(query, response.json().get("results", []), count)
    except Exception:
        return await _search_duckduckgo_html(query, count)


async def _search_searxng(query: str, count: int) -> str:
    endpoint = f"{os.environ.get('SEARXNG_BASE_URL', '').rstrip('/')}/search"
    ok, error = _validate_url_safe(endpoint)
    if not ok:
        return f"Error: invalid SearXNG URL: {error}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(endpoint, params={"q": query, "format": "json"}, headers={"User-Agent": _USER_AGENT})
            response.raise_for_status()
        return _format_provider_results(query, response.json().get("results", []), count)
    except Exception:
        return await _search_duckduckgo_html(query, count)


async def _search_jina(query: str, count: int) -> str:
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(
                f"https://s.jina.ai/{quote(query, safe='')}",
                headers={"Accept": "application/json", "Authorization": f"Bearer {os.environ['JINA_API_KEY']}"},
            )
            response.raise_for_status()
        data = response.json().get("data", [])[:count]
        items = [
            {"title": item.get("title", ""), "url": item.get("url", ""), "content": item.get("content", "")[:500]}
            for item in data
        ]
        return _format_provider_results(query, items, count)
    except Exception:
        return await _search_duckduckgo_html(query, count)


async def _search_kagi(query: str, count: int) -> str:
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                "https://kagi.com/api/v0/search",
                params={"q": query, "limit": count},
                headers={"Authorization": f"Bot {os.environ['KAGI_API_KEY']}"},
            )
            response.raise_for_status()
        items = [
            {"title": item.get("title", ""), "url": item.get("url", ""), "content": item.get("snippet", "")}
            for item in response.json().get("data", [])
            if item.get("t") == 0
        ]
        return _format_provider_results(query, items, count)
    except Exception:
        return await _search_duckduckgo_html(query, count)


def _format_provider_results(query: str, items: list[dict[str, Any]], count: int) -> str:
    if not items:
        return f"No results for: {query}"
    lines = [f"Results for: {query}"]
    for idx, item in enumerate(items[:count], 1):
        title = _normalize(_strip_tags(str(item.get("title", ""))))
        url = str(item.get("url", ""))
        snippet = _normalize(_strip_tags(str(item.get("content", item.get("snippet", "")))))
        lines.append(f"{idx}. {title}\n   {url}")
        if snippet:
            lines.append(f"   {snippet}")
    return "\n".join(lines)


async def _fetch_jina_reader(url: str, max_chars: int) -> str | None:
    try:
        headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
        if os.environ.get("JINA_API_KEY"):
            headers["Authorization"] = f"Bearer {os.environ['JINA_API_KEY']}"
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"https://r.jina.ai/{url}", headers=headers)
            if response.status_code == 429:
                return None
            response.raise_for_status()
        data = response.json().get("data", {})
        text = data.get("content", "")
        if not text:
            return None
        title = data.get("title", "")
        if title:
            text = f"# {title}\n\n{text}"
        truncated = len(text) > max_chars
        if truncated:
            text = text[:max_chars]
        text = f"{_UNTRUSTED_BANNER}\n\n{text}"
        return json.dumps(
            {
                "url": url,
                "finalUrl": data.get("url", url),
                "status": response.status_code,
                "extractor": "jina",
                "truncated": truncated,
                "length": len(text),
                "untrusted": True,
                "text": text,
            },
            ensure_ascii=False,
        )
    except Exception:
        return None


def _parse_duckduckgo_html(text: str) -> list[_SearchResult]:
    results: list[_SearchResult] = []
    blocks = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>(.*?)(?=<a[^>]+class="result__a"|$)', text, re.S)
    for raw_url, raw_title, rest in blocks:
        title = _normalize(_strip_tags(raw_title))
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', rest, re.S)
        snippet = _normalize(_strip_tags(snippet_match.group(1))) if snippet_match else ""
        results.append(_SearchResult(title=title, url=html.unescape(raw_url), snippet=snippet))
    return results

