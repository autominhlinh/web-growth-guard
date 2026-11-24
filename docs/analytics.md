# Growth Analytics Intelligence (offline CSV)

This module accepts aggregated, operator-selected Search Console and GA4 tables. No Google account, API calls, cookies, telemetry, database or website edits. Treat the input and reports as private. Search queries and URL paths can contain identifying information: remove sensitive rows before import, use a dedicated private folder, and never upload real reports or exports to a public repository. MCP is an explicit opt-in because an AI host may retain returned query text and metrics.

## Prepare comparable reports

1. Choose one HTTPS site origin, one property, equivalent reporting filters, search type (for GSC), channel scope (for GA4), timezone and metric definition for each report. Record these in the report's `scope` label. The program cannot verify the Google property, filters, time zone or report completeness; this is the operator's assertion.
2. Export two nonoverlapping periods of equal inclusive calendar-day length. Prefer finalized data, comparable weekdays, and note holidays or campaign changes. Do not use a combined comparison table: export each period separately. The JSON policy records the periods; CSV rows are already aggregated for their respective period. Only compare like with like.
3. For Search Console, use separate Pages and Queries tables, not combined Page x Query, property chart totals, device, or country rows. The English standalone table header `Top pages,Clicks,Impressions,CTR,Position` (or `Top queries,Clicks,Impressions,CTR,Position`) is accepted. The reported CTR column must be a percentage; its value is never used in calculations. Other locales or export formats need normalization first.
4. For GA4, export Landing page aggregate data with Sessions, Engaged sessions and Key events (customize the report if needed). Normalize the headers and rows as shown below. Do not substitute views, users, conversions, or event-level logs. The program does **not** parse arbitrary raw GA4 CSV metadata/comment preambles, date comparisons, totals or localized numbers. Use a single same-origin property, matching session and key event definitions in both periods.
5. Remove totals, placeholder dimensions such as `(not set)` and `(other)`, personal information, query-string URL paths and off-origin pages. Do not silently merge query-string variants or assume slash variants are identical. If aggregation is needed, perform it before import using documented metric semantics. One unique row per dimension value per period; duplicates fail rather than silently double-count. No extrapolation for omitted or anonymized rows.

## Accepted CSV formats

Normalized UTF-8 (BOM allowed), comma separated, no thousands separators, no decimal commas. Header order may vary. Metric counts are nonnegative integers, except GA4 key events and average position may be decimals. In this bounded schema clicks cannot exceed impressions; engaged sessions cannot exceed sessions. Blank/zero position is accepted only with zero impressions. Empty, header-only reports are accepted but do not establish zero site traffic. CSV fields cannot contain NUL; query labels must have 1–200 characters without control characters. Paths must be ordinary relative filenames; one file at most 1 MiB and 5,000 rows. Invalid or unsupported rows fail the entire import. Reported values are rounded to four decimal places in derived metrics.

```csv
page,clicks,impressions,position
https://example.invalid/vi/,30,2100,8
```

```csv
query,clicks,impressions,position
sample directory,30,3000,8
```

```csv
landing_page,sessions,engaged_sessions,key_events
/vi/,300,150,45
```

GA4 landing path can be an absolute same-origin URL or `/path/`. No query string, fragment, credentials or port; no merging between URL variants. Do not enter actual query phrases containing personal details. The output is JSON or escaped static HTML, never CSV formulas or executable HTML.

## Run the included demo

```sh
python -m pip install .
python examples/demo_analytics.py --output ./reports-demo
web-growth-guard analytics-portfolio --root ./reports-demo --html > ./analytics-dashboard.html
```

The demo creates a new directory of invented community-directory, product and audio-site reports; it refuses to replace an existing directory. No public websites are contacted. To try a single site with sample CSV files:

```sh
web-growth-guard analytics-audit --root examples/analytics --policy analytics.json
web-growth-guard analytics-audit --root examples/analytics --policy analytics.json --html > ./analytics-dashboard.html
```

