#!/bin/sh
# Remove this run's promo code(s) so the next run starts clean. The event,
# product and completed orders are left in place (Hi.Events will not delete an
# event that has completed orders, and there is no bulk order-delete API); the
# next run's fresh promo is what keeps runs independent.
set -eu
API="$LT_TARGET/api"
EVENT_ID="${LT_HIEVENTS_EVENT_ID:-1}"
[ -n "${LT_HIEVENTS_ORG_TOKEN:-}" ] || exit 0
AUTH="Authorization: Bearer $LT_HIEVENTS_ORG_TOKEN"
EXISTING=$(curl -sf -H "$AUTH" -H 'Accept: application/json' "$API/events/$EVENT_ID/promo-codes" \
  | python3 -c "import sys,json;[print(p['id']) for p in json.load(sys.stdin)['data']]" 2>/dev/null || true)
for pid in $EXISTING; do
  curl -s -o /dev/null -w "delete promo $pid: HTTP %{http_code}\n" -X DELETE -H "$AUTH" \
    "$API/events/$EVENT_ID/promo-codes/$pid" || true
done
