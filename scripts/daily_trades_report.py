"""Daily trade summary report.

Pulls the day's executed trades from Postgres, asks an OpenAI-compatible Ollama
endpoint to summarize the raw results into human-readable text, and posts that
summary to a Discord channel via webhook.

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

DAILY_TRADES_QUERY = """
    SELECT *
    FROM execution.trades
    WHERE created_at::date = current_date
    ORDER BY created_at DESC
"""


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"required environment variable {name} is not set")
    return value


def fetch_daily_trades(database_url: str) -> Any:
    """Best-effort fetch of today's executed trades."""
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            try:
                rows = conn.execute(text(DAILY_TRADES_QUERY)).mappings().all()
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
                    "You are a trading assistant summarizing the day's executed trades "
                    "for a quant trading platform. Given raw JSON records from the "
                    "execution.trades table created today, write a concise, "
                    "human-readable summary. State how many trades executed today, "
                    "break down activity by symbol/side, summarize total and notable "
                    "quantities/prices/PnL where present, and call out any anomalies or "
                    "errors. Keep it under 1500 characters, use plain text suitable for "
                    "a Discord message, and use short bullet-style lines."
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

    raw_trades = fetch_daily_trades(database_url)
    print("raw_daily_trades=" + json.dumps(raw_trades, default=str))

    summary = summarize_with_ollama(ollama_base_url, ollama_model, ollama_api_key, raw_trades)
    print("summary_generated ok")

    post_to_discord(discord_webhook_url, summary)
    print("posted_to_discord ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
