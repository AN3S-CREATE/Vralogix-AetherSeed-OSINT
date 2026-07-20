"""Pluggable LLM backends.

Three implementations, one factory:

* :class:`OllamaBackend` — local models over Ollama's HTTP API (no heavy client
  dependency; only ``httpx``, which is already core).
* :class:`AnthropicBackend` — optional cloud fallback behind a feature flag +
  API key. Never used unless both are set.
* :class:`NullBackend` — always available, never calls a model. Structured calls
  raise so the engine knows to use its deterministic heuristics instead.

Structured output is obtained by asking the model for a single JSON object that
conforms to a Pydantic model's JSON Schema, then validating with a repair-retry
loop — robust across models and requires no extra libraries.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError

from aetherseed.config import Settings, get_settings
from aetherseed.errors import BackendUnavailableError, ConfigurationError
from aetherseed.logging import get_logger

log = get_logger(__name__)

_STRUCTURED_SUFFIX = (
    "\n\nRespond with a SINGLE valid JSON object and nothing else — no markdown "
    "fences, no commentary. It MUST validate against this JSON Schema:\n{schema}"
)


def _extract_json(text: str) -> dict[str, Any]:
    """Extract the first balanced JSON object from a model response."""
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in model output")
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start : i + 1])  # type: ignore[no-any-return]
    raise ValueError("unbalanced JSON object in model output")


class _StructuredMixin:
    """Shared structured-output loop for HTTP-backed backends."""

    async def complete(self, prompt: str, *, system: str | None = None) -> str:  # pragma: no cover
        raise NotImplementedError

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        schema_text = json.dumps(schema.model_json_schema())
        sys = (system or "You are a precise information-extraction engine.") + _STRUCTURED_SUFFIX.format(
            schema=schema_text
        )
        last_err = ""
        current = prompt
        for _ in range(3):
            raw = await self.complete(current, system=sys)
            try:
                return schema.model_validate(_extract_json(raw))
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                last_err = str(exc)
                current = (
                    f"{prompt}\n\nYour previous response was invalid ({last_err}). "
                    "Return corrected JSON only."
                )
        raise BackendUnavailableError(
            f"structured output failed after retries: {last_err}", context={"schema": schema.__name__}
        )

    async def embed(self, texts: Any) -> list[list[float]]:  # pragma: no cover - overridden
        return []


class OllamaBackend(_StructuredMixin):
    """Local LLM backend via the Ollama HTTP API."""

    name = "ollama"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self.model = self._settings.ai_model
        self._host = self._settings.ai_ollama_host.rstrip("/")
        self._temperature = self._settings.ai_temperature

    def available(self) -> bool:
        try:
            resp = httpx.get(f"{self._host}/api/tags", timeout=2.0)
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def complete(self, prompt: str, *, system: str | None = None) -> str:
        payload = {
            "model": self.model,
            "messages": (
                ([{"role": "system", "content": system}] if system else [])
                + [{"role": "user", "content": prompt}]
            ),
            "stream": False,
            "options": {"temperature": self._temperature},
        }
        async with httpx.AsyncClient(timeout=120.0) as client:
            try:
                resp = await client.post(f"{self._host}/api/chat", json=payload)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                raise BackendUnavailableError(
                    f"Ollama request failed: {exc}", context={"host": self._host}
                ) from exc
            data = resp.json()
        return str(data.get("message", {}).get("content", ""))

    async def embed(self, texts: Any) -> list[list[float]]:
        out: list[list[float]] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            for text in texts:
                resp = await client.post(
                    f"{self._host}/api/embeddings",
                    json={"model": self._settings.ai_embed_model, "prompt": text},
                )
                resp.raise_for_status()
                out.append(list(resp.json().get("embedding", [])))
        return out


class AnthropicBackend(_StructuredMixin):
    """Optional cloud fallback (Anthropic Messages API). Off unless flag + key set."""

    name = "anthropic"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        if not self._settings.anthropic_api_key:
            raise ConfigurationError("ANTHROPIC_API_KEY not set")
        self.model = self._settings.ai_model if "claude" in self._settings.ai_model else "claude-sonnet-5"
        self._key = self._settings.anthropic_api_key

    def available(self) -> bool:
        return bool(self._settings.ai_enable_cloud_fallback and self._key)

    async def complete(self, prompt: str, *, system: str | None = None) -> str:
        headers = {
            "x-api-key": self._key or "",
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": 2048,
            "temperature": self._settings.ai_temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages", headers=headers, json=payload
            )
            resp.raise_for_status()
            data = resp.json()
        blocks = data.get("content", [])
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


class NullBackend:
    """No-op backend: always available, never contacts a model.

    Structured/complete calls raise :class:`BackendUnavailableError` so the
    engine falls back to deterministic heuristics.
    """

    name = "null"
    model = "none"

    def available(self) -> bool:
        return True

    async def complete(self, prompt: str, *, system: str | None = None) -> str:
        raise BackendUnavailableError("no LLM backend configured")

    async def structured[T: BaseModel](
        self, prompt: str, schema: type[T], *, system: str | None = None
    ) -> T:
        raise BackendUnavailableError("no LLM backend configured")

    async def embed(self, texts: Any) -> list[list[float]]:
        return []


def get_llm_backend(settings: Settings | None = None) -> Any:
    """Return the configured backend, honouring the local-first / cloud-flag policy.

    Resolution order:
    1. ``ai_backend == "null"`` -> :class:`NullBackend`.
    2. Ollama if reachable.
    3. Anthropic cloud *only if* the fallback flag and key are set.
    4. Otherwise :class:`NullBackend` (deterministic heuristics take over).
    """
    s = settings or get_settings()
    if s.ai_backend == "null":
        return NullBackend()

    if s.ai_backend == "ollama":
        backend = OllamaBackend(s)
        if backend.available():
            return backend
        log.warning("ai.ollama_unavailable", host=s.ai_ollama_host, model=s.ai_model)

    if s.ai_backend == "anthropic" or s.ai_enable_cloud_fallback:
        try:
            cloud = AnthropicBackend(s)
            if cloud.available():
                return cloud
        except ConfigurationError:
            pass

    log.info("ai.using_null_backend", reason="no reachable model; using deterministic heuristics")
    return NullBackend()
