"""
Code tool — persistent IPython notebook-style execution.

Each agent session maintains a single IPython shell. Variables, imports,
and database connections persist across turns — just like cells in a
Jupyter notebook.  The agent can build on previous results without
re-importing or re-querying.

On first use the shell is bootstrapped with:
  pd, np, datetime, timedelta, timezone, sqlite3, scipy, statsmodels, sklearn
  health_db   — SQLite connection to health data (read-only)
  memory_db   — SQLite connection to agent memory (read-write)
  df          — empty DataFrame placeholder

Last-expression display is enabled: if the last line of a cell is an
expression (not an assignment / print), its repr is included in the output,
matching Jupyter notebook behaviour.
"""
from __future__ import annotations

import asyncio
import io
import logging
import sys
import textwrap
import threading
import time
from typing import Any

from .base import BaseTool

logger = logging.getLogger(__name__)


class CodeTool(BaseTool):
    """Execute Python code in a persistent IPython notebook-style session."""

    name = "code"

    # Code tool shares an IPython session — variables would conflict if run concurrently
    @property
    def is_concurrency_safe(self) -> bool:
        return False

    def __init__(self, data_store, memory_db_path, user_id: str) -> None:
        self.data_store = data_store
        self.memory_db_path = memory_db_path
        self.user_id = user_id
        self.memory_db_file = memory_db_path / f"{user_id}.db"

        from ...config import settings
        self._use_docker = getattr(settings, "CODE_TOOL_DOCKER_SANDBOX", False)
        self._docker_image = getattr(
            settings, "CODE_TOOL_DOCKER_IMAGE", "python:3.12-slim"
        )

        # Persistent IPython shell — created lazily on first execution
        self._shell: Any = None
        # Guards concurrent initialisation from warm_up + execute racing
        self._init_lock = threading.Lock()

        # Incremental df refresh state
        self._df_high_watermark: str | None = None  # max timestamp loaded so far (kept for external compat)
        self._df_max_rowid: int | None = None  # rowid watermark for incremental loads
        self._df_last_updated_at: str | None = None  # ISO-8601 watermark for catching in-place value updates
        self._df_retention_days: int = 14

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _ensure_shell(self) -> None:
        """Create (or verify) the persistent IPython shell and DB connections."""
        if self._shell is not None:
            return
        with self._init_lock:
            # Double-check: another thread may have initialized while we waited
            if self._shell is not None:
                return

            from IPython.core.interactiveshell import InteractiveShell

            # Create a fresh shell instance (not the singleton — each tool gets its own)
            shell = InteractiveShell()
            shell.colors = "NoColor"

            # Bootstrap: pre-import libraries; health data is NOT loaded here —
            # call refresh_df() after init to populate `df` with fresh data.
            bootstrap = textwrap.dedent("""\
                import pandas as pd
                import numpy as np
                from datetime import datetime, timedelta, timezone
                import sqlite3

                # Matplotlib (non-interactive backend for chart generation)
                import matplotlib
                matplotlib.use('Agg')
                import matplotlib.pyplot as plt
                plt.rcParams['figure.figsize'] = (10, 6)
                plt.rcParams['figure.dpi'] = 100

                # Optional scientific libraries
                try:
                    import scipy
                    import scipy.stats as stats
                    import scipy.signal as signal
                except ImportError:
                    scipy = stats = signal = None
                try:
                    import statsmodels.api as sm
                except ImportError:
                    sm = None
                try:
                    import sklearn
                except ImportError:
                    sklearn = None

                # Pre-loaded health data — populated by refresh_df() before each cycle
                # columns: timestamp (datetime64[ns]), feature_type (str), value (float64)
                df = pd.DataFrame(columns=['timestamp', 'feature_type', 'value'])
            """)

            result = shell.run_cell(bootstrap, silent=True, store_history=False)
            if result.error_in_exec:
                logger.error("Code tool bootstrap failed: %s", result.error_in_exec)

            # Inject pre-connected database handles into the namespace
            # so agent code can query directly (as documented in prompts)
            try:
                import sqlite3 as _sq
                health_db_path = str(self.data_store.db_file)
                shell.user_ns["health_db"] = _sq.connect(
                    health_db_path, check_same_thread=False
                )
                shell.user_ns["memory_db"] = _sq.connect(
                    str(self.memory_db_file), check_same_thread=False
                )
                logger.info("Code tool: health_db and memory_db injected")
            except Exception as exc:
                logger.warning("Code tool: failed to inject DB connections: %s", exc)

            self._shell = shell
            logger.info("Code tool: IPython session initialized for %s", self.user_id)

    def reset_session(self) -> None:
        """Reset the IPython session (e.g. on agent restart)."""
        shell = self._shell
        self._shell = None
        self._df_high_watermark = None
        self._df_max_rowid = None
        if shell is not None:
            try:
                for name in ("health_db", "memory_db"):
                    conn = shell.user_ns.get(name)
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
            except Exception as exc:
                logger.warning("Code tool: error closing DB connections during reset: %s", exc)

    def refresh_df(self, days: int = 14) -> None:
        """Incrementally refresh ``df`` in the IPython session.

        First call: full load of the last *days* of health data.
        Subsequent calls: fetch new rows (rowid > watermark) AND rows
        whose value was updated in-place (updated_at > watermark).
        This ensures cumulative metrics (steps, energy, etc.) whose
        hourly buckets are corrected by HealthKit are reflected in ``df``.
        """
        self._ensure_shell()
        import pandas as _pd

        existing_df: _pd.DataFrame = self._shell.user_ns.get("df", _pd.DataFrame())
        cutoff = f"strftime('%Y-%m-%dT%H:%M:%S', 'now', '-{days} days')"

        try:
            if self._df_max_rowid is not None and not existing_df.empty:
                cutoff_str = f"strftime('%Y-%m-%dT%H:%M:%S', 'now', '-{days} days')"

                # Fetch new rows (brand new inserts)
                sql_new = (
                    f"SELECT rowid, timestamp, feature_type, value FROM samples "
                    f"WHERE rowid > {self._df_max_rowid} AND timestamp >= {cutoff_str} "
                    f"ORDER BY rowid"
                )
                new_rows = self.data_store.query(sql_new)

                # Fetch rows updated since last refresh (in-place value changes)
                updated_rows = _pd.DataFrame()
                if self._df_last_updated_at:
                    try:
                        sql_updated = (
                            f"SELECT rowid, timestamp, feature_type, value FROM samples "
                            f"WHERE updated_at > '{self._df_last_updated_at}' "
                            f"AND rowid <= {self._df_max_rowid} "
                            f"AND timestamp >= {cutoff_str} "
                            f"ORDER BY rowid"
                        )
                        updated_rows = self.data_store.query(sql_updated)
                    except Exception:
                        # updated_at column may not exist yet (pre-migration DB)
                        pass

                if new_rows.empty and updated_rows.empty:
                    logger.debug("code: df incremental refresh — 0 new/updated rows")
                    return

                # Advance rowid watermark from new inserts
                if not new_rows.empty:
                    max_new_rowid = int(new_rows["rowid"].max())
                    self._df_max_rowid = max_new_rowid

                # Merge updated values into existing df:
                # drop old versions of updated rows, then concat everything
                if not updated_rows.empty:
                    updated_rows = updated_rows.drop(columns=["rowid"])
                    updated_rows["timestamp"] = _pd.to_datetime(
                        updated_rows["timestamp"], format="ISO8601"
                    )
                    # Build set of (timestamp, feature_type) keys to replace
                    update_keys = set(
                        zip(updated_rows["timestamp"], updated_rows["feature_type"], strict=False)
                    )
                    mask = existing_df.apply(
                        lambda r: (r["timestamp"], r["feature_type"]) not in update_keys,
                        axis=1,
                    )
                    existing_df = existing_df[mask]

                if not new_rows.empty:
                    new_rows = new_rows.drop(columns=["rowid"])
                    new_rows["timestamp"] = _pd.to_datetime(
                        new_rows["timestamp"], format="ISO8601"
                    )

                parts = [existing_df]
                if not new_rows.empty:
                    parts.append(new_rows)
                if not updated_rows.empty:
                    parts.append(updated_rows)
                combined = _pd.concat(parts, ignore_index=True)

                # Final dedup (safety net — keeps last, i.e. the updated value)
                combined = combined.drop_duplicates(
                    subset=["timestamp", "feature_type"], keep="last"
                )

                # Trim rows older than retention window
                cutoff_dt = _pd.Timestamp.now(tz="UTC") - _pd.Timedelta(days=days)
                if combined["timestamp"].dt.tz is None:
                    cutoff_dt = cutoff_dt.tz_localize(None)
                combined = combined[combined["timestamp"] >= cutoff_dt]

                self._shell.user_ns["df"] = combined
                self._df_high_watermark = str(combined["timestamp"].max())
                self._df_last_updated_at = _pd.Timestamp.now(tz="UTC").strftime(
                    "%Y-%m-%dT%H:%M:%S"
                )
                logger.info(
                    "code: df incremental refresh — +%d new, %d updated, %d total (last %d days)",
                    len(new_rows), len(updated_rows), len(combined), days,
                )
            else:
                # --- Full load (first call or after reset) ---
                sql = (
                    f"SELECT rowid, timestamp, feature_type, value FROM samples "
                    f"WHERE timestamp > {cutoff} "
                    f"ORDER BY rowid"
                )
                fresh_df = self.data_store.query(sql)
                if not fresh_df.empty:
                    self._df_max_rowid = int(fresh_df["rowid"].max())
                    fresh_df = fresh_df.drop(columns=["rowid"])
                    fresh_df["timestamp"] = _pd.to_datetime(
                        fresh_df["timestamp"], format="ISO8601"
                    )
                    fresh_df = fresh_df.drop_duplicates(subset=["timestamp", "feature_type"])
                    self._df_high_watermark = str(fresh_df["timestamp"].max())
                else:
                    self._df_max_rowid = None
                    self._df_high_watermark = None

                self._df_last_updated_at = _pd.Timestamp.now(tz="UTC").strftime(
                    "%Y-%m-%dT%H:%M:%S"
                )
                self._shell.user_ns["df"] = fresh_df if not fresh_df.empty else fresh_df
                self._df_retention_days = days
                logger.info(
                    "code: df full refresh — %d rows (last %d days)",
                    len(fresh_df), days,
                )
        except Exception as exc:
            logger.warning("code: df refresh failed: %s", exc)

    async def refresh_df_async(self, days: int = 14) -> None:
        """Async wrapper for refresh_df."""
        await asyncio.to_thread(self.refresh_df, days)

    # ------------------------------------------------------------------
    # Tool definition
    # ------------------------------------------------------------------

    def get_definition(self) -> dict:
        return self._get_definition_from_json("code")

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    async def warm_up(self) -> None:
        """Pre-initialize the IPython session and load an initial data snapshot.

        Call this once at agent startup (fire-and-forget).  IPython's import
        chain takes ~30 s on first use; pre-warming ensures the shell is ready
        before the first ``execute()`` call arrives, so the 30 s execution
        timeout only covers actual user code and never fires on init.
        """
        if self._shell is None:
            await asyncio.to_thread(self._ensure_shell)
        # Load initial df snapshot so `df` is non-empty even before first cycle
        await self.refresh_df_async()

    async def execute(self, code: str) -> dict:
        self.report_progress({"status": "executing", "code_lines": code.count('\n') + 1})

        t0 = time.perf_counter()
        if self._use_docker:
            result = await asyncio.to_thread(self._run_in_docker, code)
        else:
            # If the shell is not ready yet (warm_up still running or never
            # called), wait for it first — no timeout for pure initialisation.
            if self._shell is None:
                await asyncio.to_thread(self._ensure_shell)
            try:
                worker_thread_id: int | None = None
                original_run = self._run_in_shell

                def _tracked_run(code_str: str) -> dict[str, Any]:
                    nonlocal worker_thread_id
                    worker_thread_id = threading.get_ident()
                    return original_run(code_str)

                result = await asyncio.wait_for(
                    asyncio.to_thread(_tracked_run, code),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                # Kill the stuck worker thread so it doesn't hang forever
                if worker_thread_id is not None:
                    self._interrupt_thread(worker_thread_id)
                result = {"success": False, "error": "Code execution timed out (30s limit)."}
        elapsed = time.perf_counter() - t0
        logger.info("code: execution took %.2fs", elapsed)

        self.report_progress({
            "status": "done",
            "success": result.get("success", False),
            "has_output": bool(result.get("output")),
            "elapsed": f"{elapsed:.2f}s",
        })
        return result

    # ------------------------------------------------------------------
    # IPython notebook-style execution
    # ------------------------------------------------------------------

    def _run_in_shell(self, code: str) -> dict[str, Any]:
        """Run a code cell in the persistent IPython shell."""
        self._ensure_shell()

        code = self._sanitize_fstrings(code)

        try:
            # Capture both stdout and stderr
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            old_stdout = sys.stdout
            old_stderr = sys.stderr
            sys.stdout = stdout_buf
            sys.stderr = stderr_buf
            try:
                result = self._shell.run_cell(code, store_history=True)
            finally:
                sys.stdout = old_stdout
                sys.stderr = old_stderr

            output = stdout_buf.getvalue().rstrip()
            errors = stderr_buf.getvalue().rstrip()

            # Check for syntax errors (error_before_exec) and
            # runtime errors (error_in_exec)
            exec_error = result.error_before_exec or result.error_in_exec
            if exec_error:
                df_info = self._dataframe_context()
                resp: dict[str, Any] = {
                    "success": False,
                    "error": str(exec_error) + df_info,
                    "error_type": type(exec_error).__name__,
                    "output": output,
                }
                if errors:
                    resp["errors"] = errors
                return resp

            # Append dataframe context for successful runs too
            df_info = self._dataframe_context()
            if df_info:
                output = output.rstrip() + "\n" + df_info

            resp = {
                "success": True,
                "output": output,
            }
            if errors:
                resp["errors"] = errors
            return resp

        except Exception as exc:
            df_info = self._dataframe_context()
            return {
                "success": False,
                "error": str(exc) + df_info,
                "error_type": type(exc).__name__,
                "output": "",
            }

    def _dataframe_context(self) -> str:
        """Extract shape/column info for DataFrames in the shell namespace."""
        if self._shell is None:
            return ""
        try:
            import pandas as _pd
            parts: list[str] = []
            for name, obj in self._shell.user_ns.items():
                if name.startswith("_"):
                    continue
                if isinstance(obj, _pd.DataFrame):
                    cols = list(obj.columns)
                    if len(str(cols)) > 200:
                        cols = cols[:5] + ["..."]
                    parts.append(f"  {name}: shape={obj.shape}, columns={cols}")
            if parts:
                return "\n[DataFrames in scope]\n" + "\n".join(parts)
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _interrupt_thread(thread_id: int) -> None:
        """Raise KeyboardInterrupt in a stuck worker thread to free it."""
        import ctypes
        try:
            ret = ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(thread_id),
                ctypes.py_object(KeyboardInterrupt),
            )
            if ret == 0:
                logger.warning("code: thread %d not found for interrupt", thread_id)
            elif ret > 1:
                # Revert if more than one thread was affected (should not happen)
                ctypes.pythonapi.PyThreadState_SetAsyncExc(
                    ctypes.c_ulong(thread_id), None
                )
                logger.error("code: interrupt affected multiple threads, reverted")
            else:
                logger.info("code: interrupted stuck worker thread %d", thread_id)
        except Exception as exc:
            logger.warning("code: failed to interrupt thread %d: %s", thread_id, exc)

    @staticmethod
    def _sanitize_fstrings(code: str) -> str:
        """Replace triple-quoted f-strings with safe single-line equivalents."""
        import re

        def _replace_triple_fstring(match: re.Match) -> str:
            body = match.group(2)
            lines = [line.strip() for line in body.split("\n") if line.strip()]
            joined = "\\n".join(lines)
            return 'f"' + joined.replace('"', '\\"') + '"'

        pattern = r'f("""|\'\'\')(.*?)\1'
        return re.sub(pattern, _replace_triple_fstring, code, flags=re.DOTALL)

    # ------------------------------------------------------------------
    # Docker sandbox (fallback, no persistence)
    # ------------------------------------------------------------------

    def _run_in_docker(self, code: str) -> dict[str, Any]:
        """Run code in a Docker container. No session persistence."""
        import shutil
        import subprocess
        import tempfile

        if not shutil.which("docker"):
            logger.warning("Docker not found; falling back to IPython session.")
            return self._run_in_shell(code)

        preamble = textwrap.dedent("""
            import pandas as pd
            import numpy as np
            from datetime import datetime, timedelta
            import sqlite3
        """)
        with tempfile.NamedTemporaryFile(
            suffix=".py", mode="w", delete=False, encoding="utf-8"
        ) as f:
            f.write(preamble + "\n" + code)
            script_path = f.name

        cmd = [
            "docker", "run", "--rm",
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:size=64m",
            "--cpus", "0.5",
            "--memory", "256m",
            "--memory-swap", "256m",
            "-v", f"{script_path}:/script.py:ro",
            self._docker_image,
            "python", "/script.py",
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return {
                "success": result.returncode == 0,
                "output": result.stdout,
                "errors": result.stderr or None,
                **(
                    {"error": f"Process exited with code {result.returncode}"}
                    if result.returncode != 0 else {}
                ),
            }
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "Code execution timed out (30s limit)."}
        except Exception as exc:
            logger.error("Docker execution error: %s", exc, exc_info=True)
            return {"success": False, "error": str(exc)}
        finally:
            import os
            try:
                os.unlink(script_path)
            except OSError:
                pass
