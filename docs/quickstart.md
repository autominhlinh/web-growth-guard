# Try Web Growth Guard

Public prerelease: [v0.6.0rc1](https://github.com/autominhlinh/web-growth-guard/releases/tag/v0.6.0rc1). MIT, no runtime dependencies, no account or API key required. Python 3.11+ and POSIX filesystem required. Linux is tested. On Windows, try Linux inside WSL using its Linux filesystem (not a mounted Windows drive); WSL/native Windows and macOS are not verified. This is not an HTTP server, SEO rank tracker, or advertising automation product.

## 1. Run the verified tag

```sh
git clone --branch v0.6.0rc1 --single-branch https://github.com/autominhlinh/web-growth-guard.git
cd web-growth-guard
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python examples/demo_regression.py
```

The source install may fetch build requirements. The runtime has no third-party dependencies. The demo copies synthetic fixtures into a temporary directory, injects two SEO errors and exits successfully only when the expected regression is blocked. It does not request a website or modify the original fixtures.

## 2. Explore four offline dashboards

From the same checkout and environment, use a new output parent (do not reuse an existing demo directory):

```sh
mkdir demo-output
python examples/demo_portfolio.py --output demo-output/discovery
python examples/demo_growth.py --output demo-output/marketing
python examples/demo_intelligence.py --output demo-output/intelligence
python examples/demo_analytics.py --output demo-output/analytics
```

Open each `dashboard.html` in your local browser. All inputs are synthetic. Discovery uses an injected HTTP transport; analytics uses generated aggregate CSVs. The reports are not measurements of real search rankings, traffic or revenue.

## 3. Run a local gate on an intentional snapshot

Start from `examples/demo/manifest.json`. Choose only intentionally shareable HTML and declare the expected canonical, indexability and language for each URL. Review the baseline and keep it outside agent-writable or exposed directories.

```sh
web-growth-guard audit --root examples/demo > baseline.json
web-growth-guard gate --root examples/demo --baseline baseline.json
```

Gate exits: 0 CLEAR, 2 BLOCK, 3 REVIEW, 1 invalid input. Never auto-deploy just because a gate returns CLEAR. The scope is only the declared pages and checks.

## 4. Optional official MCP SDK smoke

```sh
python -m pip install 'mcp==1.30.0'
python examples/sdk_smoke.py
```

This tests nine tools with explicitly configured policies, fresh gate results and argument-scope rejection. It does not use a model, account, or website. A compatible host may launch the local stdio command using the operator-reviewed configuration in the README. Specific Codex/Claude Code host support is not established by this SDK check. Do not expose the MCP adapter remotely or enable real analytics for an untrusted host.

## 5. Give feedback

[Maintainer feedback thread](https://github.com/autominhlinh/web-growth-guard/issues/1). Report exact version, OS, commands and minimal synthetic reproduction. Avoid real reports, credentials, private data and personal information. Security findings belong in a private report; see [SECURITY.md](../SECURITY.md).

[Verified tagged CI](https://github.com/autominhlinh/web-growth-guard/actions/runs/45538595499) | [Public HTTPS transport test](https://github.com/autominhlinh/web-growth-guard/actions/runs/45550313355). Neither establishes adoption or SEO improvement.
