"""Core domain layer: acquisition, ai, graph, seeding, enrichment, storage.

Depends only on the standard library, Pydantic, and the interfaces defined in
:mod:`aetherseed.core.interfaces`. Concrete integrations live in the respective
sub-packages and are wired together by :mod:`aetherseed.pipelines`.
"""

from __future__ import annotations
