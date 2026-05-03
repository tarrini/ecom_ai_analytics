FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Runtime tools needed by kaggle unzip flow and TLS certs
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    unzip \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Default command for Cloud Run Job
CMD ["python", "pipeline/run_full_pipeline.py"]
