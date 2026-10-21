#!/usr/bin/env python3
"""Writes scenarios/<name>/ (manifest.json, journeys.js, setup.py, teardown.sh) from src/. Re-run after editing src/."""
import json
import pathlib
import shutil

HERE = pathlib.Path(__file__).parent
OUT = HERE / "scenarios"
SECRETS = ["LT_KB_AUTH", "LT_KB_KEY", "LT_KB_SECRET", "LT_KB_KEY_B", "LT_KB_SECRET_B"]
OBS_HEADERS = {"Authorization": "LT_KB_AUTH", "X-Killbill-ApiKey": "LT_KB_KEY", "X-Killbill-ApiSecret": "LT_KB_SECRET"}
OK = [200, 201, 204, 400, 401, 403, 404, 409, 422]


def obs(path, assertion, expected):
    return {"path": path, "assertion": assertion, "headers_env": OBS_HEADERS, "expected": expected}


PAY_BY_KEY = "/1.0/kb/payments?externalKey=__KEY__"
BAL_BY_KEY = "/1.0/kb/accounts?externalKey=__KEY__&accountWithBalance=true"

# name -> (mode, journeys, seconds, rate, op, stages, assertions, observations, requests per journey, writes per journey)
SPECS = {
    "refund": ("refund", 10, 1, 10, "refund", ["refund_accepted"], ["clean_response", "refunds_within_paid"],
               [obs(PAY_BY_KEY, "refunds_within_paid", {"/refundedAmount": {"lte": 100}})], 1, 1),
    "refund-seq": ("refund", 10, 10, 1, "refund", ["refund_accepted"], ["clean_response", "refunds_within_paid"],
                   [obs(PAY_BY_KEY, "refunds_within_paid", {"/refundedAmount": {"lte": 100}})], 1, 1),
    "refund-canary": ("refund", 10, 1, 10, "refund", ["refund_accepted"], ["clean_response", "refunds_within_paid"],
                      [obs(PAY_BY_KEY, "refunds_within_paid", {"/refundedAmount": {"lte": 99}})], 1, 1),
    "dup": ("dup", 10, 1, 10, "purchase", ["payment_accepted"], ["clean_response", "one_payment_per_key"],
            [obs(PAY_BY_KEY, "one_payment_per_key", {"/purchasedAmount": 100, "/transactions": {"len": 1}})], 1, 1),
    "dup-seq": ("dup", 10, 10, 1, "purchase", ["payment_accepted"], ["clean_response", "one_payment_per_key"],
                [obs(PAY_BY_KEY, "one_payment_per_key", {"/purchasedAmount": 100, "/transactions": {"len": 1}})], 1, 1),
    "dup-canary": ("dup", 10, 1, 10, "purchase", ["payment_accepted"], ["clean_response", "one_payment_per_key"],
                   [obs(PAY_BY_KEY, "one_payment_per_key", {"/purchasedAmount": 100, "/transactions": {"len": 2}})], 1, 1),
    "privacy": ("privacy", 10, 1, 10, "x_account", ["refused"], ["own_read_works", "no_foreign_data", "clean_response", "foreign_write_not_applied"],
                [obs(PAY_BY_KEY, "foreign_write_not_applied", {"/purchasedAmount": 100, "/transactions": {"len": 1}})], 8, 1),
    "privacy-canary": ("privacy-canary", 10, 1, 10, "x_account", ["refused"], ["own_read_works", "no_foreign_data", "clean_response"],
                       [], 8, 1),
    "pay": ("pay", 10, 1, 10, "pay_invoice", ["payment_accepted"], ["clean_response", "invoice_paid_once"],
            [obs(BAL_BY_KEY, "invoice_paid_once", {"/accountBalance": 0})], 1, 1),
    "pay-seq": ("pay", 10, 10, 1, "pay_invoice", ["payment_accepted"], ["clean_response", "invoice_paid_once"],
                [obs(BAL_BY_KEY, "invoice_paid_once", {"/accountBalance": 0})], 1, 1),
    "pay-canary": ("pay", 10, 1, 10, "pay_invoice", ["payment_accepted"], ["clean_response", "invoice_paid_once"],
                   [obs(BAL_BY_KEY, "invoice_paid_once", {"/accountBalance": 1})], 1, 1),
    "adjust": ("adjust", 10, 1, 10, "adjust", ["adjustment_accepted"], ["clean_response", "line_adjusted_once"],
               [obs(BAL_BY_KEY, "line_adjusted_once", {"/accountBalance": 0})], 1, 1),
    "adjust-seq": ("adjust", 10, 10, 1, "adjust", ["adjustment_accepted"], ["clean_response", "line_adjusted_once"],
                   [obs(BAL_BY_KEY, "line_adjusted_once", {"/accountBalance": 0})], 1, 1),
}

shutil.rmtree(OUT, ignore_errors=True)
for name, (mode, count, secs, rate, op, stages, assertions, observations, reqs, writes) in SPECS.items():
    d = OUT / name
    d.mkdir(parents=True)
    shutil.copy(HERE / "src/journeys.js", d / "journeys.js")
    shutil.copy(HERE / "src/setup.py", d / "setup.py")
    (d / "teardown.sh").write_text("#!/bin/sh\n# Kill Bill accounts are left in place (they are unique per run); nothing to remove.\nexit 0\n")
    expected = {op: OK}
    if mode.startswith("privacy"):
        expected = {o: OK for o in ["own_read", "x_account", "x_payments", "x_invoices", "x_methods", "x_search", "x_bykey", "x_write"]}
    manifest = {
        "schema_version": 1,
        "name": f"killbill-{name}",
        "script": "journeys.js",
        "actors": [{"class": "client", "count": count, "auth_recipe": "api-key-and-secret"}],
        "secret_env": SECRETS,
        "fixtures": {
            "recipe": "fresh-account-per-run",
            "command": {"setup": ["python3", "setup.py", mode], "teardown": ["sh", "teardown.sh"],
                        "setup_timeout_seconds": 40, "teardown_timeout_seconds": 10},
        },
        "journeys": [{"name": "client", "max_requests": reqs, "max_writes": writes, "stages": stages,
                      "expected_statuses": expected}],
        "schedule": {"unit": "journeys_per_second", "phases": [{"name": "burst" if secs == 1 else "one_at_a_time", "seconds": secs, "rate": rate}]},
        "assertions": assertions,
        "observer": "killbill-records",
        "budgets": {"max_seconds": 150, "max_requests": 400, "max_write_attempts": 200,
                    "max_in_flight": count, "max_artifact_bytes": 67108864},
    }
    if observations:
        manifest["observations"] = observations
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
print("wrote", len(SPECS), "scenarios")
