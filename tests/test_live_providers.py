import os
from typing import Any, Dict, Iterator, List, Tuple

import pytest

from app.config import CircuitBreakerConfig, ProviderConfig
from app.model_catalog import load_model_index, PROVIDER_API_KEYS
from app.providers import ProviderFactory
from app.schemas import ChatCompletionRequest, Message

RUN_LIVE = os.environ.get("LLMROUTER_RUN_LIVE_TESTS") == "1"

if not RUN_LIVE:
    pytest.skip("Set LLMROUTER_RUN_LIVE_TESTS=1 to exercise live provider tests", allow_module_level=True)


def _iter_catalog_entries() -> Iterator[Tuple[str, str, Dict[str, Any], List[str]]]:
    catalog = load_model_index()
    for model in catalog.get("models", []):
        canonical = model.get("canonical")
        aliases = model.get("aliases", [])
        providers = model.get("providers", {})
        for provider_name, payload in providers.items():
            yield canonical, provider_name, payload, aliases


def _has_credentials(provider: str) -> bool:
    env_key = PROVIDER_API_KEYS.get(provider)
    return bool(env_key and os.environ.get(env_key))


@pytest.mark.parametrize("canonical,provider_name,payload,aliases", list(_iter_catalog_entries()))
@pytest.mark.asyncio
async def test_live_provider_completion(canonical: str, provider_name: str, payload: Dict[str, Any], aliases: List[str]) -> None:
    if payload.get("allow_paid"):
        pytest.skip(f"Skipping paid provider mapping for {canonical}")

    if not _has_credentials(provider_name):
        pytest.skip(f"Missing credentials for provider '{provider_name}'")

    model_id = payload.get("model")
    if not model_id:
        pytest.skip("No model id declared in catalog entry")

    provider_config = ProviderConfig(
        name=f"test-{provider_name}-{canonical}",
        type=provider_name,
        priority=0,
        circuit_breaker=CircuitBreakerConfig(),
        mock_failure_rate=0.0,
        model=model_id,
        base_url=payload.get("base_url"),
        api_key_env=payload.get("api_key_env"),
        extra_headers=payload.get("extra_headers", {}),
        canonical_model=canonical,
        aliases=aliases,
        allow_paid=payload.get("allow_paid", False),
    )

    provider = ProviderFactory.create(provider_config)

    request = ChatCompletionRequest(
        model=model_id,
        messages=[
            Message(role="system", content="You are a terse assistant."),
            Message(role="user", content="ping"),
        ],
        temperature=0.0,
    )

    result = await provider.complete(request)

    assert isinstance(result, str) and result.strip(), f"Empty response from {provider_name} for {canonical}"
