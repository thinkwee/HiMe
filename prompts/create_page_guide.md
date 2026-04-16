# create_page: HimeUI Component Library

When using `create_page`, include these two lines in `<head>`:
```html
<link rel="stylesheet" href="/api/personalised-pages/_shared/hime-ui.css">
<script src="/api/personalised-pages/_shared/hime-ui.js"></script>
```

## Backend helpers (auto-injected)
- `query_health(feature_types, days=7, agg=None, agg_interval='day')` → list[dict]
- `health_stats(feature_type, days=7)` → {count, mean, min, max, std, latest_value, latest_time}
- `query_memory(sql, params)` → list[dict]
- `write_memory(sql, params)` → int
- `ensure_table(name, columns_dict)` — create custom table
- `parse_request_body(request)` → dict
- `get_query_params(request)` → dict

## Components

**1. MetricGrid** — health stat cards (2-column grid)
```js
HimeUI.MetricGrid(sel, [{label, value, unit?, icon?, color?, trend?}])
// Colors: blue/green/red/orange/purple/pink/teal/indigo
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

**Other utilities**: `HimeUI.showSheet(html, {title?})`, `HimeUI.hideSheet()`, `HimeUI.toast(msg, 'success'|'error')`, `HimeUI.chart(sel, {type,labels,datasets})`, `HimeUI.progressRing(value,max,{size?,color?,label?})`, `HimeUI.badge(text,color)`, `HimeUI.progress(value,max,color)`, `HimeUI.table(headers,rows)`, `HimeUI.formatTime(ts,'short'|'time'|'date'|'relative')`, `HimeUI.formatNum(n,decimals)`

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

- **Read-only components** (`MetricGrid`, `DetailList`, `HimeUI.chart`, `progressRing`, `table`, `badge`, …) must be mounted **after** `await HimeUI.fetchData(<page_id>)` resolves — they need the backend data to render.
- **Form components** (`InputForm`, `Tracker`) are mounted **synchronously**, passing `pageId: <page_id>`. They manage their own POST round-trip against the same `route.py` and render their own initial state from its response.
- **`ChartView`** is mounted **synchronously** with `pageId: <page_id>` and optionally `periods`, `defaultPeriod`, `dataMap`. It manages its own fetch and re-fetch on period change. Do not `await fetchData` first unless another component on the same page also needs that payload.

## Correctness checklist

Before returning from `create_page`, verify:

- Both shared `<head>` resources (CSS and JS) are linked.
- The root container is `<div class="hime-page">` and contains a `.hime-header` block.
- The `page_id` referenced in the `<script>` block matches the `page_id` passed to `create_page`.
- Every component has its container div present in the HTML before its mount call.
- If `ChartView` shares `/data` with other components, either period-branching in `route_handler` or a `dataMap` prop on `ChartView` is in place.
- Every `query_memory` / `write_memory` on a page-owned table is preceded by `ensure_table(...)` inside the same handler.
- `route_handler` returns a plain dict — not a string, tuple, coroutine, or response object.
