# create_page: HimeUI Component Library

When using `create_page`, include these two lines in `<head>`:
```html
<link rel="stylesheet" href="/api/personalised-pages/_shared/hime-ui.css">
<script src="/api/personalised-pages/_shared/hime-ui.js"></script>
```

## Backend helpers (auto-injected)
- `query_health(feature_types, days=7, agg=None, agg_interval='day')` → list[dict]
  - Without `agg`: rows are `{timestamp, feature_type, value}`.
  - With `agg` (`'avg'|'sum'|'min'|'max'|'count'`): rows are `{period, feature_type, value}` — the time key is **`period`**, NOT `timestamp`.
- `health_stats(feature_type, days=7)` → {count, mean, min, max, std, latest_value, latest_time}
- `query_memory(sql, params)` → list[dict]
- `write_memory(sql, params)` → int
- `ensure_table(name, columns_dict)` — create custom table. Two constraints:
  1. An `id INTEGER PRIMARY KEY AUTOINCREMENT` column is added automatically. Do **not** include `id` in `columns_dict`.
  2. Column types must be simple SQL types (`TEXT`, `REAL`, `INTEGER`, `BLOB`, `NUMERIC`, `BOOLEAN`, `DATE`, `DATETIME`, `TIMESTAMP`) plus optional `NOT NULL` / `UNIQUE` / `DEFAULT <literal>`. `DEFAULT` only accepts a literal — function calls like `DEFAULT (strftime(...))` or `DEFAULT CURRENT_TIMESTAMP` are rejected. For timestamps, write them in your `INSERT` statement instead (e.g. `datetime.now().isoformat()`).
- `parse_request_body(request)` → dict
- `get_query_params(request)` → dict

## Valid `feature_type` values

These are the only values that appear in the `samples` table. Using any other name (e.g. `sleep_hours`, `hrv`, `active_calories`, `step_count`) returns zero rows — not an error.

`active_energy`, `audio_exposure_event`, `blood_oxygen`, `distance`, `exercise_time`, `flights_climbed`, `heart_rate`, `respiratory_rate`, `resting_energy`, `resting_heart_rate`, `running_ground_contact`, `running_power`, `running_stride_length`, `running_vertical_oscillation`, `sleep_awake`, `sleep_core`, `sleep_deep`, `sleep_rem`, `sleeping_wrist_temp`, `stair_ascent_speed`, `stair_descent_speed`, `stand_time`, `steps`, `time_in_daylight`, `walking_asymmetry`, `walking_double_support`, `walking_heart_rate_avg`, `walking_speed`, `walking_step_length`, `water`

Common derivations:
- "sleep hours" → sum of `sleep_core + sleep_deep + sleep_rem` (values are seconds).
- "HRV" is not in this dataset; use `resting_heart_rate` or `walking_heart_rate_avg` instead, or remove the metric.
- "steps" → `steps` (not `step_count`). "active calories" → `active_energy` (not `active_calories`).

## Components

**1. MetricGrid** — health stat cards (2-column grid)
```js
HimeUI.MetricGrid(sel, [{label, value, unit?, icon?, color?, trend?, wide?}])
// Colors: blue/green/red/orange/purple/pink/teal/indigo
// wide: true → card spans both columns (use for hero metrics like "best day", "weekly summary").
//   Prefer this over building a custom grid layout — keeps styling consistent across pages.
```

**2. DetailList** — iOS grouped list with push navigation
```js
HimeUI.DetailList(sel, {items:[{icon?,iconColor?,title,subtitle?,badge?,badgeColor?,detail?,right?}], sectionTitle?, onAction?})
// detail: HTML string or array [{type:'text',text:''}, {type:'header',text:''}, {type:'steps',items:[]}, {type:'metrics',items:[{label,value,unit}]}, {type:'button',label:'',color:'',id:''}, {type:'divider'}, {type:'alert',text:'',color:''}]
```

