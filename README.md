# llmrouter

llmrouter is a programmable router that sits in front of multiple large language model (LLM) providers. It focuses on keeping inference costs low while still giving developers an OpenAI-compatible API surface. The project aims to make it easy to mix free tiers, low-cost models, and premium fallbacks without changing application code.

_Note: this project is vibe coded right now—expect rapid changes and the occasional rough edge._

## Why it exists
- Many open-source or promotional LLM endpoints are free but unreliable; llmrouter keeps them in rotation while protecting your app from hard failures.
- Paid APIs often bill aggressively; llmrouter steers requests toward the least expensive option that can satisfy quality and latency requirements.
- Teams migrating away from OpenAI want a drop-in replacement; llmrouter mirrors the `v1/chat/completions` API and can add more surface area over time.

## Core capabilities
- **Multi-provider routing:** Route each request to whichever provider best matches the configured policy (cost, latency, model availability).
- **OpenAI-compatible endpoint:** Present an `/v1/chat/completions` entry point so existing SDKs keep working.
- **Circuit breaking:** Automatically pause providers that exceed failure thresholds and retry against healthy alternatives before bubbling errors.
- **Adaptive caching:** Cache responses for deterministic prompts so repeated requests are free; cache backends are pluggable (Redis, in-memory, etc.).
- **Provider health tracking:** Track latency, error rates, and rate-limit responses to inform routing decisions.
- **Policy hooks:** Expose a simple policy interface so teams can express custom routing logic (e.g., prefer GPU-backed models during business hours).

## High-level architecture
```
client SDK  →  llmrouter API  →  routing core  →  provider adapters
                              ↘ circuit breaker
                               ↘ response cache
```
- **Client compatibility:** Accepts OpenAI-style JSON requests and returns the same schema so SDKs and tools such as LangChain keep working.
- **Routing core:** Evaluates provider metadata and policy rules, runs pre-flight checks, and selects a target provider.
- **Provider adapters:** Thin wrappers around each upstream (OpenAI, Anthropic, Together, open-source hosts, etc.) to normalize requests/responses.
- **Circuit breaker:** Monitors failures per provider and temporarily removes them from rotation when they breach thresholds.
- **Cache layer:** Optional; stores successful responses keyed by provider/model plus a normalized prompt signature.

## Getting started
Implementation work is still in progress. The initial milestones:
1. Scaffold the OpenAI-compatible HTTP server (FastAPI/Express/etc.).
2. Define provider interface, routing policy abstraction, and configuration schema.
3. Implement a minimal policy that round-robins free-tier providers with paid fallback.
4. Add Redis-backed cache and basic in-memory circuit breaker.

Once the core is in place, setup will look roughly like:
```bash
# in the future
cp .env.example .env
# add credentials for the providers you want to use
# run the API server
make run
```
As the codebase matures this section will be expanded with concrete commands and deployment advice.

## Configuration concepts
- `providers`: List of upstreams, each with credentials, cost metadata, supported models, and optional usage caps.
- `policy`: Strategy that chooses a provider for each request (e.g., weighted cost vs. latency, or priority tiers).
- `breaker`: Thresholds for tripping the circuit (consecutive failures, failure rate over time window).
- `cache`: Backend selection and TTLs; can be disabled.
- `logging/metrics`: Hooks for Prometheus, OpenTelemetry, or custom sinks.

## Roadmap
- Least-cost routing based on live price sheets and token usage estimates.
- Per-provider quotas and rate-limit smoothing.
- Streaming support for chat completions.
- Tools endpoint compatibility (`/v1/responses`, `/v1/assistants`).
- Pluggable eval harness to measure quality and latency per provider.
- CLI for quick local benchmarking and cache priming.
- Deployment recipes (Docker Compose, Fly.io, serverless workers).

## Contributing
This project is at the design phase. Share ideas, provider support requests, or routing policies by opening issues. Once the scaffolding lands, contributions around provider adapters, caching backends, and observability will be especially helpful.

## License
llmrouter is released under [The Unlicense](https://unlicense.org/). See `LICENSE` for the full text.
