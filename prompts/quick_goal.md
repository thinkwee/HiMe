Quick health-state check for the iOS cat avatar.

Look at the **last hour** of data ending at the latest available timestamp. Discover that timestamp yourself with `SELECT MAX(timestamp) FROM samples` and filter your queries to `timestamp > strftime('%Y-%m-%dT%H:%M:%S', <max>, '-1 hours')`.

Use **at most 5 `sql` calls** total. Keep queries focused and aggregate where possible (avg/min/max/count). The schema is already in your system prompt — no need to recall it.

When you're done analysing, call `push_report` **once** with the following parameters:

- **title**: a short descriptive title (e.g. "Quick Health Check — Relaxed")
- **content**: a detailed health summary (3–6 sentences, markdown). Include key metrics with numbers and units, notable patterns/trends/anomalies, interpretation of health state, and actionable observations. Use bold for metric names, bullet points for multiple findings.
- **im_digest**: a concise plain-text summary (1–2 sentences, under 200 characters) suitable for Telegram/Feishu notification. Include key numbers.
- **time_range_start** / **time_range_end**: the analysis time window (ISO-8601)
- **alert_level**: `critical` for sick/alert, `warning` for stressed/tired/sad, `info` for everything else
- **metadata**: `{"state": "<one of the 15 states below>", "message": "<first-person, <80 chars, with a specific number>"}`

The `metadata.state` and `metadata.message` drive the iOS cat avatar — pick carefully.

## 15 cat states

| state | meaning |
|---|---|
| energetic | high-energy active state — recent exercise, lots of movement, calorie burn |
| tired | fatigue signals — poor / insufficient sleep, low recovery, exhaustion |
| stressed | autonomic strain — sympathetic dominance without physical exertion |
| sad | metrics trending downward — declining fitness, worsening sleep, less activity |
| relaxed | calm and well-balanced — good recovery, healthy rhythms, no red flags |
| curious | something unusual or anomalous in the data — outliers, odd patterns |
| happy | metrics trending upward — improving fitness, better sleep, more activity |
| focused | disciplined consistency — steady routines, stable metrics, low variance |
| sleepy | low arousal, drowsy — minimal movement, winding down, near-rest |
| recovering | post-exertion recovery — cooling down, parasympathetic reactivating |
| sick | possible illness — abnormal resting metrics, unusual at-rest patterns |
| zen | deep calm / mindfulness — meditation present, exceptionally peaceful state |
| proud | notable achievement or PR — milestone reached, exceptional performance |
| alert | concerning signals warranting attention — clinical events, red flags |
| adventurous | active outdoor exploration — hiking, cycling, daylight exposure, distance covered |

## Evaluation principles

- **Holistic**: consider all available dimensions together; don't rely on a single metric.
- **Context**: the same metric can mean different things — elevated HR after exercise ≠ elevated HR at rest. Always check for recent activity before interpreting cardiovascular signals.
- **Recency weighting**: more recent data points carry more weight.
- **Trend over snapshot**: when there's enough data, trends (improving / declining / stable) are more informative than a single point.
- **Absence is information**: if a dimension has no data, that itself is a signal — don't guess.
- **Coherence**: metrics should tell a consistent story; if they don't (e.g. high HR but high HRV), flag it as `curious`.
- **Default to `relaxed`** when signals are mixed or weak with no dominant pattern.
