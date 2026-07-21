"""Application configuration.

All settings load from environment variables (prefix ``AETHERSEED_``) and/or a
``.env`` file via :mod:`pydantic_settings`. Secrets are never hardcoded; the two
cloud API keys read from their conventional unprefixed names.

Example
-------
>>> from aetherseed.config import get_settings
>>> settings = get_settings()
>>> settings.acq_max_concurrency > 0
True
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "prod"]
AIBackend = Literal["ollama", "openai", "anthropic", "null"]
VectorBackend = Literal["memory", "chroma", "none"]
GraphBackend = Literal["networkx", "neo4j"]
SearchBackend = Literal["none", "duckduckgo", "searxng"]


class Settings(BaseSettings):
    """Typed, validated application settings.

    Grouped by concern. Field names map to ``AETHERSEED_<UPPER_FIELD_NAME>``.
    """

    model_config = SettingsConfigDict(
        env_prefix="AETHERSEED_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,  # allow constructing by field name even when aliased
    )

    # --- Core ---
    env: Environment = "dev"
    log_level: str = "INFO"
    log_json: bool = False
    data_dir: Path = Path("./data")

    # --- Database ---
    database_url: str = "sqlite+pysqlite:///./data/aetherseed.sqlite3"

    # --- Acquisition ---
    acq_user_agent: str = "AetherSeedBot/0.1 (+https://veralogix.example/osint)"
    acq_respect_robots: bool = True
    acq_max_concurrency: int = Field(default=8, ge=1, le=256)
    acq_polite_delay_ms: int = Field(default=750, ge=0)
    acq_request_timeout_s: float = Field(default=30.0, gt=0)
    acq_max_asset_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    acq_egress_allowlist: str = ""  # comma-separated hosts/CIDRs; empty => public only
    acq_proxy_url: str | None = None

    # --- Search (seed discovery from bare names) ---
    search_backend: SearchBackend = "none"  # opt-in; none => only URL/domain seeds
    search_max_results: int = Field(default=10, ge=1, le=50)
    searxng_url: str | None = None  # required when search_backend == "searxng"

    # --- AI (AetherMind) ---
    ai_backend: AIBackend = "ollama"
    ai_ollama_host: str = "http://localhost:11434"
    ai_model: str = "llama3.1"
    ai_embed_model: str = "nomic-embed-text"
    ai_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    ai_enable_cloud_fallback: bool = False
    openai_api_key: str | None = Field(
        default=None, validation_alias=AliasChoices("OPENAI_API_KEY", "AETHERSEED_OPENAI_API_KEY")
    )
    anthropic_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "AETHERSEED_ANTHROPIC_API_KEY"),
    )

    # --- Enrichment ---
    # OpenCorporates powers the company-registry enricher (includes SA/CIPC data).
    # Without a token the registry enricher reports "not_configured".
    opencorporates_api_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENCORPORATES_API_TOKEN", "AETHERSEED_OPENCORPORATES_API_TOKEN"
        ),
    )
    rdap_base_url: str = "https://rdap.org"  # WHOIS-over-RDAP bootstrap

    # --- Vector store / RAG ---
    # "memory" (default) is a zero-dependency in-process lexical index that works
    # fully offline; "chroma" adds dense embeddings (needs the `ai` extra);
    # "none" disables corpus retrieval entirely.
    vector_backend: VectorBackend = "memory"
    vector_dir: Path = Path("./data/chroma")
    rag_enabled: bool = True  # attach retrieved evidence snippets to leads
    rag_snippets_per_lead: int = Field(default=2, ge=0, le=10)
    rag_min_score: float = Field(default=0.0, ge=0.0)  # drop snippets below this

    # --- Seeding safety budgets ---
    seed_max_new_per_hour: int = Field(default=100, ge=0)
    seed_require_approval: bool = True
    seed_budget_usd: float = Field(default=0.0, ge=0.0)

    # --- Queue / cache ---
    redis_url: str | None = None

    # --- Graph ---
    graph_backend: GraphBackend = "networkx"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str | None = None

    # --- API / web ---
    api_host: str = "0.0.0.0"  # noqa: S104 — intended for containerized bind
    api_port: int = Field(default=8000, ge=1, le=65535)
    cors_origins: str = "http://localhost:3000"

    # --- Compliance (POPIA) ---
    pii_redaction: bool = True
    retention_days: int = Field(default=365, ge=0)

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        allowed = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in allowed:
            raise ValueError(f"log_level must be one of {sorted(allowed)}")
        return upper

    @property
    def is_prod(self) -> bool:
        """Whether the platform runs in production mode."""
        return self.env == "prod"

    @property
    def cors_origin_list(self) -> list[str]:
        """CORS origins parsed into a list."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def egress_allowlist_entries(self) -> list[str]:
        """Egress allowlist parsed into a list of hosts/CIDRs."""
        return [e.strip() for e in self.acq_egress_allowlist.split(",") if e.strip()]

    def ensure_dirs(self) -> None:
        """Create the runtime data directories if they do not yet exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "assets").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "audit").mkdir(parents=True, exist_ok=True)
        if self.vector_backend == "chroma":
            self.vector_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide cached settings instance.

    Cached so configuration is read once. Call ``get_settings.cache_clear()`` in
    tests to force a reload after mutating the environment.
    """
    return Settings()
