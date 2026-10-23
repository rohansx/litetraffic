#!/bin/sh
# Per-run fixture for the pretix studies. Usage: setup.sh voucher|quota
# Resets event `lab` (test mode), then creates exactly the fixture the rule needs:
#   voucher: a voucher with max_usages=1 (price set to 10.00) + a quota far larger than the load
#   quota:   a quota of 5 on the ticket, no voucher
# Prints one JSON object as its last stdout line -> LT_FIXTURE_JSON for k6.
set -eu
MODE="${1:?voucher|quota}"
API="$LT_TARGET/api/v1/organizers/study/events/lab"
: "${LT_PRETIX_AUTH:?set LT_PRETIX_AUTH to 'Token <api token>'}"
H="Authorization: $LT_PRETIX_AUTH"
py() { python3 -c "$1"; }

# Reset: delete every test-mode order, then vouchers and quotas.
for code in $(curl -sf -H "$H" "$API/orders/?page_size=200" | py "import sys,json;[print(o['code']) for o in json.load(sys.stdin)['results']]"); do
  curl -s -o /dev/null -X DELETE -H "$H" "$API/orders/$code/"
done
for id in $(curl -sf -H "$H" "$API/vouchers/" | py "import sys,json;[print(v['id']) for v in json.load(sys.stdin)['results']]"); do
  curl -s -o /dev/null -X DELETE -H "$H" "$API/vouchers/$id/"
done
for id in $(curl -sf -H "$H" "$API/quotas/" | py "import sys,json;[print(q['id']) for q in json.load(sys.stdin)['results']]"); do
  curl -s -o /dev/null -X DELETE -H "$H" "$API/quotas/$id/"
done

ITEM=$(curl -sf -H "$H" "$API/items/" | py "import sys,json;print(json.load(sys.stdin)['results'][0]['id'])")
post() { curl -s -o /dev/null -w '%{http_code}' -X POST -H "$H" -H 'Content-Type: application/json' "$API/$1/" -d "$2"; }

if [ "$MODE" = voucher ]; then
  QBODY="{\"name\":\"big\",\"size\":1000,\"items\":[$ITEM]}"
  CODE="LT$(printf '%s' "$LT_RUN_ID" | tr -cd 'A-Za-z0-9' | tr 'a-z' 'A-Z' | cut -c1-20)"
  VBODY="{\"code\":\"$CODE\",\"max_usages\":1,\"item\":$ITEM,\"price_mode\":\"set\",\"value\":\"10.00\",\"block_quota\":false}"
  rc=$(post quotas "$QBODY"); [ "$rc" = 201 ] || { echo "create quota failed: HTTP $rc" >&2; exit 1; }
  rc=$(post vouchers "$VBODY"); [ "$rc" = 201 ] || { echo "create voucher failed: HTTP $rc" >&2; exit 1; }
  printf '{"mode":"voucher","item":%s,"voucher":"%s","expected_total":"10.00"}\n' "$ITEM" "$CODE"
else
  QBODY="{\"name\":\"five\",\"size\":5,\"items\":[$ITEM]}"
  rc=$(post quotas "$QBODY"); [ "$rc" = 201 ] || { echo "create quota failed: HTTP $rc" >&2; exit 1; }
  printf '{"mode":"quota","item":%s,"voucher":null,"expected_total":"20.00"}\n' "$ITEM"
fi
