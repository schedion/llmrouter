"""Routing core that selects providers and enforces circuit breakers."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

from app.circuit_breaker import CircuitBreaker
from app.config import ProviderConfig, RouterConfig
from app.providers import Provider, ProviderError, ProviderFactory
from app.schemas import ChatCompletionRequest

logger = logging.getLogger(__name__)


class NoAvailableProviderError(Exception):
    """Raised when no provider can service a request."""


@dataclass
class ProviderEntry:
    provider: Provider
    circuit_breaker: CircuitBreaker
    priority: int


@dataclass
class ProviderResult:
    provider_name: str
    content: str


class Router:
    def __init__(self, config: RouterConfig) -> None:
        self._providers: List[ProviderEntry] = self._build_providers(config.providers)

    def _build_providers(self, provider_configs: Sequence[ProviderConfig]) -> List[ProviderEntry]:
        entries: List[ProviderEntry] = []
        for cfg in sorted(provider_configs, key=lambda c: c.priority):
            provider = ProviderFactory.create(cfg)
            breaker = CircuitBreaker(cfg.circuit_breaker)
            entries.append(ProviderEntry(provider=provider, circuit_breaker=breaker, priority=cfg.priority))
        return entries

    @property
    def provider_count(self) -> int:
        return len(self._providers)

    def catalog(self) -> List[Dict[str, Any]]:
        catalog: Dict[str, Dict[str, Any]] = {}
        for entry in self._providers:
            cfg = entry.provider.config
            canonical = cfg.canonical_model or cfg.model or cfg.name
            model_info = catalog.setdefault(
                canonical,
                {
                    "id": canonical,
                    "aliases": sorted({canonical, *cfg.aliases}),
                    "providers": {},
                },
            )

            # Merge aliases from each provider config
            current_aliases = set(model_info.get("aliases", []))
            current_aliases.update(cfg.aliases)
            current_aliases.add(canonical)
            model_info["aliases"] = sorted(current_aliases)

            provider_key = cfg.type
            model_info["providers"][provider_key] = {
                "name": cfg.name,
                "model": cfg.model,
                "base_url": cfg.base_url,
                "allow_paid": cfg.allow_paid,
                "extra_headers": cfg.extra_headers,
            }

        return sorted(catalog.values(), key=lambda item: item["id"])

    @staticmethod
    def _normalize_model_name(name: str) -> str:
        normalized = name.strip().lower()
        normalized = normalized.replace(":free", "")
        normalized = normalized.replace("_", "-")
        normalized = re.sub(r"\s+", "-", normalized)
        normalized = re.sub(r"-+", "-", normalized)
        return normalized

    async def chat_completion(self, payload: ChatCompletionRequest) -> ProviderResult:
        last_error: Exception | None = None

        requested_model = (payload.model or "").lower()
        if requested_model:
            normalized = self._normalize_model_name(payload.model or "")
            candidate_providers = [
                entry
                for entry in self._providers
                if normalized in {alias.lower() for alias in entry.provider.config.aliases}
                or normalized == self._normalize_model_name(entry.provider.config.canonical_model or "")
            ]
            provider_sequence = candidate_providers if candidate_providers else self._providers
        else:
            provider_sequence = self._providers

        for entry in provider_sequence:
            if not await entry.circuit_breaker.allow_request():
                logger.debug("Circuit open for provider '%s'", entry.provider.config.name)
                continue

            try:
                result = await entry.provider.complete(payload)
            except ProviderError as exc:
                last_error = exc
                await entry.circuit_breaker.record_failure()
                logger.warning(
                    "Provider '%s' failed to fulfill request: %s",
                    entry.provider.config.name,
                    exc,
                )
                continue
            except Exception as exc:  # pragma: no cover - defensive catch
                last_error = exc
                await entry.circuit_breaker.record_failure()
                logger.exception("Unexpected error from provider '%s'", entry.provider.config.name)
                continue

            await entry.circuit_breaker.record_success()
            return ProviderResult(provider_name=entry.provider.config.name, content=result)

        if last_error is not None:
            raise NoAvailableProviderError(str(last_error))
        raise NoAvailableProviderError("All providers are unavailable (circuits open)")
