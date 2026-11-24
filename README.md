# Web Growth Guard

**Discover. Optimize. Verify.**

An evidence-based SEO regression gate for developers, web maintainers and AI coding agents. Independent local release candidate (0.6.0rc1; not publicly released), built using Python's standard library. Static checks remain offline; the optional discovery command performs bounded public HTTPS GET requests against an operator-declared origin. No accounts, API keys, telemetry or runtime dependencies for the CLI and limited MCP server. No deployment, advertising API or website editing.

## Release candidate

Run `python examples/demo_regression.py` for the core regression demonstration. The [release checklist](docs/release-checklist.md) describes the wheel/source checks and separate official SDK and public HTTPS smoke tests. A local candidate does not imply that external tests or a public release have passed.

## Why

A redesign or AI-assisted code update can accidentally add `noindex`, point a canonical at the wrong page, remove multilingual alternate links, or drop a page from a sitemap. Instead of inventing a visibility score, Web Growth Guard checks explicit expectations against an exported HTML snapshot and reports new and existing problems before a release. Use it alongside (not instead of) crawler, browser and search console checks.

## Quickstart

Requires Python 3.11+ on Linux or another POSIX filesystem with directory descriptor and no-follow support. Linux is tested locally for this preview; other OSes have not been verified.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python examples/demo_regression.py
web-growth-guard audit --root examples/demo > baseline.json
web-growth-guard gate --root examples/demo --baseline baseline.json
```

The demo intentionally mutates **temporary synthetic files**, never the original fixture directory. The gate reads actual candidate files and compares against an operator-reviewed baseline. The example demonstrates `NOINDEX` and `CANONICAL_MISMATCH`; no actual site is tested.

### Snapshot contract

Copy public, intentionally shareable HTML exports of your own site into a dedicated local snapshot directory. Put a `manifest.json` in that directory. See `examples/demo/manifest.json` for a complete two-language example. Declare the HTTPS origin, each exact page URL, its relative HTML file, expected indexability, expected canonical, language and explicit alternate-language mapping (use `{}` when none is expected). Optionally declare one simple namespaced sitemap `urlset` XML file. Index files, live sitemap downloads, and arbitrary URLs are not supported. Limit 100 pages, 1 MiB per file and 16 MiB total. No symlink traversal, hardlinked file or nonregular file reads.

Keep the same manifest scope across baseline and candidate; changes to scope require independent review and a new baseline. Keep the baseline and manifest policy under maintainer control, outside untrusted agent write access. Never put personal or live application data in the snapshot. Output includes declared URLs, lengths and SHA-256 hashes, but does not echo scraped text. Treat reports as potentially sensitive.

## What it checks

| Check | Scope |
| --- | --- |
| Indexability | Explicit robots/Googlebot and OAI-SearchBot named meta directives; heuristic flags, not crawler policy proof |
| Canonical | Exactly one expected canonical, relative URLs resolved against page URL; base href rejected |
| Metadata | Missing or duplicate title/description and missing basic Open Graph tags |
| Language | HTML lang mismatch, required hreflang links, self-reference, reciprocal links for declared targets |
| Sitemap | Membership of declared indexable pages in a supported local sitemap, nonindexable entries flagged |
| Evidence | SHA-256 of actual files and normalized manifest, deterministic report and coverage |
| Regression gate | BLOCK on current errors or incomplete coverage, REVIEW on newly added warnings, CLEAR otherwise |

CLI exit codes: 0 success/CLEAR; 1 invalid input; 2 errors/BLOCK; 3 REVIEW (gate/compare). `audit` emits JSON even when finding errors. `compare` compares two trusted reports; prefer `gate` for CI because it reads the candidate itself. `CLEAR` does not mean the site is indexed, ranked, secure, or authorized to deploy.

## Public discovery (opt-in)

Declare public pages you are authorized to inspect. The live policy has `origin`, `pages`, and `sitemap` fields like the static manifest, but omits each page's `file` and uses an absolute same-origin sitemap HTTPS URL (or null). See `examples/live-policy.json`, which uses a deliberately nonresolving synthetic domain. Replace it with your own reviewed public scope before a real run.

```sh
web-growth-guard discover --policy reviewed-policy.json > observed.json
web-growth-guard live-gate --policy reviewed-policy.json --baseline approved-live-baseline.json
```

`discover` reports HTTP status, redirects, declared-page content SHA-256, robots access for Googlebot, OAI-SearchBot and GPTBot, X-Robots-Tag directives, HTML/sitemap findings and JSON-LD syntax. It does not impersonate a search crawler. The independent WebGrowthGuard user agent respects its own robots rules. GPTBot's policy is informational: it does not block a search check by itself. The collector requests only declared pages, robots.txt and a declared sitemap, plus bounded same-origin redirects. No automatic link following or sitemap index expansion. Non-200 pages, unsupported encodings, redirects, unavailable robots policy and incomplete coverage fail closed. 404/410 robots is treated as absent. The robots evaluator uses exact product-token groups, wildcard fallback, merged groups, longest-rule selection and allow ties; unsupported rules may make the check unknown. These are bounded approximations of the Robots Exclusion Protocol, not a guarantee of the behavior of any crawler.

Live reports have a separate versioned format and cannot be compared against static reports. Reports include observation time and collection mode (public HTTPS or injected test transport); live-gate rejects synthetic baselines. These fields describe the execution path, not a tamper-proof attestation. `live-gate` collects actual responses; `live-compare` only compares saved, trusted reports. The operator reviews the policy and baseline. A `CLEAR` result is not proof of indexing or permission to deploy. Public external DNS was unavailable during local validation; tests cover synthetic responses and real loopback TLS with a temporary test certificate. No production website was audited. See SECURITY.md.

## Portfolio dashboard (offline)

Create a dedicated report directory containing `portfolio.json` and saved JSON reports. Example manifest:

```json
{"sites":[{"label":"Example site","report":"current.json","baseline":"baseline.json"}]}
```

A baseline may be null. Each report and baseline must have matching scope and format. The portfolio supports up to 12 entries, and local files remain bounded and confined to the specified report directory. Export the report to HTML or JSON:

```sh
web-growth-guard portfolio --root reports > dashboard.html
web-growth-guard portfolio --root reports --json
python examples/demo_portfolio.py --output /absolute/new/demo-directory
```

The example creates CLEAR, REVIEW and BLOCK scenarios with synthetic HTTP responses and no website requests. Open `dashboard.html` locally. The dashboard has no network listener, scripts, remote assets, or write controls. It summarizes coverage, findings, bot policy and baseline differences; it is not live monitoring or actual traffic analytics. Actual site reports may be sensitive and must not be published by default. The demo requires a new directory and never overwrites an existing directory.

## Growth Marketing Toolkit

Use a separately reviewed growth policy (see `examples/growth/growth.json`). It contains the static `site` manifest, declared `landings` with required same-origin CTA targets and a `require_social` flag, and explicit multilingual `clusters`. Up to 50 landings and 30 clusters (2-20 members each) are supported. Each cluster member must be declared indexable and self-canonical; `x_default` is optional and may point to a member. Every member must declare the identical complete alternate map. The cluster audit checks absolute hreflang URLs, language, missing/extra alternatives and normal head metadata. It does not resolve translation quality or support cross-domain clusters, hreflang HTTP headers or sitemap alternates.

```sh
web-growth-guard growth-audit --root examples/growth > approved-growth.json
web-growth-guard growth-gate --root examples/growth --baseline approved-growth.json
web-growth-guard social-preview --root examples/growth --url https://example.invalid/vi/
web-growth-guard campaign --destination https://example.invalid/vi/ --source newsletter --medium email --campaign sample_launch
web-growth-guard campaign-batch --input examples/growth/campaigns.json
python examples/demo_growth.py --output /absolute/new/growth-demo
```

The landing audit checks ordinary H1 markup, viewport declaration, explicitly required CTA anchor targets and social metadata. Missing approved CTA targets and invalid required Open Graph fields block a direct gate; viewport/H1 and Twitter card findings are warnings. It excludes ordinary template/noscript content from head evidence. It does not render CSS or JavaScript, verify that a link works, certify accessibility, fetch share images or predict conversions. `social-preview` deliberately returns a bounded subset of metadata strings as **untrusted text**. Other reports contain derived findings rather than page text. The portfolio now supports `wgg-growth/v1` and shows landing coverage and CTA markup coverage. Gate compares only matching policy scopes and reads the current candidate directly. Reports remain operator-trusted, not signed attestations.

Campaign URLs use an untagged HTTPS destination (no existing query or fragment), required `source`, `medium`, `campaign`, optional `id`, `source_platform`, `content`, `term`. Values are exact lowercase ASCII labels, 1-80 characters; the tool does not silently rewrite them. Do not include personal identifiers, credentials or click IDs. The builder does not visit destinations, create ads, shorten links, generate QR codes, send events or measure traffic. Batch accepts 1-50 unique URLs. Keep campaign naming consistent and configure consent/analytics separately. Google documentation: [Manual tagging](https://support.google.com/analytics/answer/11242870), [Localized versions](https://developers.google.com/search/docs/specialty/international/localized-versions).

## Growth Intelligence and portfolio action queue

Analyze declared static HTML without using search-volume data or making website requests. `intelligence.json` contains:

- `site`: the same static manifest used by the existing SEO/growth checks.
- `entry_pages`: one or more declared indexable self-canonical pages from which to measure navigation; `max_depth`: 1–10 observed anchor hops.
- `topics`: up to 50 operator-defined objects with `id`, lowercase `language`, declared `target`, 1–5 `phrases` (alternatives), and `priority` (`high` or `normal`). The exact phrase matching is Unicode-normalized, case-insensitive, and limited to observed ordinary body text on declared same-language pages. COVERED means a phrase matches the target, OTHER_PAGE means it matches another page in that language, GAP means no match in the complete declared language scope, UNKNOWN means coverage is incomplete. These are lexical observations, not search demand, relevance scores or keyword recommendations.
- `required_links`: up to 200 `{ "from": ..., "to": ... }` declared eligible page pairs. The directed graph counts distinct unqualified static anchors, excludes `nofollow`, `ugc` and `sponsored`, and ignores fragments on otherwise matching URLs. The counts do not model Google's crawling decisions. Query-bearing, external and unsupported links are not followed; unverified targets are not asserted broken. Unreached pages are not asserted to be whole-site orphans. Missing snapshots suppress negative reachability/coverage conclusions.
- `schema_profiles`: mapping of eligible page URLs to arrays of local shape checks: `Product`, `Article`, `BreadcrumbList`, `Organization`, `AudioObject`. Supported JSON-LD has literal Schema.org context, top-level nodes and `@graph`; there is no remote context resolution, arbitrary nested entity discovery or `@id` graph resolution. Product checks name and selected offer/rating/review shapes; Article checks selected recommended headline/author fields; BreadcrumbList checks ordered list items, names and supported URLs. Organization and AudioObject are local project shape checks, not claims of rich-result eligibility. A profile may be MATCHED, MISSING, INCOMPLETE or UNKNOWN. Verify actual page facts and use official validators; never fabricate reviews, ratings or prices.

Run the synthetic three-site demo from the checkout:

    python examples/demo_intelligence.py --output ./reports/intelligence-demo

Use a new output directory; create its parent first. The demo writes only synthetic files. `dashboard.html` shows a prioritized action queue and topic, navigation and schema observations. `verification.json` records the expected injected problems. For a real *reviewed, dedicated, shareable* snapshot, run:

    web-growth-guard intelligence-audit --root /absolute/snapshot --policy intelligence.json
    web-growth-guard growth-plan --root /absolute/snapshot --policy intelligence.json

Both read current HTML directly and exit 2 if any technical error is found, 0 otherwise (review warnings may remain). The plan is ordered P0 (technical errors), P1 (important reviews), P2 (other reviews). It includes an evidence digest, not a signature or a growth forecast. Do not use a phrase match or structured data shape as proof of content quality, indexing, conversion or traffic.

For up to 12 independent origins within one dedicated snapshot root, provide an `intelligence-portfolio.json` with `sites` entries containing `label`, relative `snapshot` directory and relative `policy` within that directory. The command reads the snapshots directly and shares one 16 MiB file-read budget. It outputs a report even when findings remain; it is not a deployment gate.

    web-growth-guard intelligence-portfolio --root ./reports/intelligence-demo --html > ./reports/growth-queue.html

No JavaScript, CSS visibility evaluation, semantic NLP or actual site crawling in this module. Hidden/template/noscript and other inert content are excluded approximately by the static parser; unsupported/malformed markup may limit observations. Keep reports private. See [link best practices](https://developers.google.com/search/docs/crawling-indexing/links-crawlable), [Product](https://developers.google.com/search/docs/appearance/structured-data/product-snippet), [Article](https://developers.google.com/search/docs/appearance/structured-data/article) and [Breadcrumb](https://developers.google.com/search/docs/appearance/structured-data/breadcrumb) for official guidance.

## Local MCP stdio (limited compatibility preview)

This zero-dependency adapter implements only the handshake-era MCP tool subset (2025-11-25 and 2025-06-18), not the newer stateless protocol. It provides five tools by default and six with a trusted baseline: `list_landings`, `growth_audit`, `social_preview`, `campaign_url`, `campaign_batch`, and optionally `growth_gate`. With `--intelligence-policy /absolute/reviewed-intelligence.json`, two extra tools (`intelligence_audit`, `growth_plan`) are advertised, for up to eight tools without analytics. Optional `--analytics-root` and `--analytics-policy` add `analytics_audit`, for up to nine tools total. The intelligence policy must be outside the snapshot and its site manifest must exactly match the startup growth policy. Both tools recompute evidence and accept no root, policy or report arguments. All operate on the operator-selected snapshot and approved URLs. No tool can change the root, policy, baseline or filesystem, call public HTTP, access credentials or perform deployment. Policy and baseline are loaded at startup from reviewed files **outside** the snapshot. For the gate, the baseline must be complete, scope-matched and free of blockers. Restart the server after changing reviewed configuration.

```sh
# In a disposable synthetic setup, outside the snapshot directory:
cp examples/growth/growth.json reviewed-policy.json
web-growth-guard growth-audit --root examples/growth > approved-growth.json
web-growth-guard-mcp --root "$PWD/examples/growth" --policy "$PWD/reviewed-policy.json" --baseline "$PWD/approved-growth.json"
```

The last command is intended to be launched by an MCP host, not used interactively. A local stdio configuration can look like this; review host-specific syntax and substitute actual absolute paths:

```json
{"mcpServers":{"web-growth-guard":{"command":"/absolute/venv/bin/web-growth-guard-mcp","args":["--root","/absolute/snapshot","--policy","/absolute/reviewed-policy.json","--baseline","/absolute/approved-growth.json"]}}}
```

Use dedicated shareable snapshots, never a live application root. Host access to its own OS/tools is outside this server's control. The server returns structured JSON plus JSON text, rejects out-of-scope arguments and does not log page contents. This is a small compatibility adapter, not an official SDK or complete MCP implementation. Real subprocess JSON-RPC tests verify discovery and calls; optional `examples/sdk_smoke.py` checks the official v1 SDK when it is separately installed (`python -m pip install 'mcp>=1.28,<2'`). The SDK was unavailable in the local environment. No actual Codex, Claude Code, Hermes or newer stateless-host integration has been verified. [MCP specification](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle).

## Tests

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python examples/demo_regression.py
```

