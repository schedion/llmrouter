"""FastAPI entrypoint exposing an OpenAI-compatible chat completion API."""

from __future__ import annotations

import logging
import os
import time

from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, HTTPException, Request

from app.config import load_router_config
from app.router import NoAvailableProviderError, Router
from app.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    Choice,
    ChoiceMessage,
    ToolCall,
    Usage,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="llmrouter", version="0.1.0")


def _build_response(
    provider_name: str,
    payload: ChatCompletionRequest,
    content: str,
    tool_calls: Optional[List[ToolCall]],
) -> ChatCompletionResponse:
    completion_tokens = max(1, len(content.split()))
    usage = Usage(
        prompt_tokens=len(payload.messages),
        completion_tokens=completion_tokens,
        total_tokens=len(payload.messages) + completion_tokens,
    )
    return ChatCompletionResponse(
        id=f"chatcmpl-{provider_name}-{int(time.time() * 1000)}",
        model=payload.model,
        choices=[
            Choice(
                index=0,
                message=ChoiceMessage(role="assistant", content=content, tool_calls=tool_calls or None),
                finish_reason="stop",
            )
        ],
        usage=usage,
    )


def _config_path() -> str:
    return os.environ.get("LLMROUTER_CONFIG", "config/providers.yaml")


@app.on_event("startup")
async def startup() -> None:
    config = load_router_config(_config_path())
    app.state.router = Router(config)
    logger.info("Router initialized with %d providers", app.state.router.provider_count)


async def _get_router(request: Request) -> Router:
    router: Router | None = getattr(request.app.state, "router", None)
    if router is None:
        config = load_router_config(_config_path())
        router = Router(config)
        request.app.state.router = router
    return router


@app.get("/")
async def root() -> dict[str, str]:
    """Basic health check endpoint."""
    return {"status": "ok", "message": "llmrouter is vibe coded but alive"}


@app.get("/v1/models")
async def list_models(router: Router = Depends(_get_router)) -> Dict[str, Any]:
    """Return the catalog of models available across configured providers."""

    data = router.catalog()
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions", response_model=ChatCompletionResponse)
async def create_chat_completion(
    payload: ChatCompletionRequest,
    request: Request,
    router: Router = Depends(_get_router),
) -> ChatCompletionResponse:
    """Route chat completion requests through the configured providers."""

    user_messages = [m for m in payload.messages if m.role == "user"]
    if not user_messages:
        raise HTTPException(status_code=400, detail="At least one user message is required")

    semantic_threshold_header = request.headers.get("X-LLMRouter-Semantic-Threshold")
    semantic_threshold = None
    if semantic_threshold_header:
        try:
            semantic_threshold = float(semantic_threshold_header)
        except ValueError:
            semantic_threshold = None

    semantic_toggle_header = request.headers.get("X-LLMRouter-Semantic-Cache")
    semantic_enabled = None
    if semantic_toggle_header:
        value = semantic_toggle_header.strip().lower()
        if value in {"false", "off", "0"}:
            semantic_enabled = False
        elif value in {"true", "on", "1"}:
            semantic_enabled = True

    try:
        provider_result = await router.chat_completion(
            payload,
            semantic_threshold=semantic_threshold,
            semantic_enabled=semantic_enabled,
        )
    except NoAvailableProviderError as exc:
        logger.error("Routing failed: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return _build_response(
        provider_result.provider_name,
        payload,
        provider_result.content,
        provider_result.tool_calls,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8000")),
        reload=True,
    )
