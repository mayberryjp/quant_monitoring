"""Daily trade summary report.

Pulls the day's PnL summary from the execution PnL API, asks an
OpenAI-compatible Ollama endpoint to summarize the raw results into
human-readable text, and posts that summary to a Discord channel via webhook.

Configuration is read entirely from the environment (see docker-compose.yml):
  PNL_API_BASE_URL     Base URL of the execution PnL API. Defaults to
                       http://execution.quant.mayberry.farm:8028
  OLLAMA_BASE_URL      Base URL of an OpenAI-compatible Ollama endpoint, e.g.
                       http://ollama:11434/v1
  OLLAMA_MODEL                Model name to use for summarization.
  DAILY_TRADES_OLLAMA_MODEL   Optional per-script override of OLLAMA_MODEL.
  OLLAMA_API_KEY       Optional bearer token (Ollama typically ignores this).
  DISCORD_WEBHOOK_URL  Discord incoming webhook URL to post the summary to.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from typing import Any

import requests

DISCORD_MESSAGE_LIMIT = 2000

REPORT_TITLE = "💰 Daily Trades Report"

DEFAULT_PNL_API_BASE_URL = "http://execution.quant.mayberry.farm:8028"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"required environment variable {name} is not set")
    return value


def fetch_daily_trades(pnl_api_base_url: str) -> Any:
    """Best-effort fetch of today's PnL summary from the execution PnL API."""
    trade_date = datetime.now().strftime("%Y-%m-%d")
    url = f"{pnl_api_base_url.rstrip('/')}/pnl/{trade_date}"
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def summarize_with_ollama(base_url: str, model: str, api_key: str | None, raw_data: Any) -> str:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a trading assistant summarizing the day's PnL for a quant "
                    "trading platform. Given raw JSON from the execution PnL API for "
                    "today, write a concise, human-readable summary that describes the "
                    "number of trades, the realized P&L for the date, the number of "
                    "winning and losing trades, and whether it was live or paper "
                    "trading. Do not mention the amount invested or gross proceeds. Use "
                    "plain text suitable for a Discord message."
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
    pnl_api_base_url = os.environ.get("PNL_API_BASE_URL", DEFAULT_PNL_API_BASE_URL)
    ollama_base_url = _require_env("OLLAMA_BASE_URL")
    ollama_model = os.environ.get("DAILY_TRADES_OLLAMA_MODEL") or _require_env("OLLAMA_MODEL")
    ollama_api_key = os.environ.get("OLLAMA_API_KEY")
    discord_webhook_url = _require_env("DISCORD_WEBHOOK_URL")

    raw_trades = fetch_daily_trades(pnl_api_base_url)
    print("raw_daily_trades=" + json.dumps(raw_trades, default=str))

    summary = summarize_with_ollama(ollama_base_url, ollama_model, ollama_api_key, raw_trades)
    print("summary_generated ok")

    post_to_discord(discord_webhook_url, f"**{REPORT_TITLE}**\n{summary}")
    print("posted_to_discord ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