The GitHub Actions workflow is included as a template; it has not yet run in a GitHub repository. Read SECURITY.md for limits. The local suite includes real JSON-RPC stdio subprocess tests, synthetic HTTP/TLS tests and marketing regression fixtures. The optional official SDK test is a separate CI job; GitHub Actions, public external HTTPS and specific host interoperability remain unverified.

## License

MIT. See LICENSE. The project contains only newly authored code and synthetic fixtures, and has no integrations with existing private websites or operational data.


## Growth Analytics Intelligence

Analyze operator-provided aggregated CSV exports from Search Console (Pages and Queries separately) and GA4 (normalized Landing page session, engagement and key event columns). Two nonoverlapping equal-length periods, configurable review thresholds, matched-row change detection, weighted CTR and engagement, explicit unknowns for omitted rows, and a private offline portfolio dashboard. No Google account/API, scraping, telemetry or website edits.

    web-growth-guard analytics-audit --root examples/analytics --policy analytics.json
    python examples/demo_analytics.py --output ./reports-demo
    web-growth-guard analytics-portfolio --root ./reports-demo --html > ./analytics-dashboard.html

All included CSV fixtures are synthetic. The module does not automatically parse arbitrary localized GA4 exports. Keep real exports and reports outside the repository. See [Analytics import, metrics and privacy guide](docs/analytics.md) for supported headers, policy, interpretation and the explicit MCP opt-in. Hypothetical CTR gaps are arithmetic scenarios, not traffic predictions; key events per 100 sessions is not conversion rate. Missing rows are never treated as zero.
