# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Reins, **please do not open a public issue**.

Instead, email **237344440@qq.com** with:

- Description of the vulnerability
- Steps to reproduce
- Potential impact

I will respond within **48 hours** and work with you to fix the issue before any public disclosure.

## Scope

Security issues in the following areas are in scope:

- **Proxy server** (`reins proxy`) — request handling, header forwarding, data leakage
- **Storage** — DuckDB file permissions, data exposure
- **SDK instrumentation** — API key handling, prompt/response logging
- **Budget engine** — bypass of budget limits, race conditions

## Out of Scope

- Vulnerabilities in upstream dependencies (report to the dependency maintainer)
- Issues requiring physical access to the machine running Reins
