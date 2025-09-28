#!/usr/bin/env python3
"""CLI helper to inspect model availability across providers."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, List

import yaml

# Ensure the repository root (containing the `app` package) is importable when the
# script is executed with `python scripts/debug_models.py`.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.config import resolve_required_providers
from app.model_catalog import API_KEY_ENV

logger = logging.getLogger("debug_models")

SEED_PATH = Path("config/model_catalog_seed.yaml")


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="[%(levelname)s] %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def _ensure_env(providers: List[str]) -> Mapping[str, str]:
    env_values = {
        API_KEY_ENV[provider]: os.environ.get(API_KEY_ENV[provider], "")
        for provider in providers
        if provider in API_KEY_ENV
    }
    missing = [key for key, value in env_values.items() if not value]
    if missing:
        logger.warning(
            "Missing credentials for: %s (catalog download may fail)", ", ".join(missing)
        )
    return env_values


def _load_seed(path: Path = SEED_PATH) -> List[dict]:
    if not path.exists():
        raise FileNotFoundError(f"Seed file not found: {path}")
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    models = data.get("models", [])
    if not models:
        raise ValueError("Seed file does not contain any models")
    return models


def _resolve_providers(seed_models: List[dict], cli_providers: str | None) -> List[str]:
    if cli_providers:
        providers = [p.strip().lower() for p in cli_providers.split(",") if p.strip()]
        if providers:
            return providers
    env_value = os.environ.get("LLMROUTER_PROVIDERS", "")
    if env_value.strip():
        providers = [p.strip().lower() for p in env_value.split(",") if p.strip()]
        if providers:
            return providers
    provider_set = {
        provider
        for model in seed_models
        for provider in model.get("providers", {}).keys()
    }
    return sorted(provider_set)


def build_report(limit: int, show_all: bool, include_paid: bool, cli_providers: str | None) -> Dict[str, Any]:
    _ = (limit, show_all, include_paid)  # parameters kept for CLI compatibility
    seed_models = _load_seed()
    providers = _resolve_providers(seed_models, cli_providers)
    env = _ensure_env(providers)

    coverage: Dict[str, Dict[str, Any]] = {}
    common_models: List[str] = []

    for model in seed_models:
        canonical = model.get("canonical")
        if not canonical:
            continue
        aliases = sorted({canonical, *model.get("aliases", [])})
        provider_cfg = model.get("providers", {})
        entry: Dict[str, Any] = {"aliases": aliases}

        all_available = True
        for provider in providers:
            cfg = provider_cfg.get(provider)
            if not cfg:
                entry[provider] = {"available": False, "model": None}
                all_available = False
                continue
            model_id = cfg.get("model")
            if not model_id:
                entry[provider] = {"available": False, "model": None}
                all_available = False
                continue
            info = {
                "model": model_id,
                "available": True,
                "allow_paid": cfg.get("allow_paid", False),
            }
            entry[provider] = info
        entry["all_available"] = all_available
        coverage[canonical] = entry
        if all_available:
            common_models.append(canonical)

    report: Dict[str, Any] = {
        "env": {key: "set" for key in env.keys()},
        "providers": providers,
        "coverage": coverage,
        "common_models": sorted(common_models),
    }

    return report


def format_human(report: Mapping[str, Any], limit: int) -> str:
    lines: list[str] = []

    lines.append("Environment variables: " + ", ".join(report["env"].keys()))
    lines.append("Providers required: " + ", ".join(report["providers"]))

    lines.append("\nCatalog contains %d canonical models" % len(report["common_models"]))

    lines.append("\nCanonical coverage")
    for canonical, entry in report["coverage"].items():
        status = "ready" if entry.get("all_available") else "incomplete"
        lines.append(f"  {canonical}: {status}")
        for provider_name, info in entry.items():
            if provider_name in {"all_available", "aliases"}:
                continue
            marker = "✔" if info.get("available") else "✖"
            suffix = " (paid)" if info.get("allow_paid") else ""
            model_name = info.get("model", "n/a")
            lines.append(f"    {marker} {provider_name}: {model_name}{suffix}")

    lines.append("\nCommon models: " + (", ".join(report["common_models"]) or "(none)"))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect upstream model availability for llmrouter")
    parser.add_argument("--limit", type=int, default=20, help="Limit number of models displayed per provider")
    parser.add_argument("--full", action="store_true", help="Show full model lists even if long")
    parser.add_argument("--json", action="store_true", help="Output JSON instead of text")
    parser.add_argument(
        "--include-paid",
        action="store_true",
        help="Include paid OpenRouter models in the listing",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose logging",
    )
    parser.add_argument(
        "--providers",
        help="Override provider list (comma separated)",
    )
    args = parser.parse_args()

    _configure_logging(args.debug)

    try:
        report = build_report(
            limit=args.limit,
            show_all=args.full,
            include_paid=args.include_paid,
            cli_providers=args.providers,
        )
    except AutoConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.json:
        json.dump(report, sys.stdout, indent=2)
        sys.stdout.write("\n")
    else:
        print(format_human(report, args.limit))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
