# FetchIQ API. Built by compose.yaml at the repository root; no deploy target is implied.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# requirements.txt is the single install source, so the image matches a local venv.
COPY requirements.txt ./
RUN python -m pip install --upgrade pip && python -m pip install -r requirements.txt

COPY pyproject.toml ./
COPY app ./app
COPY scripts ./scripts

RUN useradd --create-home --uid 1000 fetchiq && chown -R fetchiq:fetchiq /app
USER fetchiq

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"

# No --reload: the source is copied in, not mounted.
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
