"""Page fetching and HTML->text extraction.

A page that cannot be retrieved is reported with ``source_status ==
"unavailable"`` plus a reason; the pipeline never pretends to have read it.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Callable, Optional

import requests
from bs4 import BeautifulSoup

from .models import FetchedPage
from .search import USER_AGENT

logger = logging.getLogger("phylosophy")

# Tags whose content is boilerplate/navigation, not the article body.
_SKIP_TAGS = {
    "script", "style", "noscript", "nav", "footer", "header", "aside",
    "form", "button", "iframe", "svg", "audio", "video", "figure", "figcaption",
}

_BLOCK_TAGS = {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "td", "tr", "section", "article", "ol", "ul", "br", "tr", "table"}

_URL_CACHE_VERSION = "v1"


def _cache_key(url: str) -> str:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return f"{_URL_CACHE_VERSION}_{digest[:24]}"


def _cache_path(cache_dir: Path, url: str) -> Path:
    return cache_dir / "pages" / f"{_cache_key(url)}.json"


def html_to_text(html: str, max_chars: int) -> str:
    """Extract readable text from a raw HTML document.

    Preserves paragraph breaks so the researcher can locate passages.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in list(soup.find_all(_SKIP_TAGS)):
        tag.decompose()

    parts: list[str] = []
    for block in soup.find_all(_BLOCK_TAGS):
        text = block.get_text(" ", strip=True)
        if text:
            parts.append(text)
    # Fallback: any remaining loose text.
    loose = soup.get_text(" ")
    if not parts:
        parts = re.split(r"\n\s*\n", loose)

    text = "\n\n".join(p for p in parts if p)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]


def _title_from_html(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return " ".join(soup.title.string.split())[:300]
    h1 = soup.find("h1")
    if h1:
        return " ".join(h1.get_text().split())[:300]
    return ""


def fetch_page(
    url: str,
    *,
    cache_dir: Optional[Path],
    max_chars: int = 200000,
    timeout: float = 25.0,
) -> FetchedPage:
    """Fetch and cache one page, returning a FetchedPage either way."""
    if cache_dir is not None:
        cached = _cache_path(cache_dir, url)
        if cached.exists():
            try:
                data = json.loads(cached.read_text(encoding="utf-8"))
                page = FetchedPage.model_validate(data)
                logger.debug("page cache hit: %s", url)
                return page
            except (ValueError, OSError) as exc:
                logger.warning("corrupt page cache for %s (%s); refetching", url, exc)

    page = _fetch(url, max_chars=max_chars, timeout=timeout)

    if cache_dir is not None:
        (_cache_path(cache_dir, url).parent).mkdir(parents=True, exist_ok=True)
        try:
            _cache_path(cache_dir, url).write_text(
                page.model_dump_json(indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("could not write page cache for %s: %s", url, exc)
    return page


def _fetch(url: str, *, max_chars: int, timeout: float) -> FetchedPage:
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=timeout,
            allow_redirects=True,
            stream=True,
        )
    except requests.RequestException as exc:  # noqa: PERF203
        logger.warning("unable to fetch %s: %s", url, exc)
        return FetchedPage(url=url, source_status="unavailable", reason=str(exc)[:300])

    content_type = resp.headers.get("Content-Type", "").lower()
    if resp.status_code != 200:
        return FetchedPage(
            url=url,
            source_status="unavailable",
            reason=f"HTTP {resp.status_code}",
        )

    if "pdf" in content_type or url.lower().endswith(".pdf"):
        return FetchedPage(
            url=url,
            title="(PDF)",
            source_status="unavailable",
            reason="PDF extraction is not implemented in V1",
        )

    html = resp.text
    title = _title_from_html(html)
    text = html_to_text(html, max_chars=max_chars)
    if len(text) < 200:
        return FetchedPage(
            url=url,
            title=title,
            source_status="empty",
            reason="page contained almost no extractable text",
        )
    return FetchedPage(url=url, title=title, source_status="fetched", text=text)


# A fetch function with sensible defaults, useful for dependency injection
# and for tests.
def make_fetcher(cache_dir: Optional[Path], max_chars: int, timeout: float) -> Callable[[str], FetchedPage]:
    def fetcher(url: str) -> FetchedPage:
        return fetch_page(url, cache_dir=cache_dir, max_chars=max_chars, timeout=timeout)

    return fetcher