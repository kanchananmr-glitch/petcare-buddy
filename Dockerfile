# PetCare Buddy - container image for the cloud deployment (course week 10: FastAPI + Docker + Render).
#   docker build -t petcare-buddy .
#   docker run -p 8000:8000 --env-file .env -e EMBEDDING_BACKEND=tfidf petcare-buddy
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Only what the server needs at runtime (no tests, docs, .env or local databases - see .dockerignore).
COPY app ./app
COPY data ./data

# Run as a normal user; data/ holds the vector index and the bookings database.
RUN useradd --create-home --uid 10001 appuser && mkdir -p /app/data/index && chown -R appuser /app/data
USER appuser

# Safe defaults; secrets (OPENROUTER_API_KEY, ADMIN_TOKEN) come from the platform's environment variables.
ENV LLM_PROVIDER=openrouter \
    EMBEDDING_BACKEND=tfidf \
    STORE_TIMEZONE=Asia/Bangkok

EXPOSE 8000
# Render sets $PORT; locally it falls back to 8000.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
