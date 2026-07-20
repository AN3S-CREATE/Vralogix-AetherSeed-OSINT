"""Data-driven seeding rules (+ optional Python hooks).

A rule maps a discovered entity type to a new seed, gated by a minimum
confidence and an optional blacklist of terms. Rules ship as sensible defaults
and can be overridden by a YAML/JSON file (``config/seed_rules.yaml`` by default,
or an explicit path), so investigators tune expansion behaviour without touching
code. A registry of Python hook functions allows fully custom logic where rules
are not expressive enough.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from aetherseed.logging import get_logger
from aetherseed.schemas import Entity, EntityType, SubjectSeed, SubjectType

log = get_logger(__name__)

# Map an entity type to the seed subject type it should spawn.
_ENTITY_TO_SUBJECT = {
    EntityType.COMPANY: SubjectType.COMPANY,
    EntityType.PERSON: SubjectType.PERSON,
    EntityType.DOMAIN: SubjectType.DOMAIN,
}


@dataclass(slots=True)
class SeedRule:
    name: str
    entity_type: EntityType
    subject_type: SubjectType
    min_confidence: float = 0.5
    priority: float = 0.5
    max_per_run: int = 20
    blacklist: list[str] = field(default_factory=list)

    def matches(self, entity: Entity) -> bool:
        if entity.type is not self.entity_type:
            return False
        if entity.confidence < self.min_confidence:
            return False
        label = entity.label.lower()
        return not any(term.lower() in label for term in self.blacklist)


@dataclass(slots=True)
class SeedCandidate:
    subject_type: SubjectType
    identifiers: list[str]
    context: str
    priority: float
    rationale: str
    source: str  # rule | llm | gap
    rule: str | None = None

    def to_subject(self) -> SubjectSeed:
        return SubjectSeed(
            subject_type=self.subject_type,
            primary_identifiers=self.identifiers,
            context=self.context,
        )

    def dedup_key(self) -> tuple[str, tuple[str, ...]]:
        return (self.subject_type.value, tuple(sorted(i.lower().strip() for i in self.identifiers)))


DEFAULT_RULES: list[SeedRule] = [
    SeedRule("company_entity", EntityType.COMPANY, SubjectType.COMPANY, 0.55, 0.7, 20),
    SeedRule("person_entity", EntityType.PERSON, SubjectType.PERSON, 0.6, 0.6, 20),
    SeedRule("domain_entity", EntityType.DOMAIN, SubjectType.DOMAIN, 0.5, 0.65, 30,
             blacklist=["google.com", "facebook.com", "twitter.com", "linkedin.com", "youtube.com"]),
]

# Registry for custom Python hooks: name -> callable(entities, subject) -> candidates.
HookFn = Callable[[list[Entity], SubjectSeed], list[SeedCandidate]]
_HOOKS: dict[str, HookFn] = {}


def register_hook(name: str) -> Callable[[HookFn], HookFn]:
    """Decorator registering a custom seeding hook."""

    def _wrap(fn: HookFn) -> HookFn:
        _HOOKS[name] = fn
        return fn

    return _wrap


def load_rules(path: str | Path | None = None) -> list[SeedRule]:
    """Load seeding rules from ``path`` (YAML/JSON), falling back to defaults.

    The file format is a list of mappings with keys matching :class:`SeedRule`
    fields. Unknown entity/subject types are skipped with a warning.
    """
    candidate = Path(path) if path else Path("config/seed_rules.yaml")
    if not candidate.exists():
        return list(DEFAULT_RULES)
    try:
        raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as exc:
        log.warning("seeding.rules_parse_failed", path=str(candidate), error=str(exc))
        return list(DEFAULT_RULES)

    rules: list[SeedRule] = []
    for item in raw:
        try:
            rules.append(
                SeedRule(
                    name=item["name"],
                    entity_type=EntityType(item["entity_type"]),
                    subject_type=SubjectType(item["subject_type"]),
                    min_confidence=float(item.get("min_confidence", 0.5)),
                    priority=float(item.get("priority", 0.5)),
                    max_per_run=int(item.get("max_per_run", 20)),
                    blacklist=list(item.get("blacklist", [])),
                )
            )
        except (KeyError, ValueError) as exc:
            log.warning("seeding.rule_skipped", item=item, error=str(exc))
    return rules or list(DEFAULT_RULES)


def apply_rules(
    entities: Iterable[Entity],
    subject: SubjectSeed,
    *,
    rules: list[SeedRule] | None = None,
) -> list[SeedCandidate]:
    """Apply seeding rules to ``entities``, returning candidate seeds.

    Enforces each rule's ``max_per_run`` and de-duplicates against the subject's
    own identifiers so we never re-seed the thing we started from.
    """
    active = rules if rules is not None else load_rules()
    subject_ids = {i.lower().strip() for i in subject.primary_identifiers}
    counts: dict[str, int] = {}
    seen: set[tuple[str, tuple[str, ...]]] = set()
    out: list[SeedCandidate] = []

    for entity in entities:
        for rule in active:
            if not rule.matches(entity):
                continue
            if counts.get(rule.name, 0) >= rule.max_per_run:
                continue
            ident = entity.label.strip()
            if ident.lower() in subject_ids:
                continue
            cand = SeedCandidate(
                subject_type=rule.subject_type,
                identifiers=[ident],
                context=f"Auto-seeded from {entity.type.value} '{ident}' (rule: {rule.name}).",
                priority=rule.priority * max(0.5, entity.confidence),
                rationale=f"Rule '{rule.name}' matched a {entity.type.value} entity.",
                source="rule",
                rule=rule.name,
            )
            key = cand.dedup_key()
            if key in seen:
                continue
            seen.add(key)
            counts[rule.name] = counts.get(rule.name, 0) + 1
            out.append(cand)

    for name, hook in _HOOKS.items():
        try:
            out.extend(hook(list(entities), subject))
        except Exception as exc:
            log.warning("seeding.hook_failed", hook=name, error=str(exc))

    return out
