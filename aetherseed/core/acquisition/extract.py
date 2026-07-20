"""HTML content extraction (BeautifulSoup + lxml).

Turns a fetched page into :class:`~aetherseed.core.interfaces.ExtractedContent`:
title, cleaned visible text, absolute outbound links (filtered), and a first
pass of deterministic entities. Richer entity/relation extraction is added later
by the AI engine — this layer never requires a model.
"""

from __future__ import annotations

from urllib.parse import urldefrag, urljoin, urlparse

from bs4 import BeautifulSoup

from aetherseed.core.interfaces import ExtractedContent, FetchResult
from aetherseed.core.nlp import extract_entities

_SKIP_LINK_SCHEMES = ("javascript:", "mailto:", "tel:", "data:", "#")
_NOISE_TAGS = ("script", "style", "noscript", "template", "svg", "iframe")


class HtmlExtractor:
    """Parses HTML into text, links, and deterministic entities."""

    name = "html"

    def __init__(self, *, extract_entities_inline: bool = True) -> None:
        self._extract_entities = extract_entities_inline

    def extract(self, result: FetchResult) -> ExtractedContent:
        """Parse ``result`` (must be HTML) into structured content."""
        content_type = (result.content_type or "").lower()
        if "html" not in content_type and not result.content.lstrip()[:15].lower().startswith(
            (b"<!doctype html", b"<html")
        ):
            # Non-HTML: return the raw text with no link expansion.
            text = result.text
            return ExtractedContent(
                url=result.final_url,
                text=text,
                entities=extract_entities(text, source_url=result.final_url)
                if self._extract_entities
                else [],
                metadata={"content_type": content_type, "non_html": True},
            )

        soup = BeautifulSoup(result.content, "lxml")
        for tag in soup(list(_NOISE_TAGS)):
            tag.decompose()

        title = soup.title.string.strip() if soup.title and soup.title.string else None
        text = soup.get_text(separator=" ", strip=True)
        links = self._extract_links(soup, base_url=result.final_url)

        entities = (
            extract_entities(text, source_url=result.final_url) if self._extract_entities else []
        )

        return ExtractedContent(
            url=result.final_url,
            title=title,
            text=text,
            links=links,
            entities=entities,
            metadata={
                "content_type": content_type,
                "link_count": len(links),
                "text_len": len(text),
                "rendered": result.rendered,
            },
        )

    def _extract_links(self, soup: BeautifulSoup, *, base_url: str) -> list[str]:
        base_host = urlparse(base_url).hostname
        seen: set[str] = set()
        out: list[str] = []
        for a in soup.find_all("a", href=True):
            href = str(a["href"]).strip()
            if not href or href.lower().startswith(_SKIP_LINK_SCHEMES):
                continue
            absolute = urldefrag(urljoin(base_url, href)).url
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https"):
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            out.append(absolute)
        # Stable ordering: same-host links first (crawl locality).
        out.sort(key=lambda u: (urlparse(u).hostname != base_host, u))
        return out
