"""Pydantic models for OpenAI-compatible chat completions."""

from __future__ import annotations

import os
import time
from typing import List, Optional

from pydantic import BaseModel, Field, validator


def _default_model() -> str:
    return os.environ.get("LLMROUTER_DEFAULT_MODEL", "gpt-3.5-turbo")


class Message(BaseModel):
    role: str
    content: str

    @validator("role")
    def validate_role(cls, value: str) -> str:  # noqa: N805 (pydantic validator signature)
        allowed_roles = {"system", "user", "assistant", "tool"}
        if value not in allowed_roles:
            raise ValueError(f"Unsupported role: {value}")
        return value


class ChatCompletionRequest(BaseModel):
    model: str = Field(default_factory=_default_model)
    messages: List[Message]
    temperature: Optional[float] = 1.0
    max_tokens: Optional[int] = None

    @validator("messages")
    def validate_messages(cls, value: List[Message]) -> List[Message]:  # noqa: N805
        if not value:
            raise ValueError("messages must contain at least one message")
        return value


class ChoiceMessage(BaseModel):
    role: str
    content: str


class Choice(BaseModel):
    index: int
    message: ChoiceMessage
    finish_reason: str = "stop"


class Usage(BaseModel):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int = Field(default_factory=lambda: int(time.time()))
    model: str
    choices: List[Choice]
    usage: Usage
