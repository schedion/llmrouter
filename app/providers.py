"""Provider abstractions for llmrouter."""

from __future__ import annotations

import asyncio
import os
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass
from time import perf_counter

import httpx

from app.config import ProviderConfig
from app.metrics import record_provider_error, record_provider_http
from app.schemas import ChatCompletionRequest
from app.openai_utils import build_openai_request_payload, parse_openai_response_content, parse_openai_tool_calls

DEFAULT_TIMEOUT = httpx.Timeout(30.0)


class ProviderError(Exception):
    """Base class for provider errors."""


def _last_user_message(payload: ChatCompletionRequest) -> str:
    for message in reversed(payload.messages):
        if message.role == "user":
            return message.content
    raise ProviderError("No user message found in chat payload")


def _simulate_failure(config: ProviderConfig) -> None:
    if config.mock_failure_rate > 0 and random.random() < config.mock_failure_rate:
        raise ProviderError(f"Provider {config.name} simulated failure")


class Provider(ABC):
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self._lock = asyncio.Lock()

    @abstractmethod
    async def complete(self, payload: ChatCompletionRequest) -> str:
        """Return assistant text for a chat completion."""

    def _require_api_key(self) -> str:
        if not self.config.api_key_env:
            record_provider_error(self.config.type, "missing_api_key_env")
            raise ProviderError(f"Provider {self.config.name} requires 'api_key_env' in config")
        api_key = os.environ.get(self.config.api_key_env)
        if not api_key:
            record_provider_error(self.config.type, "missing_api_key")
            raise ProviderError(
                f"Environment variable '{self.config.api_key_env}' is not set for provider {self.config.name}"
            )
        return api_key


class EchoProvider(Provider):
    async def complete(self, payload: ChatCompletionRequest) -> str:
        async with self._lock:
            _simulate_failure(self.config)
            message = _last_user_message(payload)
            return f"[{self.config.name}] {message}"


class HuggingFaceProvider(Provider):
    async def complete(self, payload: ChatCompletionRequest) -> str:
        async with self._lock:
            _simulate_failure(self.config)

            if not self.config.model:
                record_provider_error(self.config.type, "missing_model")
                raise ProviderError(f"Provider {self.config.name} requires a 'model' value")

            api_key = self._require_api_key()
            url = self.config.base_url or f"https://api-inference.huggingface.co/models/{self.config.model}"

            headers = {"Authorization": f"Bearer {api_key}", **self.config.extra_headers}
            payload_body = {
                "inputs": _last_user_message(payload),
                "parameters": {"return_full_text": False},
            }

            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                start = perf_counter()
                try:
                    response = await client.post(url, headers=headers, json=payload_body)
                except httpx.HTTPError as exc:
                    record_provider_error(self.config.type, exc.__class__.__name__)
                    raise
                duration = perf_counter() - start

            record_provider_http(self.config.type, response.status_code, duration)

            if response.status_code >= 400:
                raise ProviderError(
                    f"Hugging Face provider {self.config.name} returned status {response.status_code}: {response.text}"
                )

            data = response.json()
            generated_text = None

            if isinstance(data, list) and data:
                generated_text = data[0].get("generated_text")
            elif isinstance(data, dict):
                generated_text = data.get("generated_text")

            if not generated_text:
                record_provider_error(self.config.type, "empty_payload")
                raise ProviderError(
                    f"Hugging Face provider {self.config.name} returned no generated_text payload: {data}"
                )

            return generated_text.strip()


