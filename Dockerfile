# Pin digest — update periodically with: docker pull python:3.12-slim && docker inspect python:3.12-slim --format='{{index .RepoDigests 0}}'
FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t /deps

FROM python:3.12-slim@sha256:05cda9777409a9c3ffddd94a4c476b79f0769a0b4857f0c7ed9226b6800b0d6f

WORKDIR /app

# Create non-root user. pandoc is required by the Phase-0 convert step
# (src/ingestion/text_converters — epub/docx -> markdown via pandoc subprocess).
RUN apt-get update && apt-get upgrade -y --no-install-recommends && \
    apt-get install -y --no-install-recommends pandoc && \
    rm -rf /var/lib/apt/lists/* && \
    useradd -r -s /bin/false -d /app pipeline && \
    mkdir -p /tmp/pipeline && chown pipeline:pipeline /tmp/pipeline

# Copy dependencies
COPY --from=builder /deps /usr/local/lib/python3.12/site-packages/

# Headless Chromium for Grokipedia JS-SPA rendering (Phase 2 source_section enrichment).
# Playwright (the Python lib) ships via requirements.txt, but the BROWSER BINARY + its OS
# shared libraries are a separate install. Install to a shared, world-readable path so the
# non-root `pipeline` user can use it. Enrichment degrades gracefully to URL-only if this is
# ever missing. --with-deps pulls the required apt libraries (nss, fonts, libx*, etc.).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN python3 -m playwright install --with-deps chromium && \
    chmod -R a+rX /ms-playwright && \
    rm -rf /var/lib/apt/lists/*

# Copy application code (config.yaml excluded — patched at runtime by entrypoint)
COPY src/ src/
COPY scripts/ scripts/
COPY prompts/ prompts/
COPY search_queries/ search_queries/
COPY config.yaml .
COPY ecs_entrypoint.py .
COPY phase0_ingest.py phase0_convert.py \
     phase1_parse.py phase2_extract.py phase2_retry.py \
     phase3_enrich_data.py phase3_retry.py \
     phase3_enqueue_openserp.py openserp_worker.py \
     import_to_dynamodb.py ./

# Set ownership (config.yaml needs to be writable for runtime patching)
RUN chown -R pipeline:pipeline /app

ENV PYTHONUNBUFFERED=1

# Run as non-root
USER pipeline

# Health check for ECS
HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD python3 -c "import sys; sys.exit(0)"

# Entrypoint wraps S3 sync + phase script
ENTRYPOINT ["python3", "ecs_entrypoint.py"]
