"""Daily sticky-notes trade signals report.

Pulls the day's latest active sticky-note trade signals from Postgres, asks an
OpenAI-compatible Ollama endpoint to summarize the raw results into
human-readable text, and posts that summary to a Discord channel via webhook.

Configuration is read entirely from the environment (see docker-compose.yml):
  DATABASE_URL         Shared Postgres connection string.
  OLLAMA_BASE_URL      Base URL of an OpenAI-compatible Ollama endpoint, e.g.
                       http://ollama:11434/v1
  OLLAMA_MODEL                Model name to use for summarization.
  STICKY_NOTES_OLLAMA_MODEL   Optional per-script override of OLLAMA_MODEL.
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

REPORT_TITLE = "📝 Sticky Notes Report"

STICKY_NOTES_QUERY = """
    SELECT *
    FROM sticky_notes
    WHERE status = 'active'
      AND signal_date = (
          SELECT MAX(signal_date)
          FROM sticky_notes
          WHERE status = 'active'
      )
    ORDER BY created_at DESC
"""


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"required environment variable {name} is not set")
    return value


def fetch_sticky_notes(database_url: str) -> Any:
    """Best-effort fetch of the day's latest active sticky-note signals."""
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            try:
                rows = conn.execute(text(STICKY_NOTES_QUERY)).mappings().all()
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
                    "You are a trading assistant reporting on the day's latest active "
                    "sticky-note trade signals for a quant trading platform. Given raw "
                    "JSON records from the sticky_notes table, write a short report that "
                    "states how many tickers are in the list (a count) and whether the "
                    "list is from the most recent date. Use plain text suitable for a "
                    "Discord message. Keep it brief, a sentence or two is enough. Match "
                    "this format exactly: 'There are 71 active sticky-note trade signals "
                    "for the most recent date (date 2026-08-29).' Do not provide any "
                    "additional info or analysis."
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
    ollama_model = os.environ.get("STICKY_NOTES_OLLAMA_MODEL") or _require_env("OLLAMA_MODEL")
    ollama_api_key = os.environ.get("OLLAMA_API_KEY")
    discord_webhook_url = _require_env("DISCORD_WEBHOOK_URL")

    raw_notes = fetch_sticky_notes(database_url)
    print("raw_sticky_notes=" + json.dumps(raw_notes, default=str))

    summary = summarize_with_ollama(ollama_base_url, ollama_model, ollama_api_key, raw_notes)
    print("summary_generated ok")

    post_to_discord(discord_webhook_url, f"**{REPORT_TITLE}**\n{summary}")
    print("posted_to_discord ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
