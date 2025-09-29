"""Routing core that selects providers and enforces circuit breakers."""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from app.circuit_breaker import CircuitBreaker
from app.config import ProviderConfig, RouterConfig
from app.providers import Provider, ProviderError, ProviderFactory
from app.schemas import ChatCompletionRequest, ToolCall
from app.semantic_cache import SemanticCache

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
    tool_calls: Optional[List[ToolCall]] = None


class Router:
    def __init__(self, config: RouterConfig) -> None:
        self._providers: List[ProviderEntry] = self._build_providers(config.providers)
        self._cache_ttl = float(os.environ.get("LLMROUTER_CACHE_TTL", "0") or 0)
        self._cache: Dict[str, tuple[ProviderResult, float]] = {}
        self._cache_lock = None
        self._semantic_enabled = os.environ.get("LLMROUTER_SEMANTIC_CACHE_ENABLED", "false").lower() not in {"0", "false", "off"}
        self._semantic_threshold_default = float(os.environ.get("LLMROUTER_SEMANTIC_CACHE_THRESHOLD", "0.85") or 0.0)
        semantic_model = os.environ.get("LLMROUTER_SEMANTIC_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
        semantic_max_entries = int(os.environ.get("LLMROUTER_SEMANTIC_CACHE_MAX_ENTRIES", "512") or 512)
        self._semantic_cache = (
            SemanticCache(model_name=semantic_model, max_entries=semantic_max_entries)
            if self._semantic_enabled
            else None
        )

    def _build_providers(self, provider_configs: Sequence[ProviderConfig]) -> List[ProviderEntry]:
        entries: List[ProviderEntry] = []
        for cfg in sorted(provider_configs, key=lambda c: c.priority):
            provider = ProviderFactory.create(cfg)
            breaker = CircuitBreaker(cfg.circuit_breaker)
            entries.append(ProviderEntry(provider=provider, circuit_breaker=breaker, priority=cfg.priority))
        return entries

    @property
    def _ttl_enabled(self) -> bool:
        return self._cache_ttl > 0

    async def _ensure_cache_lock(self) -> None:
        if self._cache_lock is None:
            import asyncio

            self._cache_lock = asyncio.Lock()

    def _cache_key(self, payload: ChatCompletionRequest) -> str:
        body = payload.dict(
            include={"model", "messages", "temperature", "top_p", "max_tokens", "tools", "tool_choice"},
            exclude_none=True,
        )
        return json.dumps(body, sort_keys=True, ensure_ascii=False)

    async def _cache_get(self, key: str) -> Optional[ProviderResult]:
        if not self._ttl_enabled:
            return None
        await self._ensure_cache_lock()
        async with self._cache_lock:
            entry = self._cache.get(key)
            if not entry:
                return None
            result, expires_at = entry
            if expires_at < time.time():
                del self._cache[key]
                return None
            return ProviderResult(
                provider_name=result.provider_name,
                content=result.content,
                tool_calls=[tc.copy(deep=True) for tc in result.tool_calls] if result.tool_calls else None,
            )

    async def _cache_set(self, key: str, result: ProviderResult) -> None:
        if not self._ttl_enabled:
            return
        await self._ensure_cache_lock()
        expires_at = time.time() + self._cache_ttl
        cached = ProviderResult(
            provider_name=result.provider_name,
            content=result.content,
            tool_calls=[tc.copy(deep=True) for tc in result.tool_calls] if result.tool_calls else None,
        )
        async with self._cache_lock:
            self._cache[key] = (cached, expires_at)

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

    async def chat_completion(
        self,
        payload: ChatCompletionRequest,
        *,
        semantic_threshold: Optional[float] = None,
        semantic_enabled: Optional[bool] = None,
    ) -> ProviderResult:
        last_error: Exception | None = None

        cache_key = self._cache_key(payload)
        cached_result = await self._cache_get(cache_key)
        if cached_result is not None:
            return cached_result

        semantic_active = semantic_enabled if semantic_enabled is not None else self._semantic_enabled
        semantic_data: Optional[Dict[str, Any]] = None
        if semantic_active and self._semantic_cache is not None:
            threshold = semantic_threshold if semantic_threshold is not None else self._semantic_threshold_default
            if threshold > 0:
                semantic_data = await self._semantic_cache.get(payload, threshold=threshold)
        if semantic_data:
            tool_calls = SemanticCache.deserialize_tool_calls(semantic_data.get("tool_calls"))
            return ProviderResult(
                provider_name=semantic_data["provider_name"],
                content=semantic_data["content"],
                tool_calls=tool_calls,
            )

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
                payload.tool_calls = None
                await entry.circuit_breaker.record_failure()
                logger.warning(
                    "Provider '%s' failed to fulfill request: %s",
                    entry.provider.config.name,
                    exc,
                )
                continue
            except Exception as exc:  # pragma: no cover - defensive catch
                last_error = exc
                payload.tool_calls = None
                await entry.circuit_breaker.record_failure()
                logger.exception("Unexpected error from provider '%s'", entry.provider.config.name)
                continue

            await entry.circuit_breaker.record_success()
            tool_calls = getattr(payload, "tool_calls", None)
            provider_result = ProviderResult(
                provider_name=entry.provider.config.name,
                content=result,
                tool_calls=[tc.copy(deep=True) for tc in tool_calls] if tool_calls else None,
            )
            payload.tool_calls = None
            await self._cache_set(cache_key, provider_result)
            if semantic_active and self._semantic_cache is not None:
                await self._semantic_cache.add(
                    payload,
                    {
                        "provider_name": provider_result.provider_name,
                        "content": provider_result.content,
                        "tool_calls": SemanticCache.serialize_tool_calls(provider_result.tool_calls),
                    },
                )
            return provider_result

        if last_error is not None:
            raise NoAvailableProviderError(str(last_error))
        raise NoAvailableProviderError("All providers are unavailable (circuits open)")
