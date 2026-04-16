"""
Shared fixtures for the HIME backend test suite.

Provides:
- Temporary directories for databases and files
- Mock settings that point to temporary paths
- Pre-populated SQLite databases (health_data, memory, watch.db)
- An httpx.AsyncClient wired to the FastAPI app (via ASGITransport)
- Mock agent and LLM provider stubs
"""
from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

# ---------------------------------------------------------------------------
# Environment safety — prevent accidental real API calls during tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _safe_env(monkeypatch):
    """Ensure tests never accidentally call real LLM APIs."""
    monkeypatch.setenv("DEFAULT_LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "test-placeholder")
    monkeypatch.setenv("TELEGRAM_GATEWAY_ENABLED", "false")
    monkeypatch.setenv("FEISHU_GATEWAY_ENABLED", "false")


# ---------------------------------------------------------------------------
# Temporary directory fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dirs(tmp_path: Path) -> dict[str, Path]:
    """Create the directory tree HIME expects, rooted in a pytest tmp_path."""
    dirs = {
        "root": tmp_path,
        "memory": tmp_path / "memory",
        "data_stores": tmp_path / "data_stores",
        "logs": tmp_path / "logs",
        "prompts": tmp_path / "prompts",
        "agent_states": tmp_path / "memory" / "agent_states",
        "watch": tmp_path / "watch",
        "personalised_pages": tmp_path / "data" / "personalised_pages",
    }
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    return dirs


# ---------------------------------------------------------------------------
# Mock settings
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_settings(tmp_dirs):
    """Return a patched Settings object whose paths all point to tmp_dirs."""
    from backend.config import Settings

    s = Settings(
        MEMORY_DB_PATH=tmp_dirs["memory"],
        DATA_STORE_PATH=tmp_dirs["data_stores"],
        AGENT_LOGS_PATH=tmp_dirs["logs"],
        AGENT_STATES_PATH=tmp_dirs["agent_states"],
        AGENT_LAST_CONFIG_PATH=tmp_dirs["memory"] / "agent_last_config.json",
        APP_STATE_PATH=tmp_dirs["memory"] / "app_state.json",
        AUTO_RESTORE_AGENT=False,
        DATA_SOURCE="live",
        TELEGRAM_GATEWAY_ENABLED=False,
        # Provide dummy API keys so provider init doesn't complain
        OPENAI_API_KEY="test-key",
        GOOGLE_API_KEY="test-key",
        ANTHROPIC_API_KEY="test-key",
    )
    return s


# ---------------------------------------------------------------------------
# Realistic Apple Health mock data
# ---------------------------------------------------------------------------

MOCK_HEALTH_RECORDS: list[dict[str, Any]] = []

def _build_mock_health_records() -> list[dict[str, Any]]:
    """Generate realistic Apple Health data for the last 24 hours."""
    records: list[dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    feature_specs = {
        "heart_rate": (55.0, 120.0),
        "steps": (0.0, 500.0),
        "active_energy": (0.0, 150.0),
        "heart_rate_variability": (20.0, 80.0),
        "blood_oxygen": (94.0, 100.0),
        "resting_heart_rate": (50.0, 70.0),
        "respiratory_rate": (12.0, 20.0),
    }
    # Generate one record every 5 minutes for each feature
    import random
    random.seed(42)
    for minutes_ago in range(0, 1440, 5):  # 24 hours, every 5 min
        dt = now - timedelta(minutes=minutes_ago)
        for feat, (lo, hi) in feature_specs.items():
            records.append({
                "date": dt.strftime('%Y-%m-%dT%H:%M:%S'),
                "value": round(random.uniform(lo, hi), 2),
                "feature_type": feat,
                "pid": "LiveUser",
            })
    return records

MOCK_HEALTH_RECORDS = _build_mock_health_records()


@pytest.fixture
def mock_health_records() -> list[dict[str, Any]]:
    """Return a list of realistic mock Apple Health records."""
    return MOCK_HEALTH_RECORDS


# ---------------------------------------------------------------------------
# SQLite database fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def health_data_db(tmp_dirs, mock_health_records) -> Path:
    """
    Create and populate a health_data SQLite database (DataStore format).
    Returns the path to the .db file.
    """
    db_file = tmp_dirs["data_stores"] / "LiveUser_data.db"
    with sqlite3.connect(db_file) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS samples (
                timestamp TEXT NOT NULL,
                feature_type TEXT NOT NULL,
                value REAL,
                metadata TEXT,
                ingested_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
                PRIMARY KEY (timestamp, feature_type)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS store_metadata (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now'))
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_timestamp ON samples(timestamp)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_samples_feature ON samples(feature_type)"
        )
        rows = [
            (r["date"], r["feature_type"], r["value"], None)
            for r in mock_health_records
        ]
        conn.executemany(
            "INSERT OR REPLACE INTO samples (timestamp, feature_type, value, metadata) VALUES (?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    return db_file


@pytest.fixture
def memory_db(tmp_dirs) -> Path:
    """
    Create a memory DB with the mandatory schema.
    Returns the path to the .db file.
    """
    from backend.agent.memory_manager import _ensure_schema

    db_file = tmp_dirs["memory"] / "LiveUser.db"
    with sqlite3.connect(db_file) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        _ensure_schema(conn)
        # Insert a sample report
        conn.execute(
            "INSERT INTO reports (title, content, alert_level, time_range_start, time_range_end) "
            "VALUES (?, ?, ?, ?, ?)",
            ("Test Report", "Some analysis content", "normal",
             "2026-03-19T00:00:00", "2026-03-19T23:59:59"),
        )
        # Insert sample activity log entries
        for i in range(5):
            conn.execute(
                "INSERT INTO activity_log (event_type, event_data) VALUES (?, ?)",
                (f"test_event_{i}", json.dumps({"type": f"test_event_{i}", "data": f"value_{i}"})),
            )
        conn.commit()
    return db_file


@pytest.fixture
def watch_db(tmp_dirs) -> Path:
    """
    Create a mock watch.db (WatchExporter format) with EAV health samples.
    Returns the path to the .db file.
    """
    db_file = tmp_dirs["watch"] / "watch.db"
    now_ts = time.time()
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS health_samples_eav (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                f TEXT NOT NULL,
                ts REAL NOT NULL,
                v REAL NOT NULL
            )
        """)
        rows = []
        import random
        random.seed(123)
        for i in range(100):
            ts = now_ts - (100 - i) * 60  # one record per minute
            feature = random.choice(["heart_rate", "steps", "active_energy"])
            value = round(random.uniform(50, 150), 2)
            rows.append((feature, ts, value))
        conn.executemany(
            "INSERT INTO health_samples_eav (f, ts, v) VALUES (?, ?, ?)", rows
        )
        conn.commit()
    return db_file


# ---------------------------------------------------------------------------
# DataStore fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def data_store(tmp_dirs, health_data_db):
    """Return a DataStore instance backed by the populated health_data_db."""
    from backend.agent.data_store import DataStore
    return DataStore(db_path=tmp_dirs["data_stores"], user_id="LiveUser")


# ---------------------------------------------------------------------------
# MemoryManager fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def memory_manager(tmp_dirs, memory_db):
    """Return a MemoryManager instance backed by the pre-seeded memory DB."""
    from backend.agent.memory_manager import MemoryManager
    return MemoryManager(tmp_dirs["memory"], "LiveUser")


# ---------------------------------------------------------------------------
# Mock LLM provider
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_llm_provider():
    """Return a mock LLM provider that returns canned responses."""
    provider = MagicMock()
    provider.generate = AsyncMock(return_value={
        "content": "Test analysis response",
        "tool_calls": [],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    })
    provider.model = "test-model"
    return provider


# ---------------------------------------------------------------------------
# Mock agent
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_agent():
    """Return a mock AutonomousHealthAgent."""
    agent = MagicMock()
    agent.get_status.return_value = {
        "running": True,
        "cycle_count": 5,
        "total_tokens": 1000,
        "data_store_stats": {"total_records": 100},
    }
    agent.stop = MagicMock()
    agent.run_quick_analysis = AsyncMock(return_value={
        "state": "happy",
        "message": "Everything looks good!",
    })
    agent.run_forever = AsyncMock(return_value=iter([]))
    return agent


# ---------------------------------------------------------------------------
# FastAPI test client (httpx + ASGITransport)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def test_client(mock_settings, tmp_dirs, health_data_db, memory_db):
    """
    Create an httpx.AsyncClient connected to the FastAPI app via ASGITransport.

    The app's lifespan is bypassed: we patch settings and pre-seed state so that
    routes can be tested in isolation without starting Telegram, agents, etc.
    """
    import httpx

    # Patch settings globally before importing the app
    with patch("backend.config.settings", mock_settings), \
         patch("backend.api.agent_state.settings", mock_settings), \
         patch("backend.api.agent_lifecycle.settings", mock_settings), \
         patch("backend.api.config_routes.settings", mock_settings), \
         patch("backend.api.data_routes.settings", mock_settings), \
         patch("backend.api.page_routes.settings", mock_settings):

        # Import app AFTER patching so routers pick up mock settings
        from backend.main import app

        # Build a transport that skips the lifespan (no Telegram/agent restore)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


# ---------------------------------------------------------------------------
# Prompt files fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def prompt_files(tmp_dirs) -> Path:
    """Create stub prompt files in the temporary prompts directory."""
    prompts_dir = tmp_dirs["prompts"]
    for name, content in [
        ("soul.md", "# Soul\nI am HIME."),
        ("job.md", "# Job\nAnalyse health data."),
        ("experience.md", "# Experience\n\n<!-- Agent: editable content below. -->\n\nSome learned patterns."),
        ("user.md", "# User Profile\n\n<!-- Agent: append your observations below this line. -->\n\nUser likes morning reports."),
    ]:
        (prompts_dir / name).write_text(content, encoding="utf-8")
    return prompts_dir
