"""HTML extractor tests."""

from __future__ import annotations

from aetherseed.core.acquisition.extract import HtmlExtractor
from aetherseed.core.interfaces import FetchResult

HTML = """
<!doctype html><html><head><title> Acme </title><style>x{}</style></head>
<body><h1>Acme Mining Pty Ltd</h1>
<a href="/about">About</a>
<a href="https://other.example/x">External</a>
<a href="mailto:a@b.com">mail</a>
<script>var x=1;</script>
</body></html>
"""


def _result(url: str = "https://acme.example/") -> FetchResult:
    return FetchResult(
        url=url, final_url=url, status_code=200,
        content=HTML.encode(), content_type="text/html", ok=True,
    )


def test_extracts_title_text_links() -> None:
    out = HtmlExtractor().extract(_result())
    assert out.title == "Acme"
    assert "Acme Mining Pty Ltd" in out.text
    assert "var x=1" not in out.text  # script stripped
    # relative link resolved absolutely, mailto skipped
    assert "https://acme.example/about" in out.links
    assert "https://other.example/x" in out.links
    assert all("mailto:" not in link for link in out.links)


def test_same_host_links_sorted_first() -> None:
    out = HtmlExtractor().extract(_result())
    # first link should be same-host
    assert out.links[0].startswith("https://acme.example")
