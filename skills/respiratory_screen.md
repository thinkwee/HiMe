---
description: "Screen overnight respiratory health using extreme-value statistics on nightly SpO2 minima, respiratory rate distribution, and breath disturbance counts, and turn the findings into a clear insight the user could take to a clinician"
---
# Respiratory screen (overnight)

Use this when the user asks "is my breathing OK at night?", "am I
getting enough oxygen when I sleep?", "should I worry about sleep apnea?",
or after symptoms like daytime fatigue / morning headaches / unrefreshing
sleep that the sleep duration alone doesn't explain.

> **Important framing**: this is a *screen*, not a diagnosis. Sleep-disordered
> breathing (apnea, hypoventilation) can only be diagnosed by polysomnography.
> The point of this skill is to give the user data they can take to a
> clinician — never to label them with a condition.

## Method — extreme value statistics, not means

For respiratory monitoring, **the mean is the wrong summary**. A user can
have a perfectly normal mean overnight SpO2 of 96% and still drop to 82%
during apneic events. What matters clinically is the **distribution of
nightly minima**: the lowest SpO2 reached on each night, then statistics
over those minima across many nights.

This is the **block minima** approach from extreme value theory: take
non-overlapping blocks (here, nights), compute the minimum of each block,
and analyse that secondary distribution.

We combine three signals:

1. **SpO2 nightly minima** (`blood_oxygen` feature) — block-minima series
2. **Respiratory rate distribution** (`respiratory_rate`) — clinical range check
3. **Sleeping breathing disturbances** (`sleeping_breathing_disturbances`)
   — Apple's per-night disturbance count, when available

Clinical thresholds used:

| Metric | Normal | Borderline | Concerning |
|---|---|---|---|
| Mean nightly SpO2 minimum | ≥ 92% | 88–92% | < 88% |
| % of nights with min < 90% | < 10% | 10–25% | > 25% |
| Resting respiratory rate | 12–20 br/min | 20–24 or 10–12 | > 24 or < 10 |
| Disturbances per night (Apple) | < 5 | 5–15 | > 15 |

References:
- Berry, R. B. et al. (2012). *Rules for scoring respiratory events in
  sleep* (AASM scoring manual). Definition: a desaturation event = ≥3%
  drop from baseline lasting ≥10 sec.
- Fleming, S. et al. (2011). *Normal ranges of heart rate and respiratory
  rate in children from birth to 18 years*. Lancet 377: 1011–1018.
- Reiterer, F. et al. (2018). *Pulse oximetry in obstructive sleep apnea
  screening*. Sleep Medicine Reviews 39: 28–42.

## Step 1 — Pull 30 nights of overnight respiratory data

```sql
SELECT timestamp, feature_type, value
FROM samples
WHERE feature_type IN (
    'blood_oxygen',
    'respiratory_rate',
    'sleeping_breathing_disturbances',
    'sleep_asleep'
  )
  AND timestamp >= datetime('now', '-30 days')
ORDER BY timestamp;
```

Precondition: at least **10 nights** with `blood_oxygen` measurements
recorded during sleep windows. Apple Watch only spot-samples SpO2 (not
continuous), so on a typical night you may have 5–15 readings — that's
fine, the minimum across that handful is still informative.

## Step 2 — Restrict SpO2 readings to actual sleep windows

We don't want daytime SpO2 readings polluting the nightly minima. Use
the `sleep_asleep` records to define sleep intervals and filter SpO2 to
samples that fall inside them.

```python
import numpy as np
import pandas as pd

df = pd.DataFrame(rows)
df['ts'] = pd.to_datetime(df['timestamp'])

sleep = df[df['feature_type'] == 'sleep_asleep'].copy()
sleep['end'] = sleep['ts']
sleep['start'] = sleep['end'] - pd.to_timedelta(sleep['value'], unit='s')

spo2 = df[df['feature_type'] == 'blood_oxygen'].copy()

# Mark each SpO2 sample as overnight if it falls inside any sleep interval
def in_sleep(t):
    hits = sleep[(sleep['start'] <= t) & (sleep['end'] >= t)]
    return not hits.empty

spo2['overnight'] = spo2['ts'].apply(in_sleep)
overnight = spo2[spo2['overnight']].copy()
```

