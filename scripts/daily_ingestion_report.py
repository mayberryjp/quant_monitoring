"""Daily ingestion status report.

Pulls the latest ingestion/compute run from each of the four quant services'
Postgres schemas (quant_symbols, quant_daily_bars, quant_momentum,
quant_indicators), asks an OpenAI-compatible Ollama endpoint to summarize the
raw results into human-readable text, and posts that summary to a Discord
channel via webhook.

Configuration is read entirely from the environment (see docker-compose.yml):
  QUANT_DATABASE_URL   Shared Postgres connection string for the quant services.
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

LATEST_RUN_QUERIES: dict[str, str] = {
    "quant_symbols": """
        SELECT r.id, v.code AS vendor, r.endpoint, r.status, r.started_at, r.finished_at,
               r.records_seen, r.records_inserted, r.records_failed,
               r.symbols_new, r.symbols_delisted, r.error_message
        FROM symbol_master.vendor_api_runs r
        JOIN symbol_master.vendor_sources v ON v.id = r.vendor_source_id
        ORDER BY r.started_at DESC, r.id DESC
        LIMIT 1
    """,
    "quant_daily_bars": """
        SELECT r.id, s.vendor_name, r.mode, r.status,
               r.requested_start_date, r.requested_end_date,
               r.symbols_requested, r.symbols_succeeded, r.symbols_failed,
               r.bars_upserted, r.errors, r.error_message,
               r.duration_seconds, r.started_at, r.finished_at
        FROM market_data.vendor_bar_runs r
        JOIN market_data.vendor_bar_sources s ON s.id = r.vendor_source_id
        ORDER BY r.id DESC
        LIMIT 1
    """,
    "quant_momentum": """
        SELECT * FROM momentum.momentum_runs ORDER BY id DESC LIMIT 1
    """,
    "quant_indicators": """
        SELECT id, mode, status, adjustment_type, requested_start_date, requested_end_date,
               symbols_requested, symbols_succeeded, symbols_failed, indicators_run,
               values_upserted, errors, error_message, duration_seconds, started_at, finished_at
        FROM indicators.indicator_runs
        ORDER BY id DESC
        LIMIT 1
    """,
}


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"required environment variable {name} is not set")
    return value


def fetch_latest_runs(database_url: str) -> dict[str, Any]:
    """Best-effort fetch of the latest run row per service. One service's
    failure (e.g. schema not migrated yet) never blocks the others."""
    engine = create_engine(database_url, pool_pre_ping=True)
    results: dict[str, Any] = {}
    try:
        with engine.connect() as conn:
            for service, query in LATEST_RUN_QUERIES.items():
                try:
                    row = conn.execute(text(query)).mappings().first()
                    results[service] = dict(row) if row is not None else None
                except Exception as exc:  # noqa: BLE001
                    results[service] = {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        engine.dispose()
    return results


def summarize_with_ollama(base_url: str, model: str, api_key: str | None, raw_data: dict) -> str:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an operations assistant summarizing daily data-pipeline "
                    "ingestion runs for a quant trading data platform. Given raw JSON "
                    "run records from four services (quant_symbols, quant_daily_bars, "
                    "quant_momentum, quant_indicators), write a concise, human-readable "
                    "status report. For each service, state whether the latest run "
                    "succeeded or failed, key counts (records/symbols/bars/values), and "
                    "call out any errors or anomalies. Keep it under 1500 characters, "
                    "use plain text suitable for a Discord message, and use short "
                    "bullet-style lines per service."
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
    database_url = _require_env("QUANT_DATABASE_URL")
    ollama_base_url = _require_env("OLLAMA_BASE_URL")
    ollama_model = _require_env("OLLAMA_MODEL")
    ollama_api_key = os.environ.get("OLLAMA_API_KEY")
    discord_webhook_url = _require_env("DISCORD_WEBHOOK_URL")

    raw_runs = fetch_latest_runs(database_url)
    print("raw_latest_runs=" + json.dumps(raw_runs, default=str))

    summary = summarize_with_ollama(ollama_base_url, ollama_model, ollama_api_key, raw_runs)
    print("summary_generated ok")

    post_to_discord(discord_webhook_url, summary)
    print("posted_to_discord ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
