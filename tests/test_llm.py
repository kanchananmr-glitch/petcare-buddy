"""LLM client tests (offline): provider selection and the exact request sent to OpenRouter.

The HTTP layer is replaced with httpx.MockTransport, so no network call is made and
no real key is needed. Real answers are produced by scripts/run_eval.py.
"""
from __future__ import annotations

import dataclasses
import json

import httpx
import openai
import pytest

from app.config import get_settings
from app.llm import LLMClient, LLMError, LLMUnavailable


def settings_for(provider: str, **overrides):
    base = dataclasses.replace(
        get_settings(),
        llm_provider=provider,
        anthropic_api_key=None,
        openai_api_key=None,
        openrouter_api_key=None,
    )
    return dataclasses.replace(base, **overrides)


def test_unconfigured_provider_raises_unavailable():
    client = LLMClient(settings_for("openrouter"))
    assert not client.available
    with pytest.raises(LLMUnavailable):
        client.generate("sys", [], "hi")


def make_openrouter_client(handler):
    s = settings_for("openrouter", openrouter_api_key="test-key-not-real", openrouter_model="vendor/model-x")
    client = LLMClient(s)
    assert client.available and client.model == "vendor/model-x"
    # keep the real constructor's settings, swap only the transport
    client._client = openai.OpenAI(
        api_key="test-key-not-real",
        base_url=s.openrouter_base_url,
        default_headers={"HTTP-Referer": "http://localhost:8000", "X-Title": "PetCare Buddy"},
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    return client


def test_openrouter_request_shape_text_and_image():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["title"] = request.headers.get("x-title")
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "gen-1", "object": "chat.completion", "created": 0, "model": "vendor/model-x",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": "สวัสดีค่ะ\nSOURCES: none"}}],
            },
        )

    client = make_openrouter_client(handler)
    result = client.generate("system rules", [{"role": "user", "content": "ก่อนหน้า"}, {"role": "assistant", "content": "ค่ะ"}],
                             "คำถาม", image=b"\x89PNG fake", media_type="image/png")

    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["auth"] == "Bearer test-key-not-real"
    assert seen["title"] == "PetCare Buddy"
    body = seen["body"]
    assert body["model"] == "vendor/model-x"
    assert body["temperature"] == 0 and "max_tokens" in body
    assert body["messages"][0] == {"role": "system", "content": "system rules"}
    last = body["messages"][-1]["content"]
    assert last[0] == {"type": "text", "text": "คำถาม"}
    assert last[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert result.provider == "openrouter" and result.text.startswith("สวัสดีค่ะ")


def test_openrouter_error_becomes_llm_error():
    client = make_openrouter_client(lambda r: httpx.Response(401, json={"error": {"message": "No auth", "code": 401}}))
    with pytest.raises(LLMError):
        client.generate("sys", [], "hi")
