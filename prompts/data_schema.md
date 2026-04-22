# Data Schema & Mandatory Preprocessing

## SQL Tool

You query `health_data` via the `sql` tool — read-only. The `samples` table is EAV with exactly 3 data columns — `timestamp`, `feature_type`, `value`. Feature names are **values in the `feature_type` column**, never column names; query one feature with `WHERE feature_type = '<name>'` or several at once with `WHERE feature_type IN (...)`.

## Feature Inventory

The `samples` table contains exactly the following `feature_type` values. Use names verbatim; nothing else is queryable. The unit listed is the **stored** unit — the value in the `value` column appears in that unit and must be converted for display where noted.

### Cardiovascular & respiratory
- `heart_rate` — bpm
- `resting_heart_rate` — bpm
- `walking_heart_rate_avg` — bpm
- `heart_rate_variability` — ms
- `blood_oxygen` — decimal fraction 0–1 (×100 → %)
- `respiratory_rate` — breaths / min
- `vo2max` — mL/kg·min

### Energy
- `active_energy` — kcal
- `resting_energy` — kcal

### Activity rings
- `exercise_time` — seconds (÷60 → min)
- `stand_time` — seconds (÷60 → min)

### Ambulation & gait
- `steps` — count
- `distance` — meters (÷1000 → km)
- `flights_climbed` — count
- `walking_speed` — m/s (×3.6 → km/h)
- `walking_step_length` — meters
- `walking_asymmetry` — decimal fraction 0–1 (×100 → %)
- `walking_double_support` — decimal fraction 0–1 (×100 → %)
- `walking_steadiness` — decimal fraction 0–1 (×100 → %)
- `stair_ascent_speed` — m/s
- `stair_descent_speed` — m/s
- `six_minute_walk` — meters

### Running dynamics
- `running_power` — W
- `running_stride_length` — meters
- `running_vertical_oscillation` — cm
- `running_ground_contact` — ms

### Sleep (see "Sleep Metrics Structure" below for stage semantics)
- `sleep_in_bed` — seconds (÷60 → min; total time in bed, includes awake)
- `sleep_asleep` — seconds (÷60 → min; generic asleep, **NOT** total — see Sleep Metrics Structure)
- `sleep_core` — seconds (÷60 → min; light, N1+N2)
- `sleep_deep` — seconds (÷60 → min; slow-wave, N3)
- `sleep_rem` — seconds (÷60 → min; REM)
- `sleep_awake` — seconds (÷60 → min; awake during session)
- `sleeping_wrist_temp` — °C

### Body composition
- `body_mass` — kg
- `body_mass_index` — kg/m² (unitless ratio)

### Mindfulness, daylight, hydration
- `mindful_session` — seconds (÷60 → min)
- `time_in_daylight` — seconds (÷60 → min)
- `water` — ml

### Workout event summaries
- `workout_running_distance` — meters (÷1000 → km)
- `workout_running_duration` — seconds (÷60 → min)
- `workout_running_energy` — kcal
- `workout_walking_distance` — meters (÷1000 → km)
- `workout_walking_duration` — seconds (÷60 → min)
- `workout_walking_energy` — kcal

## Timestamp Format

Timestamps in `samples` are ISO8601 strings in UTC (seconds precision, T separator, no timezone suffix):
- Format: `'YYYY-MM-DDTHH:MM:SS'`
- Range query: `timestamp BETWEEN '<start_iso>' AND '<end_iso>'`
- Time filtering: `timestamp > strftime('%Y-%m-%dT%H:%M:%S', 'now', '-1 hours')` — NOT `datetime('now', ...)` which uses a space separator and breaks text comparisons.

## Code Tool

Two data sources are pre-loaded (do NOT call `sql()` inside code, do NOT create your own `sqlite3.connect()`):
- `df` — 14-day sliding window DataFrame (fast, no query). Columns: timestamp, feature_type, value.
- `health_db` — read-only SQLite connection to ALL health data (use `pd.read_sql(...)` for >14 days).
- `memory_db` — read-write SQLite connection to agent memory tables.

```python
# Recent data via df (preferred for last 14 days)
hr = df[df['feature_type'] == 'heart_rate']

# Older data via health_db (any time range)
month = pd.read_sql("SELECT ... FROM samples WHERE timestamp > ...", health_db)
month['ts'] = pd.to_datetime(month['timestamp'], format='ISO8601')
```

**Key**: Always use `format='ISO8601'` with `pd.to_datetime()`.

## Defensive Coding

- Always check `df.empty` before accessing rows
- Never use `.iloc[N]` without verifying `len(df) > N`
- Use single-line f-strings (triple-quoted f-strings break JSON encoding)

## Sleep Metrics Structure

Apple HealthKit sleep stages — each stored as a separate `feature_type` row, value in **seconds**:

| feature_type | Meaning | Notes |
|---|---|---|
| `sleep_in_bed` | Total time in bed | Includes awake time |
| `sleep_asleep` | Asleep (unspecified stage) | Generic "asleep" when detailed stages are unavailable. **NOT total sleep** — it is one stage among many. |
| `sleep_core` | Core (light) sleep | Stage N1+N2 |
| `sleep_deep` | Deep sleep | Stage N3 (slow-wave) |
| `sleep_rem` | REM sleep | Dreaming stage |
| `sleep_awake` | Awake during sleep session | Time spent awake after initially falling asleep |

**Total sleep** = `sleep_asleep + sleep_core + sleep_deep + sleep_rem` (sum all asleep stages).
Do NOT use `sleep_asleep` alone as total — it is usually a small fraction.

## Sleep Session Detection (mandatory before any sleep analysis)

Sleep timestamps are stored as **endDate** — the moment each stage segment ended, not when it started. An overnight sleep crosses midnight, so a single night's sleep produces rows whose timestamps span both the previous day and the current day.

**The data contains BOTH night sleep and naps, interleaved in time order.** If you aggregate sleep rows blindly, a nap will be mixed into "last night" and an overnight session will be split across two days. You must segment the raw sleep rows into sessions before doing any sleep analysis.

The segmentation is a two-step principle, not a fixed recipe:

1. **Segment into sessions.** Sort sleep rows by timestamp and open a new session whenever the gap between two consecutive rows is large enough that they cannot plausibly belong to the same sleep — stages within one sleep follow each other by minutes, so a multi-hour gap is almost always a session boundary.

2. **Classify each session as night vs nap.** For each session, estimate its approximate *start* time (the first row's timestamp minus that row's own duration, since timestamps are end-dates). A session starting in the evening or very early morning is a night sleep; anything else is a nap.

Then use the right session for the question asked: "last night" refers to the most recent night session only; a daily summary should report night and nap separately rather than lumped together; a total-sleep figure should sum all sessions but label night vs nap so the user can tell them apart.