If the watch doesn't write `sleep_asleep`, fall back to the wall-clock
window 23:00–07:00 — coarser but still meaningful.

## Step 3 — Block minima per night

```python
overnight['night'] = (overnight['ts'] - pd.Timedelta(hours=12)).dt.date
nightly_min = overnight.groupby('night')['value'].min()

n_nights = len(nightly_min)
mean_min = nightly_min.mean()
worst    = nightly_min.min()
worst_night = nightly_min.idxmin()
pct_below_90 = (nightly_min < 90).mean() * 100
pct_below_88 = (nightly_min < 88).mean() * 100

print(f"Nights analysed: {n_nights}")
print(f"Mean nightly min SpO2: {mean_min:.1f}%")
print(f"Worst night: {worst}% on {worst_night}")
print(f"Nights with min <90%: {pct_below_90:.0f}%")
print(f"Nights with min <88%: {pct_below_88:.0f}%")
```

## Step 4 — Respiratory rate distribution

```python
rr = df[df['feature_type'] == 'respiratory_rate']['value']
if len(rr) > 0:
    rr_median = rr.median()
    rr_p10, rr_p90 = rr.quantile([0.10, 0.90])
    rr_high_pct = (rr > 22).mean() * 100
    rr_low_pct  = (rr < 10).mean() * 100
    print(f"Respiratory rate median: {rr_median:.1f} (P10–P90: {rr_p10:.1f}–{rr_p90:.1f})")
```

A persistently elevated resting respiratory rate (>22 br/min for days
in a row) is a known early-warning signal for systemic illness — Apple
Watch / Fitbit COVID-19 studies replicated this in 2020.

## Step 5 — Breathing disturbances (Apple-specific)

```python
disturb = df[df['feature_type'] == 'sleeping_breathing_disturbances']
if not disturb.empty:
    disturb['night'] = (pd.to_datetime(disturb['timestamp']) - pd.Timedelta(hours=12)).dt.date
    nightly_dist = disturb.groupby('night')['value'].sum()
    median_dist = nightly_dist.median()
    high_dist_pct = (nightly_dist > 15).mean() * 100
```

This metric is only present on Apple Watch Series 9+ (watchOS 11+) so
gracefully skip it if the column has zero rows.

## Step 6 — Combined assessment

Categorise the user into one of three buckets based on the worst-trending
indicator (not the average — extreme value theory says the tail matters):

- **Reassuring** — mean nightly min ≥ 92%, < 10% of nights below 90%,
  RR median 12–20, disturbances < 5 (or unavailable)
- **Watch list** — any single concerning indicator OR two borderline
  ones. Suggest tracking for another 4 weeks before action.
- **See a clinician** — mean nightly min < 88% **OR** > 25% of nights
  below 90% **OR** disturbances > 15 per night sustained. Strongly suggest
  the user discuss with a primary care doctor and consider a sleep study.
  Be explicit: the data is suggestive, not diagnostic.

## Step 7 — From numbers to insight

Lead with the bucket as a single insight sentence — **"your overnight
breathing looks reassuring / warrants watching / warrants seeing a
clinician"** — then support with numbers. Numbers-first reports bury
the finding.

The insight gets sharper when it connects to why the user is asking.
A concerning tail often lines up with the complaint that brought them
here (daytime fatigue, morning headaches, unrefreshing sleep); naming
that link is the insight, not the SpO2 minimum itself.

Under the lead sentence, include in order:

1. Nights analysed (transparency about confidence)
2. Mean and worst nightly SpO2 minimum, with the worst date
3. Fraction of nights below 90% / 88%
4. Respiratory rate summary and any out-of-range fraction
5. Disturbance count if available
6. A clear "this is a screen, not a diagnosis" line

For the "see a clinician" bucket, the most useful output is a summary
block the user can copy directly into a doctor's-visit note. Frame it
that way explicitly. Never recommend treatments — that's the clinician's
job; the agent's job is to turn weeks of passively-collected data into
an insight the clinician would not otherwise have.
