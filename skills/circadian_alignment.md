---
description: "Fit a 24h cosinor model to heart rate to extract MESOR, amplitude and acrophase, and turn them into a plain-language insight about whether the user's body clock is robust and well-timed"
---
# Circadian alignment (cosinor analysis)

Use this when the user asks "is my body clock healthy?", "have I shifted to
a later chronotype?", "is my circadian rhythm flattening?", or after a
period of jet lag / shift work / poor sleep schedule.

## Method — single-component cosinor

This is **not** a window-vs-window comparison. We fit a periodic regression
model to a continuous heart-rate signal:

```
HR(t) = MESOR + Amplitude · cos(2π·t/24 + Acrophase) + ε
```

where `t` is hours-of-day. The three parameters describe the rhythm:

- **MESOR** — rhythm-adjusted mean (the midline; more robust than a simple mean)
- **Amplitude** — half the peak-to-trough swing; **larger amplitude = stronger,
  healthier rhythm**. Small amplitude (<5 bpm for HR) suggests a flattened or
  disrupted rhythm.
- **Acrophase** — clock time of the rhythm's peak. For heart rate the
  population peak sits around **14:00–17:00**; a peak shifted past 19:00
  suggests an evening / late chronotype, before 12:00 suggests morning type.

Reference: Cornelissen, G. (2014). *Cosinor-based rhythmometry*. Theoretical
Biology and Medical Modelling, 11:16.

## Step 1 — Pull 14 days of heart-rate samples

We need many samples spread across the 24h cycle, so pull every individual
heart-rate reading (not a daily aggregate):

```sql
SELECT timestamp, value
FROM samples
WHERE feature_type = 'heart_rate'
  AND timestamp >= datetime('now', '-14 days')
ORDER BY timestamp;
```

Precondition: at least 200 samples spanning at least 10 distinct days, with
samples present in at least 16 different hours-of-day. Otherwise the cosinor
fit is unreliable — say so and stop.

## Step 2 — Fit the cosinor model

```python
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

df = pd.DataFrame(rows)
df['timestamp'] = pd.to_datetime(df['timestamp'])
# t in hours-of-day, fractional
df['t'] = df['timestamp'].dt.hour + df['timestamp'].dt.minute / 60

# Resample to hourly means to down-weight oversampled hours
hourly = df.groupby([df['timestamp'].dt.date, df['t'].astype(int)])['value'].mean().reset_index()
hourly.columns = ['date', 'hour', 'value']

t = hourly['hour'].to_numpy(dtype=float)
y = hourly['value'].to_numpy(dtype=float)

# Linearised cosinor: cos(2πt/24 + φ) = cos(φ)·cos(2πt/24) - sin(φ)·sin(2πt/24)
# Fit y = M + β1·cos(2πt/24) + β2·sin(2πt/24) (linear regression)
omega = 2 * np.pi / 24
X = np.column_stack([np.ones_like(t), np.cos(omega * t), np.sin(omega * t)])
beta, *_ = np.linalg.lstsq(X, y, rcond=None)

M = beta[0]                                           # MESOR
A = float(np.hypot(beta[1], beta[2]))                 # amplitude
phi = float(np.arctan2(-beta[2], beta[1]))            # phase in rad
acrophase_h = (-phi % (2 * np.pi)) * 24 / (2 * np.pi) # acrophase in hours

# Goodness of fit
y_hat = X @ beta
ss_res = np.sum((y - y_hat) ** 2)
ss_tot = np.sum((y - y.mean()) ** 2)
r2 = 1 - ss_res / ss_tot

print(f"MESOR={M:.1f} bpm | Amplitude={A:.1f} bpm | Acrophase={acrophase_h:.1f}h | R²={r2:.2f}")
```

The linearised form (regression on `cos`, `sin`) is mathematically equivalent
to nonlinear `curve_fit` but faster and never fails to converge.

## Step 3 — Interpretation rules

- **R² < 0.10** → no detectable rhythm. The signal is dominated by activity
  noise, not by circadian biology. Don't over-interpret; report it honestly.
- **Amplitude < 5 bpm** → flattened rhythm. Common after travel, illness,
  poor sleep streak, or chronic stress. Note it as a soft warning.
- **Amplitude 5–12 bpm** → typical healthy range for resting / mixed-activity
  heart rate.
- **Amplitude > 12 bpm** → strong rhythm, generally good (assuming the user
  is sleeping well).
- **Acrophase 12:00–18:00** → typical aligned chronotype.
- **Acrophase 18:00–22:00** → evening / late chronotype. Not pathological,
  but if it shifted recently flag it.
- **Acrophase 22:00–04:00** → severely shifted (night-shift pattern). If
  unintentional, it's a meaningful finding.

## Step 4 — Optional: trend over time

If the user asks specifically about *change*, repeat the fit twice — once on
days [-14, -8] and once on [-7, -1]. Report the **acrophase shift in hours**
(circular: wrap around 24h). A shift of more than 1.5 h between consecutive
weeks is meaningful.

## Step 5 — From numbers to insight

MESOR / amplitude / acrophase / R² are the raw outputs. The insight is
one sentence that answers **"is your body clock healthy and well-timed?"**
— lead with that, then support it with the four numbers translated into
plain language (acrophase as a clock time, amplitude as "how strong the
rhythm is", MESOR as a rhythm-adjusted average).

Three recognisable insight shapes:

- **Robust and well-timed** — strong amplitude, acrophase in the expected
  afternoon window. Say so positively; a healthy rhythm is news worth
  giving, not a boring null result.
- **Flattened** — low amplitude regardless of timing. The user may feel
  chronically under-rested even with adequate sleep duration; naming
  that connection is the insight.
- **Phase-shifted** — reasonable amplitude but acrophase outside the
  typical window. If recent, frame as the body clock having drifted;
  if chronic, frame as the user's natural chronotype, not a problem.

Only suggest interventions (consistent wake time, morning light,
evening dim-down) when the data actually shows misalignment — never as
boilerplate advice attached to a healthy rhythm.
