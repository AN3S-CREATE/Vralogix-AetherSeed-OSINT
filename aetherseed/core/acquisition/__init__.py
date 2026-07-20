"""Acquisition engine: fetch, render, screenshot, download, crawl — safely.

The engine is defence-in-depth by default:

* :mod:`aetherseed.core.acquisition.security` blocks SSRF (private/loopback/
  link-local targets, metadata endpoints) before any request leaves the process.
* :mod:`aetherseed.core.acquisition.robots` honours robots.txt unless explicitly
  overridden per run.
* :mod:`aetherseed.core.acquisition.ratelimit` enforces polite per-host delays
  and bounded concurrency.

Every fetch is retried (transient errors only) with exponential backoff, and
every failure is isolated: one bad page never aborts a crawl.
"""

from __future__ import annotations
