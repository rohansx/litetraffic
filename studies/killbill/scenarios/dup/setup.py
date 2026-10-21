#!/usr/bin/env python3
"""Per-run fixture for the Kill Bill study. Usage: setup.py <mode>

Creates fresh records under tenant A (account, payment method, payment/invoice as the mode needs) and prints
one JSON object as its last stdout line (becomes LT_FIXTURE_JSON for k6). Needs LT_TARGET, LT_KEY (this run's
unique key, set by run.sh), LT_KB_AUTH, LT_KB_KEY, LT_KB_SECRET. Python standard library only.
"""
import json
import os
import sys
import urllib.error
import urllib.request

MODE = sys.argv[1]
T = os.environ["LT_TARGET"] + "/1.0/kb"
KEY = os.environ["LT_KEY"]


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(T + path, data=data, method=method)
    for k, v in {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": os.environ["LT_KB_AUTH"],
        "X-Killbill-ApiKey": os.environ["LT_KB_KEY"],
        "X-Killbill-ApiSecret": os.environ["LT_KB_SECRET"],
        "X-Killbill-CreatedBy": "litetraffic-setup",
    }.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.status, r.headers.get("Location", ""), r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, "", e.read().decode()


def must(res, what):
    if res[0] not in (200, 201, 204):
        print(f"{what} failed: HTTP {res[0]} {res[2][:200]}", file=sys.stderr)
        sys.exit(1)
    return res


marker = f"lt-private-{KEY}"
code, loc, _ = must(call("POST", "/accounts", {
    "name": marker, "email": f"{marker}@example.com", "currency": "USD", "externalKey": KEY}), "create account")
account = loc.rstrip("/").split("/")[-1]
fx = {"mode": MODE, "key": KEY, "account": account, "marker": marker, "search": marker}

is_default = MODE in ("refund", "dup", "privacy", "privacy-canary")
_, loc, _ = must(call("POST", f"/accounts/{account}/paymentMethods?isDefault={'true' if is_default else 'false'}",
                      {"pluginName": "__EXTERNAL_PAYMENT__"}), "create payment method")
fx["pm"] = loc.rstrip("/").split("/")[-1]

if MODE in ("refund", "privacy", "privacy-canary"):
    must(call("POST", f"/accounts/{account}/payments", {
        "transactionType": "PURCHASE", "amount": 100, "currency": "USD",
        "paymentExternalKey": KEY, "transactionExternalKey": f"{KEY}-setup"}), "purchase")
    _, _, body = must(call("GET", f"/payments?externalKey={KEY}"), "read payment")
    fx["payment"] = json.loads(body)["paymentId"]
elif MODE in ("pay", "adjust"):
    _, _, body = must(call("POST", f"/invoices/charges/{account}?autoCommit=true", [
        {"accountId": account, "amount": 100, "currency": "USD", "description": "lt charge"}]), "external charge")
    item = json.loads(body)[0]
    fx["invoice"] = item["invoiceId"]
    fx["item"] = item["invoiceItemId"]
elif MODE != "dup":
    print("unknown mode " + MODE, file=sys.stderr)
    sys.exit(1)
print(json.dumps(fx))
