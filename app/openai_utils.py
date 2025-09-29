import json
from typing import Any, Dict, Optional

from typing import List

from app.schemas import ChatCompletionRequest, ToolCall


def build_openai_request_payload(payload: ChatCompletionRequest, override_model: Optional[str] = None) -> Dict[str, Any]:
    body = payload.dict(exclude_none=True)
    if override_model:
        body["model"] = override_model
    return body


def parse_openai_response_content(data: Dict[str, Any]) -> str:
    return data["choices"][0]["message"].get("content", "")


def parse_openai_tool_calls(data: Dict[str, Any]) -> List[ToolCall]:
    raw_calls = data["choices"][0]["message"].get("tool_calls") or []
    tool_calls: List[ToolCall] = []
    for call in raw_calls:
        try:
            tool_calls.append(ToolCall.parse_obj(call))
        except Exception:
            continue
    return tool_calls
