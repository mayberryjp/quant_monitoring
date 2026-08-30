"""Daily streaming/replay status report.

Pulls the day's replay streaming events (with their session ticker/interval) from
Postgres, asks an OpenAI-compatible Ollama endpoint to summarize the raw results
into human-readable text, and posts that summary to a Discord channel via webhook.

Configuration is read entirely from the environment (see docker-compose.yml):
  DATABASE_URL         Shared Postgres connection string.
  OLLAMA_BASE_URL      Base URL of an OpenAI-compatible Ollama endpoint, e.g.
                       http://ollama:11434/v1
  OLLAMA_MODEL         Model name to use for summarization.
  OLLAMA_API_KEY       Optional bearer token (Ollama typically ignores this).
  DISCORD_WEBHOOK_URL  Discord incoming webhook URL to post the summary to.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

import requests
from sqlalchemy import create_engine, text

DISCORD_MESSAGE_LIMIT = 2000

STREAMING_STATUS_QUERY = """
    SELECT s.ticker,
           s.interval,
           e.session_id,
           e.sequence,
           e.bar_time,
           e.emitted_at,
           e.kafka_partition,
           e.kafka_offset
    FROM replay_events e
    JOIN replay_sessions s ON s.id = e.session_id
    WHERE e.emitted_at >= date_trunc('day', now())
      AND e.emitted_at <  date_trunc('day', now()) + interval '1 day'
    ORDER BY s.ticker, s.interval, e.emitted_at, e.sequence
"""


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"required environment variable {name} is not set")
    return value


def fetch_streaming_status(database_url: str) -> Any:
    """Best-effort fetch of today's replay streaming events."""
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            try:
                rows = conn.execute(text(STREAMING_STATUS_QUERY)).mappings().all()
                return [dict(row) for row in rows]
            except Exception as exc:  # noqa: BLE001
                return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        engine.dispose()


def summarize_with_ollama(base_url: str, model: str, api_key: str | None, raw_data: Any) -> str:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an operations assistant summarizing the day's replay "
                    "streaming activity for a quant trading platform. Given raw JSON "
                    "records of replay_events joined to replay_sessions (ticker, "
                    "interval, session_id, sequence, bar_time, emitted_at, "
                    "kafka_partition, kafka_offset) emitted today, write a summary of at "
                    "most one or two sentences that notes how many tickers were emitted, "
                    "whether they are for the most recent date, and whether all of the "
                    "ticker replays have reached 100% of progress. Use plain text "
                    "suitable for a Discord message. Keep summary to one or two sentences. Do not provide any additional analysis or information beyond the requested summary."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(raw_data, indent=2, default=str),
            },
        ],
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    response = requests.post(
        f"{base_url.rstrip('/')}/chat/completions",
        json=payload,
        headers=headers,
        timeout=120,
    )
    response.raise_for_status()
    body = response.json()
    return body["choices"][0]["message"]["content"].strip()


def post_to_discord(webhook_url: str, message: str) -> None:
    """Post `message` to Discord, splitting into multiple messages if needed
    since Discord rejects content over 2000 characters."""
    chunks = [
        message[i : i + DISCORD_MESSAGE_LIMIT]
        for i in range(0, len(message), DISCORD_MESSAGE_LIMIT)
    ] or [""]
    for chunk in chunks:
        response = requests.post(webhook_url, json={"content": chunk}, timeout=30)
        response.raise_for_status()


def main() -> int:
    database_url = _require_env("DATABASE_URL")
    ollama_base_url = _require_env("OLLAMA_BASE_URL")
    ollama_model = _require_env("OLLAMA_MODEL")
    ollama_api_key = os.environ.get("OLLAMA_API_KEY")
    discord_webhook_url = _require_env("DISCORD_WEBHOOK_URL")

    raw_events = fetch_streaming_status(database_url)
    print("raw_streaming_status=" + json.dumps(raw_events, default=str))

    summary = summarize_with_ollama(ollama_base_url, ollama_model, ollama_api_key, raw_events)
    print("summary_generated ok")

    post_to_discord(discord_webhook_url, summary)
    print("posted_to_discord ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
