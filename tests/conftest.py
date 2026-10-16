"""Shared pytest fixtures.

Offline tests use the TF-IDF embedder (no model download) and a FakeLLM that
records the prompt it receives. FakeLLM is ONLY used to test pipeline wiring and
guardrails; real answer quality is measured by scripts/run_eval.py against a real
LLM provider.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Offline-safe defaults; a real key in the environment enables the @pytest.mark.llm tests.
os.environ.setdefault("EMBEDDING_BACKEND", "tfidf")
os.environ.setdefault("KB_INCLUDE_DRAFTS", "false")

from fastapi.testclient import TestClient  # noqa: E402

from app.llm import LLMResult  # noqa: E402
from app.main import app  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: requires a configured LLM provider API key")


@pytest.fixture(scope="session", autouse=True)
def _isolated_index(tmp_path_factory):
    os.environ["INDEX_DIR"] = str(tmp_path_factory.mktemp("index"))
    os.environ["BOOKINGS_DB"] = str(tmp_path_factory.mktemp("db") / "bookings.db")
    os.environ["ADMIN_TOKEN"] = "staff-test-token"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


class FakeLLM:
    """Stand-in for LLMClient: returns a scripted reply and records the request."""

    available = True
    provider = "fake"
    model = "fake-model"

    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[dict] = []

    def generate(self, system, history, user_text, image=None, media_type=None):
        self.calls.append(
            {"system": system, "history": history, "user_text": user_text, "image": image, "media_type": media_type}
        )
        return LLMResult(text=self.reply, provider="fake", model="fake-model")


@pytest.fixture()
def fake_llm(client):
    """Install a FakeLLM; call fake_llm('reply text') to set the reply."""
    state = client.app.state.petcare
    original = state.llm

    def install(reply: str) -> FakeLLM:
        fake = FakeLLM(reply)
        state.llm = fake
        return fake

    yield install
    state.llm = original


def llm_configured() -> bool:
    provider = os.getenv("LLM_PROVIDER", "anthropic").lower()
    key = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "openrouter": "OPENROUTER_API_KEY"}.get(provider)
    return bool(key and os.getenv(key))
