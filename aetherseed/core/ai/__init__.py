"""AetherMind — the prospective AI engine.

Local-first: the default backend is Ollama, reached over HTTP so the heavy
``ollama`` client is not a hard dependency. Every capability degrades to a
deterministic heuristic when no model is reachable, so the platform always
produces structured output (never free text, never a hard failure).

Public surface:

* :class:`aetherseed.core.ai.engine.AetherMind` — seed expansion, entity/relation
  extraction, lead scoring, and gap analysis.
* :func:`aetherseed.core.ai.backend.get_llm_backend` — backend factory.
"""

from __future__ import annotations
