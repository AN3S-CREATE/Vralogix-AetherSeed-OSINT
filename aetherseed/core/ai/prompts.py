"""Versioned prompt library.

Each prompt has a stable name and a semantic version. The canonical text lives
inline here (so it always works, even when installed as a wheel), but a matching
file in the top-level ``prompts/`` directory — if present — overrides it. That
gives operators an editable, git-versioned prompt surface without breaking the
package default.

Override file resolution: ``<repo>/prompts/<name>.md``. The first non-frontmatter
block is used as the prompt body.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: str
    system: str

    def render(self, **kwargs: str) -> str:
        """Fill ``{placeholders}`` in the system prompt."""
        return self.system.format(**kwargs)


_DEFAULTS: dict[str, Prompt] = {
    "seed_expansion": Prompt(
        name="seed_expansion",
        version="1.0.0",
        system=(
            "You are AetherMind, an OSINT prospecting engine for lawful, ethical "
            "investigative research. Given an investigation subject, propose the most "
            "productive next lines of inquiry.\n\n"
            "Subject type: {subject_type}\n"
            "Identifiers: {identifiers}\n"
            "Investigation brief: {context}\n\n"
            "Produce: high-signal web search queries; plausible social handles; related "
            "entities (people, companies, domains) worth investigating; concrete "
            "follow-the-money hypotheses (ownership, directorships, payment flows) to "
            "TEST (never assert as fact); and candidate seeds for further crawling. "
            "Prefer precision over volume. Do not fabricate identifiers — mark "
            "uncertain items with low priority."
        ),
    ),
    "entity_extraction": Prompt(
        name="entity_extraction",
        version="1.0.0",
        system=(
            "You extract a knowledge graph from a single document for an OSINT "
            "investigation. Identify concrete entities (people, companies, domains, "
            "assets, transactions, locations, documents, accounts) and the "
            "relationships between them that are explicitly supported by the text. "
            "Do NOT infer relationships that are not stated. Assign a confidence in "
            "[0,1] reflecting how directly the text supports each item.\n\n"
            "Document URL: {url}\n"
            "Document text (truncated):\n{text}"
        ),
    ),
    "gap_analysis": Prompt(
        name="gap_analysis",
        version="1.0.0",
        system=(
            "You are the gap-analysis module of an OSINT platform. Compare what is "
            "currently known against what a thorough investigation of this subject "
            "SHOULD cover. Identify missing dimensions, unanswered questions, and a "
            "prioritised list of concrete next actions (crawl, enrich_whois, "
            "search_registry, screenshot, etc.). Be specific and actionable.\n\n"
            "Subject: {subject}\n"
            "Known entities (sample): {entities}\n"
            "Coverage summary: {coverage}"
        ),
    ),
    "lead_scoring": Prompt(
        name="lead_scoring",
        version="1.0.0",
        system=(
            "You assess a single candidate lead for an OSINT investigation. Score its "
            "relevance to the subject, novelty (is this new information?), risk (does it "
            "indicate wrongdoing/red flags?), and your confidence. Explain in one "
            "sentence why it matters.\n\n"
            "Subject: {subject}\n"
            "Candidate lead: {lead}"
        ),
    ),
}


def _load_override(name: str) -> str | None:
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    # Strip a leading YAML frontmatter block if present.
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            text = parts[2]
    return text.strip() or None


@cache
def get_prompt(name: str) -> Prompt:
    """Return the prompt ``name``, applying a ``prompts/`` override if present.

    Raises
    ------
    KeyError
        If no such prompt exists.
    """
    if name not in _DEFAULTS:
        raise KeyError(f"unknown prompt: {name}")
    base = _DEFAULTS[name]
    override = _load_override(name)
    if override is not None:
        return Prompt(name=base.name, version=f"{base.version}+override", system=override)
    return base


def all_prompts() -> dict[str, Prompt]:
    """Return every registered prompt (defaults, with overrides applied)."""
    return {name: get_prompt(name) for name in _DEFAULTS}
