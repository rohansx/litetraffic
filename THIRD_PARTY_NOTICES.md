# Third-party dependencies

The MIT license in this repository applies to LiteTraffic's original code and documentation. Dependencies retain their own licenses; installing them does not relicense them under MIT.

| Component | Use | Upstream license |
|---|---|---|
| [k6](https://github.com/grafana/k6/blob/v2.2.0/LICENSE.md) | Separately installed executable invoked as a child process; no engine source or binary bundled | AGPL-3.0 |
| [HTTPX](https://github.com/encode/httpx/blob/master/LICENSE.md) | Installed by pip for fixture and observer HTTP calls | BSD-3-Clause |
| [Pydantic](https://github.com/pydantic/pydantic/blob/main/LICENSE) | Installed by pip for manifest validation | MIT |
| [pytest](https://github.com/pytest-dev/pytest/blob/main/LICENSE) | Optional development dependency | MIT |
| [Fira Sans / Fira Sans Condensed](site/fonts/LICENSE.txt) | Font files bundled with the landing page in `site/fonts/` | SIL OFL-1.1 |

Transitive dependencies and build tools are supplied by their respective distributions. Consult the licenses in the installed versions when redistributing an environment or container.

LiteTraffic implements its own scenarios and controller. It does not bundle ShadowTraffic or E2B. The existing-E2B target resolver constructs a public application URL; sandbox lifecycle management stays with the caller.
