"""Ask Claude to draft a LiteTraffic scenario for Vendure from public material only, validate it with
`litetraffic inspect`, and let it repair its own mistakes (max 3 repairs). Everything is saved to ./attempt-N/.
Usage: python draft.py <tag>   (needs ANTHROPIC_API_KEY in ../../../.env; never printed)"""
import json, os, re, shutil, subprocess, sys
from pathlib import Path
import anthropic

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
TAG = sys.argv[1] if len(sys.argv) > 1 else "blind"
MODEL = "claude-sonnet-5-5"
LT = os.environ.get("LITETRAFFIC", "litetraffic")

key = next((m.group(1).strip().strip("\"'") for l in (ROOT / ".env").read_text().splitlines()
            if (m := re.match(r"^ANTHROPIC_API_KEY=(.*)$", l.strip()))), "")
client = anthropic.Anthropic(api_key=key)

docs = (ROOT / "docs" / "scenarios.md").read_text()
schema = (HERE / "shop-schema.graphql").read_text()
example_manifest = (HERE.parent.parent / "pretix" / "scenarios" / "quota" / "manifest.json").read_text()
example_js = (HERE.parent.parent / "pretix" / "scenarios" / "quota" / "journeys.js").read_text()

SYSTEM = "You write LiteTraffic scenario bundles: a manifest.json and a k6 journeys.js. Reply with exactly two fenced blocks, ```json (the manifest) and ```javascript (journeys.js), and nothing else."

TASK = f"""Write a scenario for an online shop built on Vendure (public GraphQL shop API at LT_TARGET + "/shop-api").

RULES the shop must keep (plain language, from the shop owner):
1. A product variant with N units in stock is never sold more than N times, however many shoppers check out at the same moment.
2. A shopper is charged the right total.
3. A shopper who cannot buy gets a clear refusal, never a server error.

WHAT ALREADY EXISTS (do not write it): a fixture setup script creates a brand-new product variant with exactly 5 units and prints this JSON, which your k6 script reads from __ENV.LT_FIXTURE_JSON:
{{"variant_id": "<id>", "stock": 5, "hold_seconds": 2, "expected_total": <integer, price with tax + 5.00 standard shipping, in cents>}}
The shop's sample data has shipping method id "1" (standard, 5.00) and one payment method with code "standard-payment" that accepts any metadata. Shoppers are anonymous: each shopper's session token comes back in the `vendure-auth-token` response header and is sent back as `Authorization: Bearer <token>`.
After the run, LiteTraffic can read the shop with GET requests (final observations). Vendure's GraphQL endpoints also answer GET with ?query=<url-encoded query>. The admin API is at "/admin-api"; an admin credential is available to the observation through the environment variable LT_VENDURE_AUTH (value "Bearer <token>"), so use "headers_env": {{"Authorization": "LT_VENDURE_AUTH"}} and list LT_VENDURE_AUTH in secret_env. The manifest's fixtures.command must be exactly: {{"setup": ["node","setup.mjs"], "teardown": ["sh","teardown.sh"], "setup_timeout_seconds": 40, "teardown_timeout_seconds": 10}}.
Load: 20 shoppers arriving within one second (journeys_per_second, 1 second, rate 20), each doing a full checkout (add the variant to the cart, set customer, shipping address (US), shipping method, move to ArrangingPayment, pay).
Budgets: max_seconds 150, max_requests 220, max_write_attempts 140, max_in_flight 20, max_artifact_bytes 67108864.

A WORKING EXAMPLE for a DIFFERENT app (pretix), to show the format:
--- manifest.json
{example_manifest}
--- journeys.js
{example_js}

LITETRAFFIC SCENARIO DOCUMENTATION:
{docs}

VENDURE SHOP API SCHEMA (SDL):
{schema}
"""

HINT = """
HUMAN REVIEWER'S NOTE (added after a first draft was reviewed): internal stock counters can look fine while customers were still charged for units that do not exist. Rule 1 must be judged by what customers experienced: how many paid orders exist for this run. The runner makes that easy: in an observation path, the literal text __PAID_ORDERS_PATH__ is replaced before the run by a ready-made admin-API path whose JSON answer is {"data":{"orders":{"totalItems": <paid orders at this run's unique total>}}}. Use it as a second observation, with its own assertion name, and keep any ledger check too.
"""
if os.environ.get("HINT"):
    TASK += HINT
messages = [{"role": "user", "content": TASK}]
total = {"input": 0, "output": 0}
for n in range(1, 5):
    out = HERE / f"attempt-{TAG}-{n}"
    out.mkdir(exist_ok=True)
    resp = client.messages.create(model=MODEL, max_tokens=20000, system=SYSTEM, messages=messages)
    text = "".join(b.text for b in resp.content if b.type == "text")
    total["input"] += resp.usage.input_tokens
    total["output"] += resp.usage.output_tokens
    (out / "claude-reply.txt").write_text(text)
    (out / "usage.json").write_text(json.dumps({"model": MODEL, "input_tokens": resp.usage.input_tokens, "output_tokens": resp.usage.output_tokens, "stop_reason": resp.stop_reason}, indent=1))
    m = re.search(r"```json\n(.*?)```", text, re.S)
    j = re.search(r"```(?:javascript|js)\n(.*?)```", text, re.S)
    if not (m and j):
        err = "reply did not contain one ```json block and one ```javascript block"
    else:
        (out / "manifest.json").write_text(m.group(1))
        (out / "journeys.js").write_text(j.group(1))
        shutil.copy(HERE.parent / "scenarios" / "stock" / "setup.mjs", out / "setup.mjs")
        shutil.copy(HERE.parent / "scenarios" / "stock" / "teardown.sh", out / "teardown.sh")
        r = subprocess.run([LT, "inspect", str(out)], capture_output=True, text=True)
        (out / "inspect.txt").write_text(r.stdout + r.stderr)
        err = None if r.returncode == 0 and "ok: False" not in r.stdout else (r.stdout + r.stderr)[-1500:]
    print(f"attempt {n}: {'VALID' if err is None else 'INVALID: ' + err[:200].replace(chr(10), ' | ')}  tokens in={resp.usage.input_tokens} out={resp.usage.output_tokens}")
    if err is None:
        (HERE / f"RESULT-{TAG}.json").write_text(json.dumps({"valid_on_attempt": n, "dir": out.name, "tokens": total}, indent=1))
        break
    messages += [{"role": "assistant", "content": text}, {"role": "user", "content": f"LiteTraffic rejected that bundle:\n{err}\nFix it and reply with the same two fenced blocks."}]
else:
    (HERE / f"RESULT-{TAG}.json").write_text(json.dumps({"valid_on_attempt": None, "tokens": total}, indent=1))
print("total tokens:", total)
