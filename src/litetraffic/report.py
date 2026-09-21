from __future__ import annotations

from html import escape


def render_report(result: dict, run: dict) -> str:
    assertions = "".join(
        f"<tr><td>{escape(str(item['id']))}</td><td>{escape(str(item['status']).upper())}</td>"
        f"<td>{escape(str(item['samples']))}</td></tr>"
        for item in result["assertions"]
    )
    limitations = "".join(f"<li>{escape(str(item))}</li>" for item in result["limitations"]) or "<li>None</li>"
    metrics = result["metrics"]
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
<dt>Journeys</dt><dd>{escape(str(metrics.get('iterations', 0)))} / {escape(str(result['planned_journeys']))}</dd>
<dt>HTTP requests</dt><dd>{escape(str(metrics.get('http_reqs', 0)))}</dd></dl></section>
<section class="card"><h2>Assertions</h2><table><thead><tr><th>Assertion</th><th>Status</th><th>Samples</th></tr></thead><tbody>{assertions}</tbody></table></section>
<section class="card"><h2>Limitations</h2><ul>{limitations}</ul></section>
</body></html>
"""