class GroqProvider(Provider):
    DEFAULT_BASE_URL = "https://api.groq.com/openai/v1/chat/completions"

    async def complete(self, payload: ChatCompletionRequest) -> str:
        async with self._lock:
            _simulate_failure(self.config)

            api_key = self._require_api_key()
            url = self.config.base_url or self.DEFAULT_BASE_URL

            request_body = build_openai_request_payload(payload, override_model=self.config.model)

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **self.config.extra_headers,
            }

            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                start = perf_counter()
                try:
                    response = await client.post(url, headers=headers, json=request_body)
                except httpx.HTTPError as exc:
                    record_provider_error(self.config.type, exc.__class__.__name__)
                    raise
                duration = perf_counter() - start

            record_provider_http(self.config.type, response.status_code, duration)

            if response.status_code >= 400:
                raise ProviderError(
                    f"Groq provider {self.config.name} returned status {response.status_code}: {response.text}"
                )

            data = response.json()
            tool_calls = parse_openai_tool_calls(data)
            payload.tool_calls = tool_calls  # store on payload for response usage
            try:
                content = parse_openai_response_content(data)
            except Exception as exc:  # pragma: no cover - defensive catch
                raise ProviderError(
                    f"Groq provider {self.config.name} produced unexpected response: {data}"
                ) from exc

            return content.strip()


class OpenRouterProvider(Provider):
    DEFAULT_BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    async def complete(self, payload: ChatCompletionRequest) -> str:
        async with self._lock:
            _simulate_failure(self.config)

            api_key = self._require_api_key()
            url = self.config.base_url or self.DEFAULT_BASE_URL

            request_body = build_openai_request_payload(payload, override_model=self.config.model)

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **self.config.extra_headers,
            }

            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                start = perf_counter()
                try:
                    response = await client.post(url, headers=headers, json=request_body)
                except httpx.HTTPError as exc:
                    record_provider_error(self.config.type, exc.__class__.__name__)
                    raise
                duration = perf_counter() - start

            record_provider_http(self.config.type, response.status_code, duration)

            if response.status_code >= 400:
                raise ProviderError(
                    f"OpenRouter provider {self.config.name} returned status {response.status_code}: {response.text}"
                )

            data = response.json()
            payload.tool_calls = parse_openai_tool_calls(data)
            try:
                content = parse_openai_response_content(data)
            except Exception as exc:  # pragma: no cover - defensive catch
                raise ProviderError(
                    f"OpenRouter provider {self.config.name} produced unexpected response: {data}"
                ) from exc

            return content.strip()


class NvidiaNimProvider(Provider):
    DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

    async def complete(self, payload: ChatCompletionRequest) -> str:
        async with self._lock:
            _simulate_failure(self.config)

            api_key = self._require_api_key()
            url = self.config.base_url or self.DEFAULT_BASE_URL

            request_body = build_openai_request_payload(payload, override_model=self.config.model)

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                **self.config.extra_headers,
            }

            async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT) as client:
                start = perf_counter()
                try:
                    response = await client.post(url, headers=headers, json=request_body)
                except httpx.HTTPError as exc:
                    record_provider_error(self.config.type, exc.__class__.__name__)
                    raise
                duration = perf_counter() - start

            record_provider_http(self.config.type, response.status_code, duration)

            if response.status_code >= 400:
                raise ProviderError(
                    f"NVIDIA NIM provider {self.config.name} returned status {response.status_code}: {response.text}"
                )

            data = response.json()
            payload.tool_calls = parse_openai_tool_calls(data)
            try:
                content = parse_openai_response_content(data)
            except Exception as exc:  # pragma: no cover - defensive catch
                raise ProviderError(
                    f"NVIDIA NIM provider {self.config.name} produced unexpected response: {data}"
                ) from exc

            return content.strip()


@dataclass
class ProviderFactory:
    """Factory to instantiate provider implementations."""

    @staticmethod
    def create(config: ProviderConfig) -> Provider:
        if config.type == "echo":
            return EchoProvider(config)
        if config.type == "huggingface":
            return HuggingFaceProvider(config)
        if config.type == "groq":
            return GroqProvider(config)
        if config.type == "openrouter":
            return OpenRouterProvider(config)
        if config.type == "nvidia_nim":
            return NvidiaNimProvider(config)
        raise ValueError(f"Unsupported provider type: {config.type}")
