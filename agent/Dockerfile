FROM python:3.12-slim

# Voice agents are latency-sensitive; unbuffered output keeps logs in real time.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DECK_DIR=/app/deck \
    AUDIT_DIR=/app/data/audit \
    LATENCY_DIR=/app/data/latency

WORKDIR /app

COPY agent/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY agent/src ./src
COPY deck ./deck
COPY evals/fixtures/lexicon.txt ./evals/fixtures/lexicon.txt

RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /app/data/audit /app/data/latency \
    && chown -R agent:agent /app
USER agent

# `start` runs the worker in production mode; `dev` enables hot reload.
CMD ["python", "src/agent.py", "start"]
