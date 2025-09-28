"""Configuration models and loader for llmrouter."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Iterable, List, Mapping, Optional

import yaml
from pydantic import BaseModel, Field, validator

from app.model_catalog import AutoConfigError, ProviderTemplate, discover_provider_templates

logger = logging.getLogger(__name__)


class CircuitBreakerConfig(BaseModel):
    failure_threshold: int = Field(3, ge=1)
    recovery_time_seconds: int = Field(30, ge=1)


class ProviderConfig(BaseModel):
    name: str
    type: str = Field("echo")
    priority: int = Field(100, ge=0)
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)
    mock_failure_rate: float = Field(0.0, ge=0.0, le=1.0)
    model: Optional[str] = None
    base_url: Optional[str] = None
    api_key_env: Optional[str] = None
    extra_headers: dict[str, str] = Field(default_factory=dict)
    canonical_model: Optional[str] = None
    aliases: List[str] = Field(default_factory=list)
    allow_paid: bool = False

    @validator("type")
    def validate_type(cls, value: str) -> str:  # noqa: N805
        supported = {"echo", "huggingface", "groq", "openrouter", "nvidia_nim"}
        if value not in supported:
            raise ValueError(f"Unsupported provider type '{value}'. Supported types: {sorted(supported)}")
        return value


class RouterConfig(BaseModel):
    providers: List[ProviderConfig]

    @validator("providers")
    def validate_providers(cls, value: List[ProviderConfig]) -> List[ProviderConfig]:  # noqa: N805
        if not value:
            raise ValueError("At least one provider must be configured")
        return value


FALLBACK_CONFIG = RouterConfig(
    providers=[
        ProviderConfig(name="local-echo", type="echo", priority=0),
    ]
)


def resolve_required_providers(env: Optional[Mapping[str, str]] = None) -> List[str]:
    env = env or os.environ
    raw = env.get("LLMROUTER_PROVIDERS", "")
    if not raw.strip():
        return ["groq", "openrouter", "nvidia_nim"]
    providers = []
    seen = set()
    for value in raw.split(","):
        provider = value.strip().lower()
        if not provider:
            continue
        if provider not in seen:
            providers.append(provider)
            seen.add(provider)
    return providers


def _provider_config_from_template(
    provider_type: str,
    canonical_name: str,
    template: ProviderTemplate,
    priority: int,
    aliases: List[str],
) -> ProviderConfig:
    breaker_payload = template.circuit_breaker or {}
    circuit = CircuitBreakerConfig(**breaker_payload)
    name = template.name_suffix or canonical_name
    provider_name = f"{provider_type}-{name}"
    return ProviderConfig(
        name=provider_name,
        type=provider_type,
        priority=priority + template.priority_offset,
        circuit_breaker=circuit,
        mock_failure_rate=template.mock_failure_rate,
        model=template.model,
        base_url=template.base_url,
        api_key_env=template.api_key_env,
        extra_headers=dict(template.extra_headers),
        canonical_model=canonical_name,
        aliases=aliases,
        allow_paid=template.allow_paid,
    )


def _canonical_from_provider_name(name: str) -> str:
    parts = name.split("-", 1)
    return parts[1] if len(parts) > 1 else name


def build_dynamic_default_config() -> RouterConfig:
    required_providers = resolve_required_providers(os.environ)
    template_map = discover_provider_templates(required_providers=required_providers)
    if not template_map:
        raise AutoConfigError("No common models found across providers")

    providers: List[ProviderConfig] = []
    for index, canonical in enumerate(sorted(template_map.keys())):
        model_entry = template_map[canonical]
        templates = model_entry.providers
        aliases = sorted(model_entry.aliases)
        priority_base = index * 10
        for provider_type, template in templates.items():
            providers.append(
                _provider_config_from_template(
                    provider_type=provider_type,
                    canonical_name=canonical,
                    template=template,
                    priority=priority_base,
                    aliases=aliases,
                )
            )

    return RouterConfig(providers=providers)


def load_router_config(config_path: Optional[str] = None) -> RouterConfig:
    """Load router configuration from YAML, falling back to defaults if absent."""
    path = Path(config_path or os.environ.get("LLMROUTER_CONFIG", "config/providers.yaml"))

    if not path.exists():
        logger.warning("Config file '%s' not found. Attempting automatic provider discovery.", path)
        try:
            dynamic_config = build_dynamic_default_config()
            providers_list = resolve_required_providers(os.environ)
            logger.info(
                "Automatically configured %d providers spanning %d model groups using providers %s",
                len(dynamic_config.providers),
                len({_canonical_from_provider_name(cfg.name) for cfg in dynamic_config.providers}),
                ", ".join(providers_list),
            )
            return dynamic_config
        except AutoConfigError as exc:
            logger.warning("Automatic provider discovery failed: %s", exc)
            return FALLBACK_CONFIG

    with path.open("r", encoding="utf-8") as handle:
        try:
            raw = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:  # pragma: no cover - defensive logging
            logger.error("Failed to parse config file '%s': %s", path, exc)
            raise

    try:
        return RouterConfig.parse_obj(raw)
    except Exception as exc:
        logger.error("Invalid router configuration in '%s': %s", path, exc)
        raise
