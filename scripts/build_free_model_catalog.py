#!/usr/bin/env python3
"""Generate the curated free-model catalog for llmrouter."""

from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import httpx
import yaml

LOGGER = logging.getLogger("build_catalog")

API_KEY_ENV = {
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "nvidia_nim": "NVIDIA_NIM_KEY",
    "huggingface": "HUGGINGFACE_API_TOKEN",
}

DEFAULT_BREAKER = {"failure_threshold": 3, "recovery_time_seconds": 15}
DEFAULT_TIMEOUT = httpx.Timeout(15.0)
SEED_PATH = Path("config/model_catalog_seed.yaml")


def load_seed(path: Path = SEED_PATH) -> List[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Seed file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    models = data.get("models", [])
    if not models:
        raise ValueError("Seed file does not contain any models")
    return models


def ensure_keys(providers: List[str]) -> None:
    missing = []
    for provider in providers:
        env_key = API_KEY_ENV.get(provider)
        if env_key and not os.environ.get(env_key):
            missing.append(env_key)
    if missing:
        raise RuntimeError(f"Missing environment variables: {', '.join(missing)}")


def fetch_groq_models(api_key: str) -> List[str]:
    url = "https://api.groq.com/openai/v1/models"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
        response = client.get(url, headers=headers)
    response.raise_for_status()
    payload = response.json()
    return sorted({item["id"] for item in payload.get("data", []) if "id" in item})


def _is_free_openrouter(item: Dict[str, object]) -> bool:
    pricing = item.get("pricing", {}) or {}

    def to_float(value: object) -> Optional[float]:
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    prompt = to_float(pricing.get("prompt"))
    completion = to_float(pricing.get("completion"))
    if prompt is not None or completion is not None:
        prompt_ok = prompt is None or prompt == 0.0
        completion_ok = completion is None or completion == 0.0
        if prompt_ok and completion_ok:
            return True

    identifier = item.get("id", "")
    return isinstance(identifier, str) and identifier.endswith(":free")


def _clean_openrouter_identifier(identifier: str) -> str:
    return identifier.split(":", 1)[0]


def fetch_openrouter_models(api_key: str) -> Tuple[List[str], List[str]]:
    url = "https://openrouter.ai/api/v1/models"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
        response = client.get(url, headers=headers)
    response.raise_for_status()
    payload = response.json()
    models = payload.get("data") or payload.get("models") or []

    free_ids: set[str] = set()
    all_ids: set[str] = set()
    for item in models:
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        if not isinstance(identifier, str):
            continue
        clean = _clean_openrouter_identifier(identifier)
        all_ids.add(clean)
        if _is_free_openrouter(item):
            free_ids.add(clean)
    return sorted(free_ids), sorted(all_ids)


def fetch_nvidia_nim_models(api_key: str) -> List[str]:
    url = "https://integrate.api.nvidia.com/v1/models"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=DEFAULT_TIMEOUT) as client:
        response = client.get(url, headers=headers)
    response.raise_for_status()
    payload = response.json()
    models = payload.get("data") or payload.get("models") or []
    ids = [item.get("id") for item in models if isinstance(item, dict) and item.get("id")]
    return sorted(ids)


def build_catalog(providers: List[str], seed_models: List[dict]) -> Tuple[List[dict], Dict[str, List[str]]]:
    required = [provider.lower() for provider in providers]
    required_set = set(required)

    availability: Dict[str, set[str]] = {}
    if "groq" in required_set:
        LOGGER.info("Fetching Groq model list")
        availability["groq"] = set(fetch_groq_models(os.environ[API_KEY_ENV["groq"]]))
    if "openrouter" in required_set:
        LOGGER.info("Fetching OpenRouter model list")
        free_ids, _ = fetch_openrouter_models(os.environ[API_KEY_ENV["openrouter"]])
        availability["openrouter"] = set(free_ids)
    if "nvidia_nim" in required_set:
        LOGGER.info("Fetching NVIDIA NIM model list")
        availability["nvidia_nim"] = set(fetch_nvidia_nim_models(os.environ[API_KEY_ENV["nvidia_nim"]]))

    entries: List[dict] = []
    for item in seed_models:
        canonical = item.get("canonical")
        if not canonical:
            continue
        seed_providers: Dict[str, dict] = item.get("providers", {})
        if not required_set.issubset(seed_providers.keys()):
            LOGGER.warning(
                "Skipping '%s' because not all providers are defined in the seed",
                canonical,
            )
            continue

        aliases = sorted({canonical, *item.get("aliases", [])})
        provider_payload: Dict[str, dict] = {}

        for provider in required:
            cfg = seed_providers[provider]
            model_id = cfg.get("model")
            if not model_id:
                LOGGER.warning(
                    "Skipping '%s' because provider '%s' has no model mapping",
                    canonical,
                    provider,
                )
                provider_payload = None
                break

            available_models = availability.get(provider)
            if available_models is not None:
                candidate_id = model_id
                if provider == "openrouter":
                    candidate_id = _clean_openrouter_identifier(model_id)
                if candidate_id not in available_models:
                    LOGGER.warning(
                        "Skipping '%s' because provider '%s' does not advertise model '%s'",
                        canonical,
                        provider,
                        model_id,
                    )
                    provider_payload = None
                    break

            provider_payload[provider] = {
                "model": model_id,
                "api_key_env": cfg.get("api_key_env", API_KEY_ENV.get(provider, "")),
                "base_url": cfg.get("base_url"),
                "extra_headers": cfg.get("extra_headers", {}) or {},
                "allow_paid": cfg.get("allow_paid", False),
                "priority_offset": cfg.get("priority_offset", 0),
                "circuit_breaker": cfg.get("circuit_breaker", DEFAULT_BREAKER),
                "mock_failure_rate": cfg.get("mock_failure_rate", 0.0),
            }

        if not provider_payload:
            continue

        entries.append(
            {
                "canonical": canonical,
                "aliases": aliases,
                "providers": provider_payload,
            }
        )

    LOGGER.info("Generated %d canonical entries", len(entries))
    return entries, availability


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate free model catalog")
    parser.add_argument(
        "--providers",
        default="groq,openrouter,nvidia_nim",
        help="Comma-separated provider list (default: groq,openrouter,nvidia_nim)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("generated/model_index.json"),
        help="Where to write catalog JSON",
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    parser.add_argument("--log", default="INFO", help="Logging level (default: INFO)")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log.upper(), logging.INFO), format="[%(levelname)s] %(message)s")

    providers = [provider.strip().lower() for provider in args.providers.split(",") if provider.strip()]
    if not providers:
        raise ValueError("No providers specified")

    ensure_keys(providers)
    seed_models = load_seed()
    entries, availability = build_catalog(providers, seed_models)

    provider_catalogs = {
        name: sorted(models)
        for name, models in availability.items()
    }

    payload = {
        "providers": providers,
        "models": entries,
        "provider_catalogs": provider_catalogs,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2 if args.pretty else None, ensure_ascii=False)
        handle.write("\n")

    LOGGER.info("Wrote catalog to %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