**3. ChartView** — chart with period segmented control
```js
HimeUI.ChartView(sel, {pageId, type?'line', periods?['1W','1M','3M'], defaultPeriod?, dataMap?})
// ChartView fetches /data?period=X and by default reads TOP-LEVEL res.labels & res.datasets.
// If the same /data endpoint also serves MetricGrid/DetailList/etc. (multi-component page),
// you MUST either (a) branch on params['period'] in route_handler and return different shapes,
// or (b) nest chart data (e.g. {chart:{labels,datasets}}) and pass dataMap:r=>r.chart here.
// Returning {chart:{...}} WITHOUT a dataMap → empty chart. This is the most common create_page bug.
```

**4. InputForm** — iOS grouped form (POSTs to backend)
```js
HimeUI.InputForm(sel, {pageId, fields:[{name,type,label,placeholder?,value?,min?,max?,options?,display?}], submitLabel?, onSuccess?, extraData?})
// Field types: text, number, slider, select, toggle, textarea, date, time
```

**5. Tracker** — combined form + scrollable history
```js
HimeUI.Tracker(sel, {pageId, fields, submitLabel?, historyKey?'history', historyRender:item=>{icon?,iconColor?,title,subtitle?,right?}, emptyText?})
```

**6. Section** — section header + body container (use to avoid hand-writing eyebrow/card markup on every dashboard)
```js
const s = HimeUI.Section(sel, {title, icon?, badge?, badgeColor?, cardTitle?})
// Renders an iOS-style section header into `sel` and creates a body div underneath.
// Returns { bodySelector, bodyEl } — pass bodySelector to whichever component fills the section:
//   const s = HimeUI.Section('#summary', {title:'TODAY', badge:'live'});
//   HimeUI.MetricGrid(s.bodySelector, items);
// cardTitle (any string, including ''): wraps the body in a .hime-card with that title.
//   Omit `cardTitle` entirely for a plain (un-carded) body.
```

**7. HimeUI.drawChart** — minimal multi-series chart (use this for static multi-chart dashboards; use `ChartView` if you want a single chart with a period switcher)
```js
HimeUI.drawChart(sel, {
  type: 'line'|'bar'|'area',          // chart-level default; per-dataset `type` overrides it
  labels: [...],
  datasets: [{label, data, color?, type?, axis?}]
})
// sel: a selector string or a <div> ELEMENT — NOT a <canvas>, NOT a 2D context.
//   The function injects its own <canvas> inside the host div.
// color: palette name (blue/green/red/orange/purple/pink/teal/indigo/yellow) OR any CSS color string.
// dataset.type:  'line'|'bar'|'area' — override the chart-level type for one series,
//                  enabling mixed bar+line in a single chart.
// dataset.axis:  'left' (default) | 'right' — when ANY dataset uses 'right', a second
//                  Y axis is drawn on the right with its own auto-scaled range. Right-axis
//                  series get an "R" tag in the legend.
//
// THIS IS NOT CHART.JS. The following Chart.js fields are silently ignored (the lib will
// log a one-time console warning if it sees them):
//   - top-level `options` (no scales/plugins/legend/interaction config)
//   - per-dataset Chart.js fields: backgroundColor, borderColor, borderWidth, pointRadius,
//     tension, fill, yAxisID, borderDash, pointBackgroundColor, …
// Stacking is not supported. Do NOT add a Chart.js CDN — the page must stay zero-dep.
//
// Common bug: passing `getContext('2d')` (or the <canvas> element) as `sel`. The chart
// will fail to render and log an actionable error. Always pass a <div> selector.
```

**Other utilities**: `HimeUI.showSheet(html, {title?})`, `HimeUI.hideSheet()`, `HimeUI.toast(msg, 'success'|'error')`, `HimeUI.progressRing(value,max,{size?,color?,label?})`, `HimeUI.badge(text,color)`, `HimeUI.progress(value,max,color)`, `HimeUI.table(headers,rows)`, `HimeUI.formatTime(ts,'short'|'time'|'date'|'relative')`, `HimeUI.formatNum(n,decimals)`

> Deprecated: `HimeUI.chart` still works as an alias for `drawChart` but emits a console warning. The `chart` name collided with Chart.js and caused authors to write Chart.js-style specs. Always use `drawChart` in new pages.

## Page structure

Every page is one `index.html` plus one `route.py`. They communicate through a single URL: `/api/personalised-pages/<page_id>/data`. The `page_id` you pass to `create_page` is what both the HTML script and the registry row reference — keep it consistent throughout.

