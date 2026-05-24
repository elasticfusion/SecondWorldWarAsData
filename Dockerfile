# Pin digest — update periodically with: docker pull python:3.12-slim && docker inspect python:3.12-slim --format='{{index .RepoDigests 0}}'
FROM python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203 AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t /deps

FROM python:3.12-slim@sha256:090ba77e2958f6af52a5341f788b50b032dd4ca28377d2893dcf1ecbdfdfe203

WORKDIR /app

# Create non-root user
RUN useradd -r -s /bin/false -d /app pipeline && \
    mkdir -p /tmp/pipeline && chown pipeline:pipeline /tmp/pipeline

# Copy dependencies
COPY --from=builder /deps /usr/local/lib/python3.12/site-packages/

# Copy application code (config.yaml excluded — patched at runtime by entrypoint)
COPY src/ src/
COPY scripts/ scripts/
COPY prompts/ prompts/
COPY config.yaml.example config.yaml
COPY ecs_entrypoint.py .
COPY phase1_parse.py phase2_extract.py phase2_retry.py \
     phase3_enrich_data.py phase3_retry.py \
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
