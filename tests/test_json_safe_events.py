"""Non-finite floats in agent events must not break strict-JSON consumers."""
import json
import math

from backend.agent.chat_stream import json_safe


def test_json_safe_replaces_non_finite_floats() -> None:
    event = {
        "type": "chat_tool_result",
        "result": {"rows": [[1.5, float("nan")], [float("inf"), -float("inf")]], "n": 2},
        "tuple": (float("nan"), "x"),
    }
    safe = json_safe(event)
    # Strict JSON (what browsers / Starlette accept) now serialises.
    text = json.dumps(safe, allow_nan=False)
    back = json.loads(text)
    assert back["result"]["rows"] == [[1.5, None], [None, None]]
    assert back["tuple"] == [None, "x"]
    assert back["result"]["n"] == 2
    # Original is untouched.
    assert math.isnan(event["result"]["rows"][0][1])


def test_legacy_nan_rows_read_back_as_null(tmp_path) -> None:
    import asyncio

    from backend.agent.memory_manager import MemoryManager

    mm = MemoryManager.__new__(MemoryManager)
    mm.user_id = "LiveUser"
    mm.db_file = tmp_path / "m.db"
    mm.ACTIVITY_LIMIT = 2000
    # Write a legacy row exactly as the old code did (json.dumps allows NaN).
    mm._persist_activity_sync({"type": "chat_tool_result", "v": float("nan")})
    events = mm.get_recent_activity(10)
    assert events[-1]["data"]["v"] is None
    json.dumps(events, allow_nan=False)
    assert asyncio.iscoroutinefunction(mm.persist_activity)
