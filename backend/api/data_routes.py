"""
Data routes — user and feature data access.

Design notes
------------
- The data reader is **lazy-initialised** on first request rather than at
  module import time.
- Only the 'live' data source (real-time HealthKit via WatchExporter) is
  supported.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from ..data_readers import BaseDataReader, create_reader
from ..utils import dataframe_to_json_safe

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/data", tags=["data"])


# ---------------------------------------------------------------------------
# Reader cache — lazily initialised
# ---------------------------------------------------------------------------

_reader: BaseDataReader | None = None


def _ensure_reader() -> BaseDataReader:
    """Return the cached reader, initialising on first call."""
    global _reader
    if _reader is None:
        base_dir = Path(__file__).parent.parent.parent
        path = (base_dir / "ios" / "Server").resolve()
        logger.info("Initialising data reader: source=live path=%s", path)
        _reader = create_reader("live", path)
        logger.info("Data reader ready: live")
    return _reader


def _flush_reader() -> None:
    """Invalidate the cached reader."""
    global _reader
    _reader = None


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------

@router.post("/reload")
async def reload_reader():
    """Force-reload the data reader."""
    _flush_reader()
    try:
        await asyncio.to_thread(_ensure_reader)
        return {"success": True, "data_source": "live"}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Participants
# ---------------------------------------------------------------------------

@router.get("/users")
async def list_users(datasets: list[str] | None = Query(None)):
    """List all available users."""
    reader = _init_or_raise()
    try:
        users = await asyncio.to_thread(reader.get_available_users, datasets)
        ds = datasets or await asyncio.to_thread(reader.get_datasets)
        return {
            "success":      True,
            "users": users,
            "count":        len(users),
            "datasets":     ds,
            "data_source":  "live",
        }
    except Exception as exc:
        logger.error("Error listing users: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Features
# ---------------------------------------------------------------------------

@router.get("/features/{pid}")
async def get_user_features(
    pid: str,
    feature_type: str = Query("steps"),
):
    """Get available feature columns for a user."""
    reader = _init_or_raise()
    try:
        df = await asyncio.to_thread(reader.load_feature_data, [pid], feature_type)
        if df.empty:
            return {
                "success":      True,
                "user":  pid,
                "feature_type": feature_type,
                "features":     [],
                "num_features": 0,
                "num_records":  0,
                "date_range":   {"start": None, "end": None},
                "data_source":  "live",
            }
        features   = [c for c in df.columns if c not in ("pid", "date", "dataset")]
        date_range = await asyncio.to_thread(reader.get_date_range, [pid])
        return {
            "success":      True,
            "user":  pid,
            "feature_type": feature_type,
            "features":     features,
            "num_features": len(features),
            "date_range":   {
                "start": date_range[0].strftime('%Y-%m-%dT%H:%M:%S') if date_range[0] else None,
                "end":   date_range[1].strftime('%Y-%m-%dT%H:%M:%S') if date_range[1] else None,
            },
            "num_records":  len(df),
            "data_source":  "live",
        }
    except Exception as exc:
        logger.error("Error fetching features for %s: %s", pid, exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/inspect/{pid}")
async def inspect_user_data(
    pid:          str,
    feature_type: str = Query("steps"),
    limit:        int = Query(100, ge=1, le=1000),
):
    """Return a raw data sample for a user + feature combination."""
    reader = _init_or_raise()
    try:
        df = await asyncio.to_thread(reader.load_feature_data, [pid], feature_type)
        if df.empty:
            return Response(
                content=json.dumps({
                    "success":      True,
                    "user":  str(pid),
                    "feature_type": str(feature_type),
                    "num_records":  0,
                    "sample_size":  0,
                    "columns":      [],
                    "data":         []
                }),
                media_type="application/json",
            )

        sample       = df.head(limit)
        data_records = dataframe_to_json_safe(sample)

        result = {
            "success":      True,
            "user":  str(pid),
            "feature_type": str(feature_type),
            "num_records":  int(len(df)),
            "sample_size":  int(len(sample)),
            "columns":      [str(c) for c in sample.columns.tolist()],
            "data":         data_records,
        }

        json_str = json.dumps(result).replace("-Infinity", "null").replace("Infinity", "null").replace("NaN", "null")
        return Response(content=json_str, media_type="application/json")

    except Exception as exc:
        logger.error("Error inspecting data for %s/%s: %s", pid, feature_type, exc, exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Metadata endpoints
# ---------------------------------------------------------------------------

@router.get("/datasets")
async def list_datasets():
    reader = _init_or_raise()
    datasets = await asyncio.to_thread(reader.get_datasets)
    return {"success": True, "datasets": datasets, "count": len(datasets), "data_source": "live"}


@router.get("/feature_types")
async def list_feature_types():
    reader = _init_or_raise()
    feature_types = await asyncio.to_thread(reader.get_feature_types)
    return {"success": True, "feature_types": feature_types, "data_source": "live"}


@router.get("/source")
async def get_data_source():
    return {
        "success":          True,
        "data_source":      "live",
        "available_sources": ["live"],
    }


@router.get("/feature_metadata")
async def get_feature_metadata():
    """Feature display metadata (unit, format, scale)."""
    try:
        from ..data_readers.apple_health_features import FEATURE_SPEC
        features = {
            k: {
                "display_unit":  v.get("display_unit", ""),
                "format":        v.get("format", "{:.2f}"),
                "display_scale": v.get("display_scale", 1),
            }
            for k, v in FEATURE_SPEC.items()
        }
        return {"success": True, "features": features, "data_source": "live"}
    except Exception as exc:
        logger.error("Error loading feature metadata: %s", exc)
        return {"success": False, "features": {}, "error": str(exc)}


# ---------------------------------------------------------------------------
# Dashboard summary (used by iOS client)
# ---------------------------------------------------------------------------

@router.get("/count")
async def get_storage_count():
    """Return the true total row count in the live health_samples_eav table.

    Used by the frontend Storage Total card. Cheap COUNT(*) query — does not
    load any sample payloads, so it's not subject to the dashboard endpoint's
    per-feature truncation cap.
    """
    reader = _init_or_raise()
    try:
        total = await asyncio.to_thread(reader.get_total_sample_count)
        return {"success": True, "count": int(total)}
    except Exception as exc:
        logger.error("Storage count error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/dashboard")
async def get_dashboard_data(minutes: int = Query(1440, ge=10, le=43200)):
    """Return time-series data for all features with data, for iOS dashboard charts.

    Returns a dict keyed by feature name, each containing an array of
    ``{ts, v}`` objects sorted ascending by timestamp.
    """
    _MAX_POINTS_PER_FEATURE = 2000
    reader = _init_or_raise()
    try:
        feature_types = await asyncio.to_thread(reader.get_feature_types)

        async def _load_feature(ft: str):
            try:
                df = await asyncio.to_thread(
                    lambda _ft=ft: reader.load_feature_data(["LiveUser"], _ft, minutes=minutes)
                )
                if df.empty:
                    return ft, []
                df = df.tail(_MAX_POINTS_PER_FEATURE)
                points = [
                    {"ts": float(row["ts"]), "v": float(row["value"])}
                    for _, row in df.iterrows()
                ]
                return ft, points
            except Exception:
                return ft, []

        results = await asyncio.gather(*[_load_feature(ft) for ft in feature_types])
        result: dict = {ft: pts for ft, pts in results if pts}
        return {"success": True, "features": result, "count": len(result)}
    except Exception as exc:
        logger.error("Dashboard data error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _init_or_raise() -> BaseDataReader:
    """Initialise reader or surface a clean 500 error."""
    try:
        return _ensure_reader()
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Data reader initialisation failed: {exc}",
        ) from exc
