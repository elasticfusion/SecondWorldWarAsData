FROM python:3.12-slim AS builder

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -t /deps

FROM python:3.12-slim

WORKDIR /app

# Copy application code
COPY --from=builder /deps /usr/local/lib/python3.12/site-packages/

# Copy application code
COPY src/ src/
COPY scripts/ scripts/
COPY prompts/ prompts/
COPY config.yaml .
COPY ecs_entrypoint.py .
COPY phase1_parse.py phase2_extract.py phase2_retry.py \
     phase3_enrich_data.py phase3_retry.py \
     import_to_dynamodb.py ./

ENV PYTHONUNBUFFERED=1

# Entrypoint wraps S3 sync + phase script
ENTRYPOINT ["python3", "ecs_entrypoint.py"]
