"""LLM provider abstraction (Anthropic Claude or OpenAI), text + vision."""
from __future__ import annotations

import base64
from dataclasses import dataclass

from app.config import Settings


class LLMUnavailable(RuntimeError):
    """No provider configured (missing API key or LLM_PROVIDER=none)."""


class LLMError(RuntimeError):
    """The provider returned an error or timed out."""


@dataclass
class LLMResult:
    text: str
    provider: str
    model: str


class LLMClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.provider = settings.llm_provider
        self._client = None
        if not settings.llm_configured:
            return
        if self.provider == "anthropic":
            import anthropic

            self._client = anthropic.Anthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_s)
        elif self.provider == "openai":
            import openai

            self._client = openai.OpenAI(api_key=settings.openai_api_key, timeout=settings.llm_timeout_s)
        elif self.provider == "openrouter":
            import openai

            # OpenRouter exposes an OpenAI-compatible Chat Completions API (text + image inputs).
            self._client = openai.OpenAI(
                api_key=settings.openrouter_api_key,
                base_url=settings.openrouter_base_url,
                timeout=settings.llm_timeout_s,
                default_headers={"HTTP-Referer": "http://localhost:8000", "X-Title": "PetCare Buddy"},
            )

    @property
    def available(self) -> bool:
        return self._client is not None

    @property
    def model(self) -> str | None:
        return self.settings.llm_model

    def generate(
        self,
        system: str,
        history: list[dict],
        user_text: str,
        image: bytes | None = None,
        media_type: str | None = None,
    ) -> LLMResult:
        if not self.available:
            raise LLMUnavailable(
                f"LLM provider '{self.provider}' is not configured (set the API key in .env)"
            )
        try:
            if self.provider == "anthropic":
                return self._anthropic(system, history, user_text, image, media_type)
            return self._openai(system, history, user_text, image, media_type)
        except LLMUnavailable:
            raise
        except Exception as exc:  # network errors, auth errors, rate limits, timeouts
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc

    # -------------------------------------------------------------- providers
    def _anthropic(self, system, history, user_text, image, media_type) -> LLMResult:
        content: list[dict] = []
        if image is not None:
            content.append(
                {
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": base64.b64encode(image).decode()},
                }
            )
        content.append({"type": "text", "text": user_text})
        messages = [{"role": h["role"], "content": h["content"]} for h in history]
        messages.append({"role": "user", "content": content})
        resp = self._client.messages.create(
            model=self.settings.anthropic_model,
            max_tokens=self.settings.llm_max_tokens,
            temperature=0,
            system=system,
            messages=messages,
        )
        text = "".join(block.text for block in resp.content if getattr(block, "type", "") == "text")
        return LLMResult(text=text, provider="anthropic", model=self.settings.anthropic_model)

    def _openai(self, system, history, user_text, image, media_type) -> LLMResult:
        is_openrouter = self.provider == "openrouter"
        content: list[dict] = [{"type": "text", "text": user_text}]
        if image is not None:
            data_uri = f"data:{media_type};base64,{base64.b64encode(image).decode()}"
            content.append({"type": "image_url", "image_url": {"url": data_uri}})
        messages = [{"role": "system", "content": system}]
        messages += [{"role": h["role"], "content": h["content"]} for h in history]
        messages.append({"role": "user", "content": content})
        model = self.settings.openrouter_model if is_openrouter else self.settings.openai_model
        token_arg = {"max_tokens" if is_openrouter else "max_completion_tokens": self.settings.llm_max_tokens}
        resp = self._client.chat.completions.create(model=model, messages=messages, temperature=0, **token_arg)
        if not resp.choices:  # OpenRouter reports some upstream failures as an empty choice list
            raise LLMError(f"empty response from {self.provider}: {getattr(resp, 'error', '')}")
        return LLMResult(text=resp.choices[0].message.content or "", provider=self.provider, model=model)
