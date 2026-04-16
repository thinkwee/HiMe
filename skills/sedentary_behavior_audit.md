---
description: "Audit sedentary behavior over 14 days using bout segmentation on hourly stand data — daily sitting time, longest unbroken sit, breaks per hour — and surface the single most actionable insight about bout structure rather than total sitting time"
---
# Sedentary behavior audit

Use this when the user asks "am I sitting too much?", "do I need to
move more during the day?", or after a stretch of long desk days /
travel / convalescence. This is a **general health** question — long
uninterrupted sitting is independently associated with cardiometabolic
risk regardless of how much exercise you do at other times.

## Method — bout segmentation, not daily totals

A daily sitting **total** is misleading: 8 hours of sitting broken by
hourly walks is not the same physiological signal as 8 hours straight.
What matters is **bout structure**:

- **Total sedentary time** — hours/day with no standing or stepping activity
- **Longest unbroken sedentary bout** — the worst contiguous run
- **Breaks per sedentary hour** — how often the user interrupts sitting

The technique is **run-length encoding** on a per-hour binary series of
"active vs sedentary", then summary statistics over the resulting bouts.

References:
- WHO (2020). *Guidelines on physical activity and sedentary behaviour*.
  Recommendation: limit sedentary time, replace with activity of any
  intensity.
- Healy, G. N. et al. (2008). *Breaks in sedentary time: beneficial
  associations with metabolic risk*. Diabetes Care 31(4): 661–666.
- Dunstan, D. W. et al. (2012). *Breaking up prolonged sitting reduces
  postprandial glucose and insulin responses*. Diabetes Care 35(5): 976–983.

## Step 1 — Pull 14 days of stand-hour records

Apple HealthKit's `stand_hour` is a per-hour binary indicator: a row
exists if the user stood and moved for at least 1 minute in that hour.
This is the cleanest signal for bout analysis because it's already
hour-bucketed.

```sql
SELECT timestamp
FROM samples
WHERE feature_type = 'stand_hour'
  AND timestamp >= datetime('now', '-14 days')
ORDER BY timestamp;
```

Also pull `steps` so we can sanity-check the stand-hour signal (some
HealthKit installs don't write stand_hour reliably):

```sql
SELECT timestamp, value
FROM samples
WHERE feature_type = 'steps'
  AND timestamp >= datetime('now', '-14 days')
ORDER BY timestamp;
```

Precondition: at least 7 days with **any** `stand_hour` records. If
zero records exist, the data source isn't recording the metric — fall
back to a coarser approach: bucket steps by hour, treat any hour with
≥100 steps as "active".

## Step 2 — Build per-day binary hour series

```python
import numpy as np
import pandas as pd

stand = pd.DataFrame(stand_rows)
stand['ts'] = pd.to_datetime(stand['timestamp'])
stand['day'] = stand['ts'].dt.date
stand['hour'] = stand['ts'].dt.hour
stand['active'] = 1

# Pivot to a 14×24 matrix: row = day, column = hour, value = 1 if active
days = sorted(stand['day'].unique())
grid = pd.DataFrame(0, index=days, columns=range(24))
for d, h in zip(stand['day'], stand['hour']):
    grid.loc[d, h] = 1
```

Restrict the analysis to **waking hours**. Apple's stand goal already
excludes obvious sleep hours, but to be safe drop hours where no row
exists across **any** day in the window — those are almost certainly
asleep hours.

```python
# Heuristic: if a column (hour-of-day) has zero stand activity across
# the entire 14 days, treat it as sleep, not as 14 sedentary hours.
waking_hours = [h for h in range(24) if grid[h].sum() > 0]
if len(waking_hours) < 8:
    # Fall back to a fixed waking window 8:00–22:00
    waking_hours = list(range(8, 23))
grid = grid[waking_hours]
```

## Step 3 — Run-length encode each day, derive bout statistics

```python
def bouts(row):
    """Return list of (state, length_hours) for one day's hour series."""
    out = []
    cur_state = None
    cur_len = 0
    for v in row.tolist() + [None]:  # sentinel
        if v != cur_state:
            if cur_state is not None:
                out.append((cur_state, cur_len))
            cur_state = v
            cur_len = 1
        else:
            cur_len += 1
    return [b for b in out if b[0] is not None]

records = []
for d, row in grid.iterrows():
    b = bouts(row)
    sedentary_total = sum(L for s, L in b if s == 0)
    longest_sed     = max((L for s, L in b if s == 0), default=0)
    sed_bouts       = [L for s, L in b if s == 0]
    n_breaks        = sum(1 for s, L in b if s == 1)  # active runs interrupting sitting
    records.append({
        'day': d,
        'sedentary_h': sedentary_total,
        'longest_sed_h': longest_sed,
        'breaks': n_breaks,
        'waking_h': len(row),
    })

summary = pd.DataFrame(records)
print(summary)

mean_sed     = summary['sedentary_h'].mean()
mean_longest = summary['longest_sed_h'].mean()
worst_day    = summary.loc[summary['longest_sed_h'].idxmax()]
break_rate   = summary['breaks'].sum() / summary['sedentary_h'].sum() if summary['sedentary_h'].sum() else 0
```

## Step 4 — Decision bands (WHO + literature thresholds)

| Metric | Healthy | Borderline | Concerning |
|---|---|---|---|
| **Mean daily sedentary time** | ≤ 6 h | 6 – 9 h | > 9 h |
| **Mean longest unbroken bout** | ≤ 2 h | 2 – 4 h | > 4 h |
| **Breaks per sedentary hour** | ≥ 0.5 | 0.25 – 0.5 | < 0.25 |

The most actionable single number is the **longest unbroken bout**:
even if the daily total is OK, a 5-hour unbroken sit is independently
problematic for postprandial glucose and lipid metabolism.

## Step 5 — From numbers to insight

The insight is rarely "you sit X hours" — for modern desk workers the
daily total is almost always somewhere between bad and worse, and
repeating that back is not useful. The insight is **which part of the
bout structure is the problem, and when it happens**.

Lead with a one-sentence answer of the shape "your total is in band X,
but your bout structure shows Y" — where Y is the most actionable
finding across the three metrics. Common insight shapes:

- Total is fine but one bout is very long → the long bout is the
  problem, not the total. Say so explicitly.
- Total is high but bouts are broken up → much better than the total
  alone suggests; reassure rather than scold.
- Breaks per hour are low specifically within a contiguous window of
  the day → localise the problem to that window by clock time.

Name the worst day by date so the user can self-explain it — the
user's recall is the missing context the agent can't produce. Suggest
interventions only on the specific pattern the data shows, never as
boilerplate. Don't recommend "do more exercise" — that's a different
lever; this skill is about *interrupting sitting*, which has
independent benefits regardless of workout volume.
