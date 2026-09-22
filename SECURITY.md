# Security policy

## Supported versions

LiteTraffic is a developer preview. Security fixes land on `main`.

## Reporting a vulnerability

Please do not open a public issue. Report privately through [GitHub security advisories](https://github.com/rohansx/litetraffic/security/advisories/new), with steps to reproduce and the affected version or commit.

You should get an acknowledgement within a few days. Please give us reasonable time to fix the problem before disclosing it.

## Scope

In scope: the controller escaping its declared scenario directory, leaking credentials into artifacts or output it creates, following redirects or sending requests to origins other than the target, or mishandling fixture cleanup.

Out of scope: behavior of scenario scripts you wrote or chose to run (they are trusted code; see [safety](docs/safety.md)), vulnerabilities in k6 itself (report those to Grafana), and the intentionally faulty demo servers under `examples/`.
