"""Application settings, read from environment variables (.env supported)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:  # optional: load .env when python-dotenv is installed
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # LLM provider: "anthropic", "openai", "openrouter" or "none"
    llm_provider: str
    anthropic_api_key: str | None
    anthropic_model: str
    openai_api_key: str | None
    openai_model: str
    openrouter_api_key: str | None
    openrouter_model: str
    openrouter_base_url: str
    llm_timeout_s: float
    llm_max_tokens: int

    # RAG
    embedding_backend: str  # "auto", "sentence-transformers" or "tfidf"
    embedding_model: str
    top_k: int
    index_dir: Path
    knowledge_path: Path
    drafts_path: Path
    rules_path: Path
    include_drafts: bool
    cat_pricing_path: Path

    # Booking requests
    bookings_db: Path
    admin_token: str | None
    timezone: str
    slot_capacity: int  # active requests one hourly slot can hold (number of groomers)

    # Input limits
    max_message_chars: int
    max_history_turns: int
    max_image_mb: float

    @property
    def llm_configured(self) -> bool:
        if self.llm_provider == "anthropic":
            return bool(self.anthropic_api_key)
        if self.llm_provider == "openai":
            return bool(self.openai_api_key)
        if self.llm_provider == "openrouter":
            return bool(self.openrouter_api_key)
        return False

    @property
    def llm_model(self) -> str | None:
        return {
            "anthropic": self.anthropic_model,
            "openai": self.openai_model,
            "openrouter": self.openrouter_model,
        }.get(self.llm_provider)


def get_settings() -> Settings:
    return Settings(
        llm_provider=os.getenv("LLM_PROVIDER", "anthropic").strip().lower(),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
        openrouter_api_key=os.getenv("OPENROUTER_API_KEY") or None,
        openrouter_model=os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        openrouter_base_url=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        llm_timeout_s=float(os.getenv("LLM_TIMEOUT_S", "60")),
        llm_max_tokens=int(os.getenv("LLM_MAX_TOKENS", "800")),
        embedding_backend=os.getenv("EMBEDDING_BACKEND", "auto").strip().lower(),
        embedding_model=os.getenv(
            "EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        ),
        top_k=int(os.getenv("RAG_TOP_K", "5")),
        index_dir=Path(os.getenv("INDEX_DIR", str(DATA_DIR / "index"))),
        knowledge_path=Path(os.getenv("KNOWLEDGE_PATH", str(DATA_DIR / "knowledge.json"))),
        drafts_path=Path(os.getenv("DRAFTS_PATH", str(DATA_DIR / "knowledge_draft_additions.json"))),
        rules_path=Path(os.getenv("RULES_PATH", str(DATA_DIR / "chatbot_rules.json"))),
        include_drafts=_bool("KB_INCLUDE_DRAFTS", False),
        cat_pricing_path=Path(os.getenv("CAT_PRICING_PATH", str(DATA_DIR / "cat_pricing.json"))),
        bookings_db=Path(os.getenv("BOOKINGS_DB", str(DATA_DIR / "bookings.db"))),
        admin_token=os.getenv("ADMIN_TOKEN") or None,
        timezone=os.getenv("STORE_TIMEZONE", "Asia/Bangkok"),
        slot_capacity=max(1, int(os.getenv("BOOKING_SLOT_CAPACITY", "1"))),
        max_message_chars=int(os.getenv("MAX_MESSAGE_CHARS", "1000")),
        max_history_turns=int(os.getenv("MAX_HISTORY_TURNS", "8")),
        max_image_mb=float(os.getenv("MAX_IMAGE_MB", "5")),
    )
