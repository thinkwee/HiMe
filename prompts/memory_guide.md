# Memory Database Reference

You write to the agent's memory database via `sql(database='memory', query=...)`. Health data lives in a separate `health_data` DB which you cannot touch.

## What memory is (concept)

`health_data` is the passive stream of objective sensor samples — it can never be written. `memory` is the agent's persistent **self**: the writable layer where subjective context, user statements, prior findings, scheduled work, trigger rules, and any cross-cycle state live. It is the seam where objective sensor signals meet the subjective and agent-generated information that gives them meaning.

Three categories of tables coexist in this DB, and you should be aware of all of them when reading or writing:

1. **Framework contract tables** — listed below under "Fixed tables". The backend, cron scheduler, trigger evaluator and messaging-gateway evidence layer depend on these schemas; do not break them.
2. **Semantic tables you (or a previous run of you) created** — open-ended tables for things the user told the agent or that the agent decided to remember. Schemas were chosen at write time, so always confirm columns before assuming.
3. **Personalised-page tables** — when a `create_page` call generates a page, the page's `route.py` may build its own tables to back its UI. These are not listed in this file and appear/disappear with pages, so always discover via `sqlite_master` rather than relying on a hardcoded list.

The read sub-agent (`sub_analysis`) can SELECT from this database too, but only **you** can write. When you store a user-stated fact here, you are giving every future analysis run an *anchor* it can join health_data against — that is the highest-leverage thing you can write.

## Fixed tables (always exist)

| table | purpose | your access |
|---|---|---|
| `reports` | Published analysis reports (written by the autonomous wrapper's `push_report`) | read-only via SELECT |
| `activity_log` | System event log | read-only |
| `scheduled_tasks` | Cron-scheduled analysis tasks (`id, cron_expr, prompt_goal, status, last_run_at, created_at`) | full CRUD |
| `trigger_rules` | Event-driven analysis rules (`id, name, feature_type, condition, threshold, window_minutes, cooldown_minutes, prompt_goal, status, last_triggered_at, trigger_count, created_at`) | full CRUD |
| `personalised_pages` | Pages registered by `create_page` (`page_id, display_name, description, backend_route, frontend_asset, status, created_at`) | full CRUD |
| `message_evidence` | Evidence trail for fact verification | read-only |

**Schema discovery**: tables evolve over time and you may have created custom ones in the past. Before querying any table, confirm its columns first — never guess:

```
sql(database='memory', query='PRAGMA table_info(reports)')
sql(database='memory', query="SELECT name FROM sqlite_master WHERE type='table'")
```

## Writing operations by table

Three tables accept full CRUD from this role: `scheduled_tasks`, `trigger_rules`, and `personalised_pages`. The column-by-column meaning and the CRUD semantics are below. Never guess values — if the intent is unclear, do a `SELECT` on an existing row first to see how the column is shaped.

### `scheduled_tasks` — cron-driven analysis jobs

Each row is one recurring analysis that the scheduler will launch on a cron cadence.

Columns:
- `id` (INTEGER, auto) — primary key; used to target `UPDATE` and `DELETE`.
- `cron_expr` (TEXT) — a standard 5-field cron expression in the system timezone. Decides *when* the task fires.
- `prompt_goal` (TEXT) — the self-contained analysis goal handed to sub_analysis when the cron fires. Must read sensibly without any surrounding context, because the sub-agent will not see any.
- `status` (TEXT) — `'active'` or `'paused'`. Only `'active'` rows are evaluated by the scheduler.
- `last_run_at` (TEXT) — framework-managed; treat as read-only.
- `created_at` (TEXT) — framework default.

Operations:
- **Create**: `INSERT` a row with `cron_expr`, `prompt_goal`, and `status='active'`.
- **Pause / resume**: `UPDATE status` between `'active'` and `'paused'`. Reversible and preserves history.
- **Reschedule or rewrite**: `UPDATE cron_expr` and/or `prompt_goal` on an existing row. Prefer this over delete-and-recreate so the `id` and its history stay stable.
- **Remove permanently**: `DELETE WHERE id=?`. Use only when the task is genuinely obsolete; pausing is the safer default.
- **Inspect**: `SELECT` by `id`, `status`, or any column.

### `trigger_rules` — event-driven analysis rules

Each row is a condition that, when met by freshly-ingested health data, queues a sub_analysis run.

Columns:
- `id` (INTEGER, auto) — primary key.
- `name` (TEXT) — human-readable label for the rule.
- `feature_type` (TEXT) — the health feature the rule watches. Must be a valid `feature_type` value from `health_data`; confirm against `data_schema.md` before writing.
- `condition` (TEXT) — comparison operator. Supported values: `gt`, `lt`, `gte`, `lte`, `avg_gt`, `avg_lt`, `spike`, `drop`, `delta_gt`, `absent`.
- `threshold` (REAL) — the numeric bound the condition compares against. Units match the stored unit of the feature (see `data_schema.md`).
- `window_minutes` (INTEGER) — lookback window the evaluator considers when computing averages, spikes, or absence.
- `cooldown_minutes` (INTEGER) — minimum gap between two firings of the same rule, to prevent spam.
- `prompt_goal` (TEXT) — self-contained analysis goal handed to sub_analysis on fire. Same rules as for `scheduled_tasks`.
- `status` (TEXT) — `'active'` or `'paused'`.
- `last_triggered_at`, `trigger_count`, `created_at` — framework-managed; treat as read-only.

Operations:
- **Create**: `INSERT` with the eight authored columns (`name`, `feature_type`, `condition`, `threshold`, `window_minutes`, `cooldown_minutes`, `prompt_goal`, `status='active'`).
- **Pause / resume**: `UPDATE status`.
- **Tune**: `UPDATE threshold`, `window_minutes`, `cooldown_minutes`, or `prompt_goal` on an existing row.
- **Remove permanently**: `DELETE WHERE id=?` when the rule is truly obsolete.
- **Inspect**: `SELECT` by `id`, `status`, `feature_type`, or any column.

### `personalised_pages` — registry of pages you have created

This table is normally managed by the `create_page` tool; direct SQL is the exception, not the rule.

Columns:
- `id` (INTEGER, auto) — primary key.
- `page_id` (TEXT, unique) — the stable identifier that both the frontend and the backend route use.
- `display_name`, `description` (TEXT) — shown in the page list UI.
- `backend_route`, `frontend_asset` (TEXT) — paths written by `create_page`; do not rewrite by hand.
- `status` (TEXT) — `'active'` or `'deleted'`. Soft-deleted pages are hidden from the list but their rows (and associated history / evidence trails) remain intact.
- `created_at` (TEXT) — framework default.

Operations:
- **Create** a page: call `create_page` (which inserts the row and writes the HTML and `route.py` files on disk). Do not raw-`INSERT` into this table.
- **Patch** a page's code: call `create_page` with `patch=True`.
- **Soft-delete**: `UPDATE personalised_pages SET status='deleted' WHERE page_id=?`. This is the only direct-SQL write you should normally do on this table.
- **Inspect**: `SELECT` by `page_id` or `status`.

## Custom tables

You may create your own tables for persistence (e.g. user-shared context like medications, lifestyle changes, goals):

```sql
CREATE TABLE IF NOT EXISTS my_table (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%S', 'now')),
  ...
);
```

## What NOT to store

- **Static "normal range" baselines** (e.g. "user's normal HR is 65 bpm"). These go stale and mislead future analysis. Health baselines are derived fresh from data every time, never persisted.
- Past observations as facts. If you store observations at all, treat them as **hypotheses to verify against fresh data**, not as ground truth.
