"""In-memory semantic cache using sentence-transformers."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import numpy as np
from sentence_transformers import SentenceTransformer

from app.schemas import ChatCompletionRequest, ToolCall


class SemanticCache:
    def __init__(
        self,
        model_name: str = "sentence-transformers/all-MiniLM-L6-v2",
        max_entries: int = 512,
    ) -> None:
        self.model_name = model_name
        self.max_entries = max_entries
        self._model: Optional[SentenceTransformer] = None
        self._entries: List[Dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def _ensure_model(self) -> SentenceTransformer:
        if self._model is None:
            self._model = await asyncio.to_thread(SentenceTransformer, self.model_name)
        return self._model

    async def _embed(self, text: str) -> np.ndarray:
        model = await self._ensure_model()
        vector = await asyncio.to_thread(model.encode, text, normalize_embeddings=True)
        return np.asarray(vector, dtype=np.float32)

    @staticmethod
    def _serialize_payload(payload: ChatCompletionRequest) -> str:
        message_repr = [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in payload.messages
        ]
        tools_repr = [tool.dict() for tool in payload.tools] if payload.tools else []
        base = {
            "model": payload.model,
            "messages": message_repr,
            "tools": tools_repr,
            "tool_choice": payload.tool_choice,
        }
        return json.dumps(base, sort_keys=True, ensure_ascii=False)

    async def get(self, payload: ChatCompletionRequest, threshold: float) -> Optional[Dict[str, Any]]:
        if threshold <= 0:
            return None
        text = self._serialize_payload(payload)
        query_embedding = await self._embed(text)

        best_entry: Optional[Dict[str, Any]] = None
        best_similarity = threshold

        async with self._lock:
            for entry in self._entries:
                similarity = float(np.dot(query_embedding, entry["embedding"]))
                if similarity >= best_similarity:
                    best_similarity = similarity
                    best_entry = entry

        if best_entry is None:
            return None

        return {
            "provider_name": best_entry["provider_name"],
            "content": best_entry["content"],
            "tool_calls": best_entry["tool_calls"],
        }

    async def add(self, payload: ChatCompletionRequest, result: Dict[str, Any]) -> None:
        text = self._serialize_payload(payload)
        embedding = await self._embed(text)

        entry = {
            "embedding": embedding,
            "provider_name": result["provider_name"],
            "content": result["content"],
            "tool_calls": result.get("tool_calls"),
        }

        async with self._lock:
            self._entries.append(entry)
            if len(self._entries) > self.max_entries:
                self._entries.pop(0)

    @staticmethod
    def serialize_tool_calls(tool_calls: Optional[List[ToolCall]]) -> Optional[List[Dict[str, Any]]]:
        if not tool_calls:
            return None
        return [tc.dict() for tc in tool_calls]

    @staticmethod
    def deserialize_tool_calls(tool_calls: Optional[List[Dict[str, Any]]]) -> Optional[List[ToolCall]]:
        if not tool_calls:
            return None
        return [ToolCall.parse_obj(tc) for tc in tool_calls]
