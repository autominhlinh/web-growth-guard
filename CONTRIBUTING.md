# Contributing

Use a dedicated synthetic snapshot and reproduce findings with exact versions,
commands and sanitized output. Do not submit private website files, analytics
exports, account tokens, customer data or full reports from real projects.

Install the package in a virtual environment and run the standard-library test
suite (`python -m unittest discover -s tests -v`). Keep behavior, policy scope,
limits and regression tests aligned. State what ran, what failed and what was
not verified. Do not claim ranking or growth from synthetic examples. The optional
SDK job and public HTTPS smoke are separate evidence levels. Keep the explicit
`release-files.txt` source allowlist current when adding distributable files.
For reproducible installation feedback, see [issue #1](https://github.com/autominhlinh/web-growth-guard/issues/1). Use the bug or compatibility template when opening a distinct issue. For security findings, follow SECURITY.md and use private reporting.
