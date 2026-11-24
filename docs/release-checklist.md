# Release candidate and publication checklist

The version in pyproject.toml is a candidate, not proof of a public release.

## Local engineering checks

1. Build the wheel using a trusted Python and setuptools environment.
2. Run `python tools/release_check.py --root . --wheel dist/<exact-wheel-name> --output release-preview`.
   This explicitly selects source files, verifies module bytes and wheel RECORD,
   checks MIT license and dependency metadata, and writes an archive/manifest.
   Review the list and contents; the limited pattern scan is not a secret scanner.
3. Install the wheel in a fresh environment without source on PYTHONPATH, run all
   tests and the synthetic demos. Record skips, failures, Python and OS versions.
4. Run `examples/sdk_smoke.py` with the official SDK installed separately, and
   `examples/public_https_smoke.py` only where public HTTPS access is permitted.
   The latter checks example.com transport and robots handling, not SEO quality.
   If unavailable, report NOT VERIFIED, not PASS. The optional manual workflow
   separates external service availability from deterministic regression checks.
5. Verify CI for the exact candidate on GitHub. Linux locally is not evidence
   for macOS, Windows, Codex, Claude Code or other hosts.

## Publication and privacy

Review the intended source and author/committer metadata before making any
repository public. Source export does not include Git objects or workspace data.
The operator controls repository settings and should use a privacy-preserving
commit identity, retain the MIT notice and establish a private reporting channel.
Do not copy internal code, real website or analytics exports into the repository.
Publish a reviewed release only with maintainer approval. Gather genuine bug
reports and usage feedback; do not manufacture adoption metrics or guarantee
external program eligibility. Report actual host checks separately from model use.