The HTML must load the two shared `<head>` resources above, wrap all content in one root `<div class="hime-page">`, include a `<div class="hime-header">` with the page title, and provide a container div for each component that will be mounted. The minimal skeleton is:

```html
<div class="hime-page"><div class="hime-header"><h1 class="hime-large-title">…</h1></div>…component containers…</div>
```

## Backend wiring: `route.py`

Define a single function `route_handler(request)`. The framework auto-injects the helpers listed under "Backend helpers" above, so you never import anything yourself. The function must return a plain dict; the framework serialises it as JSON.

Two orthogonal kinds of branching shape the handler:

- **Method branching.** If the page accepts form submissions, check `request.method`. On `POST`, parse the body with `parse_request_body(request)`, persist it with `write_memory(sql, params)`, and return a status dict. On `GET`, build and return the page's current state.
- **Query-parameter branching.** If the page uses a `ChartView`, read `get_query_params(request)` and branch on the `period` key. `ChartView` re-fetches the same endpoint with `?period=…` and expects a different response shape than the page's initial load — see "Response shapes" below.

For health-data reads call `query_health(feature_types, days, agg, agg_interval)` or `health_stats(feature_type, days)`. For page-owned persistent state use `query_memory` / `write_memory`, and call `ensure_table(name, columns_dict)` once at the top of the handler so the page's table is created idempotently (the same call is safe to run on every request). Do not open your own sqlite connection or bypass these helpers.

## Response shapes: the `ChartView` contract

Most components (`MetricGrid`, `DetailList`, `Tracker`, `InputForm`, and the utilities) accept any dict shape — define the fields your frontend expects and read them from the returned dict.

`ChartView` is the exception. By default it reads `labels` and `datasets` from the **top level** of the response. When the same `/data` endpoint serves both the initial page load (for other components) and `ChartView`'s period re-fetches, the two responses have incompatible shapes. You must handle this in one of two ways:

1. **Branch on `period` in the handler.** When a `period` query parameter is present, return a flat `{labels, datasets}` response for the chart. Otherwise return the richer dict the other components need on initial load.
2. **Nest the chart data and pass a `dataMap`.** Return the chart data under a key inside a larger dict, and pass `dataMap: r => r.<that_key>` when mounting `ChartView`.

Returning a nested chart response **without** a matching `dataMap` is the single most common `create_page` bug — the chart renders empty and no error is raised.

## Frontend wiring: the script block

In the page's `<script>` block, declare a page-id constant whose value matches the `page_id` you pass to `create_page`, then mount components following these rules:

- **Read-only components** (`MetricGrid`, `Section`, `DetailList`, `HimeUI.drawChart`, `progressRing`, `table`, `badge`, …) must be mounted **after** `await HimeUI.fetchData(<page_id>)` resolves — they need the backend data to render.
- **Form components** (`InputForm`, `Tracker`) are mounted **synchronously**, passing `pageId: <page_id>`. They manage their own POST round-trip against the same `route.py` and render their own initial state from its response.
- **`ChartView`** is mounted **synchronously** with `pageId: <page_id>` and optionally `periods`, `defaultPeriod`, `dataMap`. It manages its own fetch and re-fetch on period change. Do not `await fetchData` first unless another component on the same page also needs that payload.

## Correctness checklist

Before returning from `create_page`, verify:

- Both shared `<head>` resources (CSS and JS) are linked.
- The root container is `<div class="hime-page">` and contains a `.hime-header` block.
- The `page_id` referenced in the `<script>` block matches the `page_id` passed to `create_page`.
- Every component has its container div present in the HTML before its mount call.
- If `ChartView` shares `/data` with other components, either period-branching in `route_handler` or a `dataMap` prop on `ChartView` is in place.
- If using `HimeUI.drawChart`, every chart container is a `<div>` (not a `<canvas>`) and the spec contains only `{type, labels, datasets:[{label, data, color?, type?, axis?}]}` — no Chart.js `options`/`backgroundColor`/`yAxisID`/etc.
- Every `query_memory` / `write_memory` on a page-owned table is preceded by `ensure_table(...)` inside the same handler.
- `route_handler` returns a plain dict — not a string, tuple, coroutine, or response object.
