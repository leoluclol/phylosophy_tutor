"""Web search, abstracted from the rest of the pipeline.

The pipeline depends only on the ``SearchProvider`` interface, so swapping the
search backend is a configuration change, not a code change.
"""
from __future__ import annotations

import logging
import urllib.parse
from abc import ABC, abstractmethod
from typing import Optional

import requests
from bs4 import BeautifulSoup

from .config import Config
from .models import SearchResult

logger = logging.getLogger("phylosophy")

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

# Domains that are trustworthy for this project (section 6 of the brief).
PREFERRED_DOMAINS = {
    "plato.stanford.edu": -40,
    "iep.utm.edu": -35,
    "gutenberg.org": -30,
    "earlymoderntexts.com": -28,
    "marxists.org": -28,
    "oll.libertyfund.org": -25,
    "constitution.org": -22,
    "econlib.org": -20,
    "philpapers.org": -18,
    "jstor.org": -10,
    "academia.edu": 5,
    "researchgate.net": 8,
    "wikipedia.org": 8,  # allowed for orientation, never foundational
}

# Low-quality / SEO sites to avoid as argumentative sources.
PENALIZED_DOMAINS = {
    "quora.com": 25,
    "coursehero.com": 30,
    "studocu.com": 30,
    "medium.com": 15,
    "slideshare.net": 20,
    "prezi.com": 20,
    "scribd.com": 20,
    "reddit.com": 25,
    "chegg.com": 30,
    "bartleby.com": 30,
    "123doc.org": 30,
}


def _netloc(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.lower().removeprefix("www.")
    except ValueError:
        return ""


def score_result(result: SearchResult) -> SearchResult:
    """Score a result by domain quality. Lower = better."""
    host = _netloc(result.url)
    score = 10.0
    for domain, penalty in {**PREFERRED_DOMAINS, **PENALIZED_DOMAINS}.items():
        if host == domain or host.endswith("." + domain):
            score += penalty
            break
    if not host:
        score += 50
    result.priority = score
    return result


class SearchProvider(ABC):
    """Interface for a web-search backend."""

    name: str = "abstract"

    @abstractmethod
    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        ...


class DuckDuckGoSearchProvider(SearchProvider):
    """DuckDuckGo HTML endpoint (server-rendered, no JS)."""

    name = "duckduckgo"

    def __init__(self, config: Config):
        self.config = config

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        url = "https://html.duckduckgo.com/html/?" + urllib.parse.urlencode(
            {"q": query, "kl": "us-en"}
        )
        try:
            resp = requests.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=self.config.search_fetch_timeout
            )
        except requests.RequestException as exc:  # noqa: PERF203
            logger.warning("DuckDuckGo search failed: %s", exc)
            return []
        if resp.status_code != 200:
            logger.warning("DuckDuckGo search returned HTTP %s", resp.status_code)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        results: list[SearchResult] = []
        for node in soup.select("div.result")[: max_results * 2]:
            link = node.select_one("a.result__a")
            if not link:
                continue
            href = self._decode_href(link.get("href", ""))
            if not href:
                continue
            snippet_node = node.select_one(".result__snippet")
            results.append(
                SearchResult(
                    title=" ".join(link.get_text().split())[:300],
                    url=href,
                    snippet=" ".join(snippet_node.get_text().split())[:600] if snippet_node else "",
                    provider=self.name,
                )
            )
        results = [score_result(r) for r in results[:max_results]]
        logger.debug("DuckDuckGo: %d hits for %r", len(results), query)
        return results

    @staticmethod
    def _decode_href(href: str) -> str:
        """DDG wraps links as //duckduckgo.com/l/?uddg=<url>."""
        if "uddg=" in href:
            parsed = urllib.parse.urlparse(href)
            qs = urllib.parse.parse_qs(parsed.query)
            if "uddg" in qs and qs["uddg"]:
                return qs["uddg"][0]
        return href


class BingSearchProvider(SearchProvider):
    """Bing HTML results scraping (fallback provider)."""

    name = "bing"

    def __init__(self, config: Config):
        self.config = config

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        url = "https://www.bing.com/search?" + urllib.parse.urlencode(
            {"q": query, "setlang": "en", "mkt": "en-US", "cc": "US"}
        )
        try:
            resp = requests.get(
                url,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"},
                timeout=self.config.search_fetch_timeout,
            )
        except requests.RequestException as exc:  # noqa: PERF203
            logger.warning("Bing search failed: %s", exc)
            return []
        if resp.status_code != 200:
            logger.warning("Bing search returned HTTP %s", resp.status_code)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        results: list[SearchResult] = []
        for item in soup.select("li.b_algo")[: max_results * 2]:
            link = item.select_one("h2 a") or item.select_one("a")
            if not link:
                continue
            href = link.get("href", "")
            if not href or "bing.com/ck/a" in href:
                continue  # skip click-tracking redirects
            snippet_node = item.select_one("p")
            results.append(
                SearchResult(
                    title=" ".join(link.get_text().split())[:300],
                    url=href,
                    snippet=" ".join(snippet_node.get_text().split())[:600] if snippet_node else "",
                    provider=self.name,
                )
            )
        results = [score_result(r) for r in results[:max_results]]
        logger.debug("Bing: %d hits for %r", len(results), query)
        return results


class MultiProviderSearch(SearchProvider):
    """Try providers in order and merge their (deduplicated) results."""

    name = "multi"

    def __init__(self, providers: list[SearchProvider]):
        self.providers = providers

    def search(self, query: str, max_results: int = 10) -> list[SearchResult]:
        seen: dict[str, SearchResult] = {}
        for provider in self.providers:
            try:
                hits = provider.search(query, max_results=max_results)
            except Exception as exc:  # noqa: BLE001 - a broken backend must not kill the pipeline
                logger.warning("Provider %s failed: %s", getattr(provider, "name", "?"), exc)
                continue
            for hit in hits:
                key = urllib.parse.urlparse(hit.url).netloc + urllib.parse.urlparse(hit.url).path
                key = key.lower().rstrip("/")
                if key not in seen:
                    seen[key] = hit
        merged = sorted(seen.values(), key=lambda r: r.priority)
        return merged[:max_results]


def build_search_provider(config: Config) -> Optional[SearchProvider]:
    """Instantiate the configured provider (returns None when search is off)."""
    if not config.search_enabled:
        logger.info("Web search disabled by configuration (search.enabled=false).")
        return None

    name = config.search_provider.strip().lower()
    if name == "multi":
        providers = []
        for sub in config.search_providers:
            providers.append(_one(config, sub))
        return MultiProviderSearch([p for p in providers if p is not None])
    return _one(config, name)


def _one(config: Config, name: str) -> Optional[SearchProvider]:
    if name == "duckduckgo":
        return DuckDuckGoSearchProvider(config)
    if name == "bing":
        return BingSearchProvider(config)
    logger.warning("Unknown search provider %r; falling back to duckduckgo", name)
    return DuckDuckGoSearchProvider(config)