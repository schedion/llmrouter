# llmrouter

llmrouter is a programmable router that sits in front of multiple large language model (LLM) providers. It focuses on keeping inference costs low while still giving developers an OpenAI-compatible API surface. The project aims to make it easy to mix free tiers, low-cost models, and premium fallbacks without changing application code.

_Note: this project is vibe coded right now—expect rapid changes and the occasional rough edge._

## Why it exists
- Many open-source or promotional LLM endpoints are free but unreliable; llmrouter keeps them in rotation while protecting your app from hard failures.
- Paid APIs often bill aggressively; llmrouter steers requests toward the least expensive option that can satisfy quality and latency requirements.
- Teams migrating away from OpenAI want a drop-in replacement; llmrouter mirrors the `v1/chat/completions` API and can add more surface area over time.
- The project draws inspiration from [RouteLLM](https://github.com/lm-sys/RouteLLM) while remaining lightweight enough for hobby projects and self-hosted deployments.

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
The current implementation ships a FastAPI server with an OpenAI-compatible `/v1/chat/completions` endpoint. Providers are defined through YAML config and an in-memory circuit breaker guards each provider.

### Configure providers
```bash
cp config/providers.example.yaml config/providers.yaml
```
Edit `config/providers.yaml` to list the providers you want in rotation. Each provider entry supports:
- `type`: Provider implementation. `echo` is the built-in mock that repeats the user prompt and is useful for local wiring.
- `priority`: Lower numbers are tried first. Tie-breakers fall back to declaration order.
- `circuit_breaker`: Failure threshold and recovery window before the provider re-enters rotation.
- `mock_failure_rate`: Optional float (0–1) to simulate flaky upstreams for testing circuit breaking.
- Provider-specific fields: external backends typically require `model`, `api_key_env`, and optionally `base_url` or `extra_headers`.

Starter templates are included in `config/providers.example.yaml` for:
- `huggingface`: Uses the Inference API. Set `api_key_env` to an environment variable containing your Hugging Face token (e.g., `HUGGINGFACE_API_TOKEN`).
- `groq`: Hits Groq's OpenAI-compatible endpoint. Provide `GROQ_API_KEY` (or whichever env var you choose) and, if needed, override `model` or `base_url`.
- `openrouter`: Targets OpenRouter's chat completions API. Supply `OPENROUTER_API_KEY` and set `extra_headers` to include a `HTTP-Referer` and `X-Title` per their requirements.

OpenRouter discovery prefers free models (pricing prompt/completion cost of `0` or IDs ending in `:free`). Certain canonical entries, such as `deepseek-r1-distill-llama-70b`, opt into paid availability when a free tier is not advertised. Use `python scripts/debug_models.py --include-paid` if you need to inspect the full catalog or confirm paid coverage.

Set `LLMROUTER_CONFIG` to point elsewhere if you prefer a different path. When the config file is missing, llmrouter now downloads a pre-generated catalog of free models (see below) so it can spin up quickly without probing upstream APIs. Export `LLMROUTER_MODEL_INDEX_URL` if you host the catalog somewhere other than the default GitHub location.

To troubleshoot discovery results, run `python scripts/debug_models.py` inside your virtualenv. Use `--json` for machine-readable output or `--full` to print the complete list of models returned by each upstream.

The bundled GitHub Action (`.github/workflows/model-catalog.yml`) invokes `scripts/build_free_model_catalog.py` to collate the free-tier model lists from Groq, OpenRouter, and NVIDIA NIM into `generated/model_index.json`. Secrets `GROQ_API_KEY`, `OPENROUTER_API_KEY`, and `NVIDIA_NIM_KEY` must be set in the repository before the workflow can run. The resulting catalog is published as an artifact, and llmrouter fetches it at startup (override the URL with `LLMROUTER_MODEL_INDEX_URL` if you host it elsewhere). Set `LLMROUTER_PROVIDERS` (e.g., `groq,openrouter,nvidia_nim`) to specify which providers must advertise a model before it is enabled locally, and ensure the corresponding API keys are available in your runtime environment. Use `python scripts/debug_models.py` to inspect which catalog entries are active for your deployment.

### Run locally
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Then hit the API:
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"messages": [{"role": "user", "content": "ping"}]}'
```
Override the default model reported in responses by setting `LLMROUTER_DEFAULT_MODEL`.

### Run with Docker
```bash
docker build -t llmrouter:latest .
docker run -p 8000:8000 -v $(pwd)/config:/app/config llmrouter:latest
```
Mounting the `config/` directory lets the container pick up your provider definitions. A GitHub Action (`.github/workflows/docker.yml`) builds the container and performs a smoke test on every push/PR so you know the image stays healthy. Published images are available on Docker Hub (link coming soon).

## Configuration concepts
- `providers`: List of upstreams, each with credentials, cost metadata, supported models, and optional usage caps.
- `policy`: Strategy that chooses a provider for each request (e.g., weighted cost vs. latency, or priority tiers).
- `breaker`: Thresholds for tripping the circuit (consecutive failures, failure rate over time window).
- `cache`: Backend selection and TTLs; can be disabled.
- `logging/metrics`: Hooks for Prometheus, OpenTelemetry, or custom sinks.
- `LLMROUTER_PROVIDERS`: Optional environment variable (`groq,openrouter,nvidia_nim` by default) that specifies which providers must advertise a model before it is enabled from the published catalog.
- Provider credentials: `GROQ_API_KEY`, `OPENROUTER_API_KEY`, `NVIDIA_NIM_KEY` (and, once mapping is extended, `HUGGINGFACE_API_TOKEN`).

## Maintaining the model catalog
- Regenerate the catalog locally with `python scripts/build_free_model_catalog.py --providers groq,openrouter,nvidia_nim --output generated/model_index.json --pretty`. The script expects the provider API keys in your environment and writes `generated/model_index.json` (ignored by git).
- The GitHub Action `.github/workflows/model-catalog.yml` runs the same script on a schedule and uploads the JSON artifact. Publish the file to a static location (for example GitHub Pages or S3) and point `LLMROUTER_MODEL_INDEX_URL` at it so deployments can fetch the latest mapping.

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
