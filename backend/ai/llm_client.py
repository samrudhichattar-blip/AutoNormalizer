"""
Provider-agnostic LLM access. The API key is read from the SERVER environment only (never sent to the browser).

  export ANTHROPIC_API_KEY=...        (required for real AI)
  export LLM_MODEL=claude-sonnet-5-5  (optional, change if your account uses another model)

With no key set, OfflineClient is used: every AI feature then falls back to deterministic templates/heuristics and says so.
"""
import json
import os
import re

import httpx


class LLMError(Exception):
    pass


class AnthropicClient:
    name = "anthropic"

    def __init__(self, api_key, model=None, transport=None, timeout=60):
        self.model = model or os.getenv("LLM_MODEL", "claude-sonnet-5-5")
        self._key = api_key
        self._http = httpx.Client(timeout=timeout, transport=transport)

    def complete(self, system, user, max_tokens=1500):
        try:
            r = self._http.post("https://api.anthropic.com/v1/messages",
                                headers={"x-api-key": self._key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                                json={"model": self.model, "max_tokens": max_tokens, "system": system,
                                      "messages": [{"role": "user", "content": user}]})
        except httpx.HTTPError as e:
            raise LLMError(f"network error: {e}") from e
        if r.status_code != 200:
            raise LLMError(f"API returned {r.status_code}: {r.text[:200]}")
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


class OfflineClient:
    name = "offline"
    model = None

    def complete(self, system, user, max_tokens=0):
        raise LLMError("no ANTHROPIC_API_KEY configured")


def get_client():
    key = os.getenv("ANTHROPIC_API_KEY")
    return AnthropicClient(key) if key else OfflineClient()


def parse_json(text):
    """LLMs sometimes wrap JSON in prose or code fences. Extract the first JSON object/array."""
    text = re.sub(r"```(?:json)?", "", text)
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts: raise ValueError("no JSON in model output")
    s = min(starts); e = max(text.rfind("}"), text.rfind("]"))
    try:
        return json.loads(text[s:e + 1])
    except json.JSONDecodeError as ex:
        raise ValueError(f"invalid JSON from model: {ex}") from ex
