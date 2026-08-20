import importlib
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

daily_ingestion_report = importlib.import_module("daily_ingestion_report")


class FakeResponse:
    def __init__(self, json_body=None, status_code=200):
        self._json_body = json_body or {}
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json_body


def test_require_env_raises_when_missing(monkeypatch):
    monkeypatch.delenv("SOME_VAR", raising=False)
    with pytest.raises(SystemExit):
        daily_ingestion_report._require_env("SOME_VAR")


def test_summarize_with_ollama_posts_expected_payload(monkeypatch):
    captured = {}

    def fake_post(url, json, headers, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return FakeResponse({"choices": [{"message": {"content": "summary text"}}]})

    monkeypatch.setattr(daily_ingestion_report.requests, "post", fake_post)

    result = daily_ingestion_report.summarize_with_ollama(
        "http://ollama:11434/v1", "llama3.1", "secret", {"quant_symbols": {"status": "ok"}}
    )

    assert result == "summary text"
    assert captured["url"] == "http://ollama:11434/v1/chat/completions"
    assert captured["json"]["model"] == "llama3.1"
    assert captured["headers"]["Authorization"] == "Bearer secret"


def test_post_to_discord_chunks_long_messages(monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json["content"])
        return FakeResponse()

    monkeypatch.setattr(daily_ingestion_report.requests, "post", fake_post)

    long_message = "x" * 2500
    daily_ingestion_report.post_to_discord("https://discord.example/webhook", long_message)

    assert len(calls) == 2
    assert len(calls[0]) == 2000
    assert len(calls[1]) == 500


def test_fetch_latest_runs_isolates_per_service_errors(monkeypatch):
    class FakeConnection:
        def execute(self, query):
            raise RuntimeError("relation does not exist")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class FakeEngine:
        def connect(self):
            return FakeConnection()

        def dispose(self):
            pass

    monkeypatch.setattr(daily_ingestion_report, "create_engine", lambda *a, **k: FakeEngine())

    results = daily_ingestion_report.fetch_latest_runs("sqlite:///:memory:")

    assert set(results) == set(daily_ingestion_report.LATEST_RUN_QUERIES)
    for service_result in results.values():
        assert "error" in service_result
