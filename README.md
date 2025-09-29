# llmrouter

llmrouter is a programmable OpenAI-compatible router for Large Language Models. It lets you combine multiple providers, fall back gracefully, and cache responses without changing upstream client code.

## Supported Providers (out of the box)
- [Groq](https://groq.com)
- [OpenRouter](https://openrouter.ai)
- [NVIDIA NIM](https://www.nvidia.com/en-us/ai-platform/)
- [Hugging Face Inference API](https://huggingface.co/inference-api)

## Quick Start
```bash
# install dependencies
pip install -r requirements.txt

# export provider credentials
export PROVIDER_KEY_GROQ=...
export PROVIDER_KEY_OPENROUTER=...
export PROVIDER_KEY_NVIDIA_NIM=...
export PROVIDER_KEY_HUGGINGFACE=...

# point at the published catalog (or your local one)
export LLMROUTER_MODEL_INDEX_URL="https://raw.githubusercontent.com/schedion/llmrouter/refs/heads/main/generated/model_index.json"

uvicorn app.main:app --reload
```

Query the API:
```bash
# list available canonical models
curl http://localhost:8000/v1/models

# run a chat completion
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
        "model": "gpt-oss-20b",
        "messages": [
          {"role": "system", "content": "You are a concise assistant."},
          {"role": "user", "content": "hello"}
        ]
      }'
```

Semantic caching controls:
- enable/disable globally with `LLMROUTER_SEMANTIC_CACHE_ENABLED` (`false` by default)
- override per request using headers:
  - `X-LLMRouter-Semantic-Cache: on|off`
  - `X-LLMRouter-Semantic-Threshold: 0.9`

Exact-match caching uses `LLMROUTER_CACHE_TTL` (seconds, `0` disables).

## Configuration
- Canonical model mappings live in `config/model_catalog_seed.yaml`.
- Regenerate the catalog (`generated/model_index.json`) with:
  ```bash
  ./scripts/build_free_model_catalog.py \
    --providers groq,openrouter,nvidia_nim,huggingface \
    --output generated/model_index.json --pretty
  ```
- Skip providers you don’t use by editing the seed or passing a smaller `--providers` list.
- Provider credentials expected by default: `PROVIDER_KEY_GROQ`, `PROVIDER_KEY_OPENROUTER`, `PROVIDER_KEY_NVIDIA_NIM`, `PROVIDER_KEY_HUGGINGFACE`.

## Docker
A sample Compose file (`docker-compose.sample.yml`) mounts named volumes for config, generated data, and the Hugging Face cache. Update the image name and credentials, then run:
```bash
docker compose -f docker-compose.sample.yml up -d
```

## Publishing to Docker Hub
- GitHub repository: https://github.com/schedion/llmrouter
- Docker Hub image: https://hub.docker.com/r/schedion/llmrouter

To let CI push the README to Docker Hub, add these repository secrets/variables:
- `DOCKERHUB_USERNAME` (secret) – Docker Hub username with write access.
- `DOCKERHUB_TOKEN` (secret) – Docker Hub access token (write scope).
- `DOCKERHUB_NAMESPACE` (repository variable) – e.g. `schedion`.

The workflow reuses this README via `peter-evans/dockerhub-description@v4` when changes land on `main`.

## Development
```bash
python -m compileall app scripts  # quick syntax check
pytest                             # when tests are added
```

## License
[The Unlicense](https://unlicense.org/)