For actual data, copy the policy shape into a dedicated private input folder and choose the exact filenames/filters. Set `data_mode` to `operator_csv`. This label is declared, not independently authenticated. `reports` can contain one or more of `gsc_pages`, `gsc_queries`, `ga4_landings`, each with distinct previous/current file paths and a human-reviewed `scope` string. Optional threshold overrides are defined in `analytics.DEFAULTS`: minimum impressions 200; low CTR 2%; review position band 4–20; minimum click/session loss 20 and 20% relative decline; minimum 100 sessions in both periods and engagement drop 10 percentage points. These are example heuristics, not universal benchmarks. Tune to your actual site, traffic mix and goals.

To compare multiple sites, use a portfolio JSON `{"policies":["directory.json","store.json"]}` in a private folder containing each policy and its CSV files; up to 12 distinct origins and a bounded shared read budget. Use different filenames for each site. There is no cross-site total or mixed-unit score. The CLI exit status 0 means import and analysis succeeded, **not** that the website has no SEO issues or is safe to deploy.

## Interpret results

- CTR = clicks / impressions; engagement rate = engaged sessions / sessions. The ratio of sums is used for exported-row summaries, not an arithmetic mean of row percentages. Zero denominators are unknown, not 0%. Average position is the exported observation, not an exact rank.
- Page and query dimensions are independent; never sum them together. The report gives separate observed exported-row totals, matched-row totals and changes. Missing/added rows are listed separately (bounded examples); they are unknown in the other period, not zero. Exported rows may omit anonymized/truncated queries, and do not establish property totals. An empty or unmatched input does not establish a successful marketing outcome.
- `GSC_LOW_CTR` is a review suggestion. The hypothetical extra clicks are `(impressions × chosen CTR / 100) - observed clicks` at unchanged impressions, floored at zero. They are not expected traffic, forecast or revenue. Do not add these scenarios across page and query dimensions.
- `GSC_POSITION_REVIEW` identifies a page/query inside the configured average-position band with enough impressions, not a prediction of a first-page ranking.
- `GSC_CLICK_DECLINE`, `GA4_SESSION_DECLINE` and `GA4_ENGAGEMENT_DECLINE` flag matched-row threshold crossings. Diagnose demand, seasonality, traffic mix, tracking changes and website changes independently. No causal impact or statistical significance is established.
- `key_events_per_100_sessions` is **not** the session key event rate or conversion rate. Multiple events per session can exceed 100. GA4 and GSC dates/timezones can differ, and clicks do not equal sessions. No cross-source user attribution is performed.
- The evidence digest binds the selected policy and SHA-256 of CSV bytes read in this run. It is not a signature, Google attestation or proof of how the data was exported. A changed input changes the digest. Keep snapshots stable during a run; reads are not an atomic multi-file transaction.
- Matched examples and review items are bounded (50 and 200 respectively); totals and threshold counts cover all accepted rows. Priorities P1/P2 are threshold-based review work, not deployment gates.

## Optional MCP access

Only the trusted operator may enable analytics with `--analytics-root /absolute/private-csv-folder --analytics-policy /absolute/reviewed-analytics.json` in addition to the existing server `--root` and `--policy`. The CSV root must be separate from the HTML root (neither nested inside the other), and the analytics policy must be outside both. The declared origin must match the startup growth policy. The `analytics_audit` tool takes no arguments and reads fresh evidence each call. It cannot select new roots/files, change thresholds, request credentials, call Google or write anything. Reports may contain query text; only enable for a trusted host with explicit operator approval. The legacy stdio adapter remains a limited protocol subset; official SDK integration is a separate CI check.

## Metric references

- [Search Console Performance report](https://support.google.com/webmasters/answer/7576553)
- [Search Console impressions, position, clicks](https://support.google.com/webmasters/answer/7042828)
- [GA4 Landing page report](https://support.google.com/analytics/answer/12931766)
- [GA4 Traffic acquisition report](https://support.google.com/analytics/answer/12923437)
