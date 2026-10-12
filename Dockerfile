FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    PORT=8080

WORKDIR /srv

# Install dependencies first so code changes don't invalidate this layer.
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py README.md \
    && pip install . \
    && rm -rf app build *.egg-info README.md

COPY README.md ./
COPY app ./app
RUN pip install --no-deps . \
    && useradd --system --uid 1000 --home-dir /srv roborock \
    && mkdir -p /data && chown roborock /data

USER roborock
VOLUME ["/data"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8080\")}/api/health', timeout=4)"

CMD ["python", "-m", "app.main"]
