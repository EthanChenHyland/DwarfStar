"""Streaming chat-completions client for tests: timing, token counts and the raw answer."""
from __future__ import annotations

import http.client
import json
import time


def chat(port: int, body: dict, timeout: float = 3600) -> dict:
    body = dict(body)
    body["stream"] = True
    body.setdefault("stream_options", {"include_usage": True})
    data = json.dumps(body).encode()
    started = time.time()
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    connection.request("POST", "/v1/chat/completions", data, {"Content-Type": "application/json"})
    response = connection.getresponse()
    content, reasoning, errors, tool_names = [], [], [], []
    finish = usage = first = first_content = None
    buffer = b""
    while True:
        chunk = response.read1(65536)
        if not chunk:
            break
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            text = line.decode(errors="replace")
            if not text.startswith("data:"):
                continue
            payload = text[5:].strip()
            if payload == "[DONE]":
                continue
            try:
                event = json.loads(payload)
            except ValueError:
                errors.append(payload[:200])
                continue
            if "error" in event:
                errors.append(event["error"])
            if event.get("usage"):
                usage = event["usage"]
            for choice in event.get("choices", []):
                delta = choice.get("delta") or {}
                if delta.get("reasoning_content"):
                    reasoning.append(delta["reasoning_content"])
                    first = first or time.time()
                if delta.get("content"):
                    content.append(delta["content"])
                    first = first or time.time()
                    first_content = first_content or time.time()
                for call in delta.get("tool_calls") or []:
                    name = (call.get("function") or {}).get("name")
                    if name:
                        tool_names.append(name)
                        first = first or time.time()
                if choice.get("finish_reason"):
                    finish = choice["finish_reason"]
    ended = time.time()
    if response.status != 200:
        errors.append({"status": response.status})
    completion = (usage or {}).get("completion_tokens", 0)
    prompt = (usage or {}).get("prompt_tokens", 0)
    cached = ((usage or {}).get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    return {
        "http_status": response.status,
        "content": "".join(content),
        "reasoning": "".join(reasoning),
        "reasoning_chars": len("".join(reasoning)),
        "reasoning_head": "".join(reasoning)[:300],
        "tool_calls": tool_names,
        "reasoning_tail": "".join(reasoning)[-300:],
        "finish_reason": finish,
        "usage": usage,
        "errors": errors,
        "ttft_s": first - started if first else None,
        "total_s": ended - started,
        "decode_tok_s": (completion - 1) / (ended - first) if first and completion > 1 and ended > first else None,
        "prefill_tok_s": (prompt - cached) / (first - started) if first and prompt else None,
    }
