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
# optional: install semantic cache extras (pulls in PyTorch)
pip install -r requirements-semantic.txt

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
- Looking to slim the Docker image? Today we rely on `sentence-transformers`, which pulls in PyTorch. We may switch to an ONNXRuntime-backed embedding loader in the future; if you explore that path, keep the MiniLM model and tokenizer files together and use `onnxruntime` for inference.

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

Published images:
- `schedion/llmrouter:slim` – no semantic cache dependencies, smallest footprint (multi-arch: `linux/amd64`, `linux/arm64`, `linux/arm/v7`).
- `schedion/llmrouter:latest` – includes semantic cache extras (published for `linux/amd64` only because PyTorch wheels aren’t available for our other targets).

## Publishing to Docker Hub
- GitHub repository: https://github.com/schedion/llmrouter
- Docker Hub image: https://hub.docker.com/r/schedion/llmrouter

## Development
```bash
python -m compileall app scripts  # quick syntax check
pytest                             # when tests are added
```

## License
[The Unlicense](https://unlicense.org/)
