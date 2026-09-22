from __future__ import annotations

import json
from html import escape


def _count(value: object) -> str:
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)


def _cell(value: object) -> str:
    text = "" if value is None else value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    return f"<td>{escape(text)}</td>"


def _failure_tables(assertions: list[dict]) -> str:
    sections = []
    for item in assertions:
        if item["status"] != "fail":
            continue
        samples = item.get("failures") or [{"expected": item.get("expected"), "actual": item.get("actual")}]
        rows = "".join(
            "<tr>" + "".join(_cell(sample.get(key)) for key in ("sequence", "logical_key", "expected", "actual", "detail")) + "</tr>"
            for sample in samples
        )
        sections.append(
            f"<h3>{escape(str(item['id']))}</h3><table><thead><tr><th>Sample</th><th>Logical key</th>"
            f"<th>Expected</th><th>Actual</th><th>Detail</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    return f'<section class="card"><h2>Failing samples</h2>{"".join(sections)}</section>' if sections else ""


def render_report(result: dict, run: dict) -> str:
    assertions = "".join(
        f"<tr><td>{escape(str(item['id']))}</td><td>{escape(str(item['status']).upper())}</td>"
        f"<td>{escape(_count(item['samples']))}</td></tr>"
        for item in result["assertions"]
    )
    limitations = "".join(f"<li>{escape(str(item))}</li>" for item in result["limitations"]) or "<li>None</li>"
    metrics = result["metrics"]
    durations = metrics.get("http_req_duration_ms", {})
    latency = "".join(
        f"<dt>HTTP {label}</dt><dd>{escape(f'{durations[key]} ms' if key in durations else 'Unavailable')}</dd>"
        for label, key in (("avg", "average"), ("p50", "p50"), ("p95", "p95"), ("max", "max"))
    )
    notes = "".join(f"<li>{escape(str(item))}</li>" for item in result.get("notes", []))
    failure_rate = metrics.get("http_req_failed_rate", {}).get("rate")
    failure_label = f"{failure_rate * 100:.1f}%" if failure_rate is not None else "Unavailable"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>LiteTraffic — {escape(str(run['scenario']))}</title>
<style>
body{{font:16px system-ui,sans-serif;max-width:960px;margin:40px auto;padding:0 20px;color:#17211b;background:#f7f8fa}}
h1{{color:#0e6b3c}} .card{{background:white;border:1px solid #dfe5e1;border-radius:12px;padding:20px;margin:16px 0}}
.verdict{{font-size:2rem;font-weight:750}} table{{width:100%;border-collapse:collapse}} th,td{{padding:10px;text-align:left;border-bottom:1px solid #e5e7eb}}
dl{{display:grid;grid-template-columns:max-content 1fr;gap:8px 20px}} dt{{font-weight:700}}
</style></head><body>
<h1>{escape(str(run['scenario']))}</h1>
<section class="card"><div class="verdict">{escape(str(result['verdict']).upper())}</div>
<dl><dt>Lifecycle</dt><dd>{escape(str(result['lifecycle']))}</dd><dt>Run</dt><dd>{escape(str(result['run_id']))}</dd>
<dt>Seed</dt><dd>{escape(str(run['seed']))}</dd><dt>Target</dt><dd>{escape(str(run['target']))}</dd>
<dt>Engine</dt><dd>{escape(str(run['engine']))}</dd>
<dt>Journeys</dt><dd>{escape(_count(metrics.get('iterations', 0)))} / {escape(_count(result['planned_journeys']))}</dd>
<dt>HTTP requests</dt><dd>{escape(_count(metrics.get('http_reqs', 0)))}</dd>
{latency}<dt>Latency samples</dt><dd>{escape(_count(durations.get('samples', 0)))}</dd><dt>HTTP failure rate</dt><dd>{escape(failure_label)}</dd>
<dt>HTTP throughput</dt><dd>{escape(str(metrics.get('http_reqs_per_second', 'Unavailable')))} req/s</dd></dl></section>
<section class="card"><h2>Assertions</h2><table><thead><tr><th>Assertion</th><th>Status</th><th>Samples</th></tr></thead><tbody>{assertions}</tbody></table></section>
{_failure_tables(result["assertions"])}
<section class="card"><h2>Limitations</h2><ul>{limitations}</ul></section>
<section class="card"><h2>Notes</h2><ul>{notes or "<li>None</li>"}</ul></section>
</body></html>
"""
