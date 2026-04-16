---
description: "Run a CUSUM change-point detector on resting heart rate or HRV to find the exact day a personal baseline shifted, and translate the finding into a dated, plain-language insight about what changed"
---
# Baseline change-point detection (CUSUM)

Use this when the user asks "**when** did my baseline change?", "did
something happen on a specific day?", or when you've already noticed a
trend and want to localise the inflection point. Common real-world
triggers: a recent illness, starting or stopping a medication, a
lifestyle change (new job, diet change, moved house), seasonal
transitions, or simply "I feel different lately and don't know why".

## Method — CUSUM is fundamentally different from threshold checks

A threshold check answers "is today abnormal vs baseline?". CUSUM answers
"**when did the baseline itself shift?**" — it reports a specific date, not a
delta. This is the right tool when you suspect a step change (illness
onset, starting a new medication, a major lifestyle shift, recovering
from a viral infection) rather than slow drift.

The CUSUM (Cumulative Sum) statistic, originally Page (1954), accumulates
how far each observation is from a target value `μ₀`:

```
S₀  = 0
S_i = max(0, S_{i-1} + (xᵢ - μ₀ - k))     ← upward CUSUM
S⁻_i = max(0, S⁻_{i-1} + (μ₀ - xᵢ - k))   ← downward CUSUM
```

where `k` is a slack constant (typically 0.5σ — half the smallest shift we
care about). An alarm fires when `S_i` exceeds a threshold `h` (typically
4–5σ). The change point is the most recent index where `S_i` was zero before
the alarm.

Reference: Page, E. S. (1954). *Continuous inspection schemes*. Biometrika
41(1/2): 100–115. NIST/SEMATECH e-Handbook of Statistical Methods, §6.3.2.

## Step 1 — Pull 90 days of the metric of interest

Default to `resting_heart_rate`; the user may also ask about `heart_rate_variability`
or `walking_heart_rate_avg`. Always 90 days — CUSUM needs a stable
pre-change baseline to anchor against.

```sql
SELECT timestamp, value
FROM samples
WHERE feature_type = 'resting_heart_rate'
  AND timestamp >= datetime('now', '-90 days')
ORDER BY timestamp;
```

Precondition: at least 45 days with data. Below that the baseline isn't
stable enough — say so and stop.

## Step 2 — Build a clean daily series

```python
import numpy as np
import pandas as pd

df = pd.DataFrame(rows)
df['timestamp'] = pd.to_datetime(df['timestamp'])
df['day'] = df['timestamp'].dt.date

daily = df.groupby('day')['value'].mean().sort_index()
daily.index = pd.to_datetime(daily.index)
daily = daily.asfreq('D').interpolate('linear', limit=3)  # fill ≤3-day gaps
daily = daily.dropna()
```

## Step 3 — Anchor the reference and run CUSUM

Use the **first 30 days** as the reference window — this is the "before"
period the algorithm compares against.

```python
ref = daily.iloc[:30]
mu0 = ref.median()           # robust centre
sigma = (ref - mu0).abs().median() * 1.4826  # MAD-based scale
if sigma == 0 or np.isnan(sigma):
    raise RuntimeError("Reference window has zero variability — data is bad")

k = 0.5 * sigma   # detect shifts of ≥1σ
h = 5.0 * sigma   # alarm threshold (~ false-alarm every ~370 obs in-control)

x = daily.iloc[30:].to_numpy()
dates = daily.index[30:]

S_up = np.zeros(len(x) + 1)
S_dn = np.zeros(len(x) + 1)
alarms = []  # list of (date, direction, change_start_date)

for i, xi in enumerate(x, start=1):
    S_up[i] = max(0, S_up[i-1] + (xi - mu0 - k))
    S_dn[i] = max(0, S_dn[i-1] + (mu0 - xi - k))
    if S_up[i] > h:
        # Walk back to the most recent zero — that's the change point
        j = i - 1
        while j > 0 and S_up[j] > 0:
            j -= 1
        alarms.append((dates[i-1], 'up', dates[max(0, j)]))
        S_up[i] = 0  # reset after alarm
    elif S_dn[i] > h:
        j = i - 1
        while j > 0 and S_dn[j] > 0:
            j -= 1
        alarms.append((dates[i-1], 'down', dates[max(0, j)]))
        S_dn[i] = 0
```

## Step 4 — Quantify the shift

For each detected change point, compute the shift in the original units:

```python
for alarm_date, direction, change_start in alarms:
    pre  = daily.loc[:change_start].tail(14).mean()
    post = daily.loc[change_start:].head(14).mean()
    shift_bpm = round(post - pre, 1)
    print(f"{change_start.date()}: {direction}-shift, "
          f"{pre:.1f} → {post:.1f} bpm (Δ={shift_bpm:+})")
```

## Step 5 — Decision rules

- **No alarms** → baseline is stable for the last ~60 days. Report it
  positively: "no detectable shifts in 60 days."
- **Single alarm, |Δ| < 2 bpm** → minor shift, mention but don't alarm
  the user.
- **Single alarm, |Δ| ≥ 2 bpm** → clinically meaningful shift. Report the
  date and the magnitude. Common explanations to suggest the user
  consider: recent illness (cold/flu), starting/stopping caffeine or
  alcohol, a new medication, dehydration episode, sleep schedule change,
  or seasonal shift.
- **Multiple alarms** → unstable baseline / something cyclic. Report all
  but don't try to over-interpret.

## Step 6 — From numbers to insight

The CUSUM statistic is scaffolding. The insight the user actually wants is
a one-sentence answer: **"did your baseline shift, and if so, when and by
how much?"** Lead the report with that sentence; the date, magnitude, and
method come after as supporting detail. A number-first report buries the
finding.

A baseline shift is interesting because it lets the user match a date
against their own memory of what changed — *that match* is the insight,
not the CUSUM value. Enumerate plausible categories of cause (illness,
medication, lifestyle change, seasonal transition) as a prompt for the
user's recall; never pick one for them and never invent a cause.

No alarm firing is also an insight, not an absence of one. Say plainly
that the baseline has been stable over the window as a positive
finding — stability is news worth giving.
