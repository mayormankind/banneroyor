FROM python:3.12-slim

# FFmpeg is required for media inspection, extraction, normalization, chunking.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY prompts ./prompts

ENV WORKER_ENV=production \
    PYTHONUNBUFFERED=1

# Render Background Worker: no ports, long-running process.
CMD ["python", "-m", "app.main"]
