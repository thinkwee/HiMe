---
description: "Compute the Sleep Regularity Index (SRI) and circular standard deviation of sleep midpoint over 14 nights, and translate schedule consistency into an insight that explains how the user feels when duration alone can't"
---
# Sleep Regularity Index (SRI) — schedule consistency, not duration

Use this when the user asks "is my sleep schedule consistent?", "do I
have social jet lag?", "I sleep enough but feel tired — why?", or after a
weekend / travel period.

## Method — wrap-around / circular statistics

This skill is about **timing**, not duration or quality. Two people can
both sleep 7.5 hours yet have completely different metabolic outcomes if
one goes to bed at the same time every night and the other shifts ±3
hours week to week. The Sleep Regularity Index quantifies that
specifically:

> SRI = the percentage of minute-pairs (separated by exactly 24h) in
> which the user is in the same sleep state (asleep vs awake).

A perfectly identical schedule scores 100; a schedule that wakes / sleeps
at random scores ~0. Healthy adults typically score in the **70s–80s**;
shift workers and chronic social-jet-lag scores fall into the 40s–60s.

Reference: Phillips, A. J. K. et al. (2017). *Irregular sleep/wake
patterns are associated with poorer academic performance and delayed
circadian and sleep/wake timing*. Scientific Reports 7: 3216.

We complement SRI with the **circular standard deviation of the sleep
midpoint** — because midpoint is a clock time, ordinary stddev breaks
down at midnight (23:00 and 01:00 are 2h apart, not 22h). We use
`scipy.stats.circstd` with a 24h period.

## Step 1 — Pull 14 nights of sleep stage records

```sql
SELECT timestamp, feature_type, value
FROM samples
WHERE feature_type IN ('sleep_asleep', 'sleep_in_bed', 'sleep_core',
                       'sleep_deep', 'sleep_rem', 'sleep_awake')
  AND timestamp >= datetime('now', '-15 days')
ORDER BY timestamp;
```

Apple HealthKit stores sleep as duration records: each row's `timestamp`
is the **end** of the period and `value` is the duration in seconds. To
reconstruct the actual sleep window we'll subtract the duration to get
the start.

Precondition: at least 10 nights with sleep data in the window. Below
that the SRI is too noisy — say so and stop.

## Step 2 — Reconstruct minute-by-minute sleep state for each calendar day

```python
import numpy as np
import pandas as pd
from scipy.stats import circstd

df = pd.DataFrame(rows)
df['end'] = pd.to_datetime(df['timestamp'])
df['start'] = df['end'] - pd.to_timedelta(df['value'], unit='s')

# Asleep states only (exclude in_bed, awake)
asleep_types = {'sleep_asleep', 'sleep_core', 'sleep_deep', 'sleep_rem'}
df['is_asleep'] = df['feature_type'].isin(asleep_types)

# Build a per-minute boolean grid for the entire 14-day window
window_start = df['start'].min().normalize()
window_end   = df['end'].max().normalize() + pd.Timedelta(days=1)
minutes = pd.date_range(window_start, window_end, freq='1min', inclusive='left')
state = pd.Series(False, index=minutes)

for _, row in df[df['is_asleep']].iterrows():
    s = max(row['start'], window_start)
    e = min(row['end'],   window_end)
    if s < e:
        state.loc[s:e] = True
```

## Step 3 — Compute SRI

```python
# For every minute, compare against the same minute one day later
shifted = state.shift(-24 * 60)
valid = shifted.notna()
agree = (state[valid] == shifted[valid]).sum()
total = valid.sum()
sri = round(100 * agree / total, 1) if total else None
```

This is the exact Phillips et al. 2017 definition: percent of paired
minutes that match.

## Step 4 — Compute sleep midpoints and circular SD

```python
# Group asleep minutes by "night" (using the 12h-shift trick)
state_idx = state[state].index  # only asleep minutes
nights = (state_idx - pd.Timedelta(hours=12)).date

midpoints_h = []
for night, idx in pd.Series(state_idx).groupby(nights):
    if len(idx) < 60:  # require at least 1h of sleep
        continue
    # Express each minute as fractional hour-of-day
    hours = idx.dt.hour + idx.dt.minute / 60
    # Median is robust; sleep_asleep can have brief gaps
    midpoint = float(np.median(hours))
    midpoints_h.append(midpoint)

# Circular SD on a 24h scale
if len(midpoints_h) >= 5:
    radians = np.array(midpoints_h) * 2 * np.pi / 24
    csd_rad = circstd(radians, high=2*np.pi, low=0)
    csd_min = csd_rad * (24 * 60) / (2 * np.pi)  # convert back to minutes
else:
    csd_min = None
```

## Step 5 — Social jet lag (weekday vs weekend midpoint shift)

If the data spans at least one weekend, compute the simple
"social jet lag":

```python
mp_series = pd.Series(midpoints_h, index=pd.to_datetime(sorted(set(nights))))
mp_series = mp_series.sort_index()
weekday_med = mp_series[mp_series.index.weekday < 5].median()
weekend_med = mp_series[mp_series.index.weekday >= 5].median()
sjl_h = abs(weekend_med - weekday_med) if pd.notna(weekday_med) and pd.notna(weekend_med) else None
```

A `sjl_h ≥ 1.0` is the threshold from Wittmann et al. (2006) for
clinically meaningful social jet lag.

## Step 6 — Decision bands

| Metric | Healthy | Borderline | Concerning |
|---|---|---|---|
| **SRI** | ≥ 75 | 60–74 | < 60 |
| **Midpoint circular SD** | < 30 min | 30–60 min | > 60 min |
| **Social jet lag** | < 30 min | 30–59 min | ≥ 60 min |

A user can have great total sleep duration **and** poor regularity at
the same time. That's the whole point of this skill — it surfaces a
problem the duration-based skills miss.

## Step 7 — From numbers to insight

The insight is **"your schedule is / isn't consistent, and here's what
that means for how you feel"** — not the SRI number. Many users sleep
enough hours yet feel tired; this skill exists to explain that
mismatch, so the report should explicitly make that connection when
the data supports it.

Recognisable insight shapes:

- Healthy duration + poor SRI → the likely reason the user feels tired
  despite adequate sleep is *when*, not *how much*. Name that
  connection; it is the whole value of the skill.
- Good SRI → genuinely reassuring news. Irregular schedules attract a
  lot of unjustified concern and a clean SRI is worth giving plainly.
- High social jet lag specifically → point at the weekday/weekend
  midpoint gap as the lever, not at "sleep more".

Lead with that one-sentence insight, then support with SRI, midpoint
as a clock time, circular SD in minutes, and social jet lag if
computable. Always name the single most outlier night by date so the
user can self-explain it — their own recall is the missing information
the agent can't produce.

## Optional: cross-check with HRV

Schedule irregularity tends to depress HRV. If `heart_rate_variability`
data exists, query its 14-day mean and report whether it co-varies with
the irregular nights — but only as an observation, not causal claim.
