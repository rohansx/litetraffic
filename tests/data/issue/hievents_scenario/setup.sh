#!/bin/sh
# Per-run fixture for the Hi.Events promo-oversell scenario.
#
# The account, default organizer, event (id 1) and one PAID product are created
# ONCE by the bootstrap in README.md and reused. Each run only needs a FRESH
# single-use promo code so its usage counter starts at zero and runs stay
# independent and repeatable. The scenario's final observation reads that promo's
# own recorded usage count, so a fresh code per run is what isolates the runs.
#
# Contract (same as examples/gitea): reads LT_TARGET / LT_RUN_ID from the
# environment plus LT_HIEVENTS_ORG_TOKEN (the organizer JWT for this target), and
# prints a single JSON object as its last stdout line -> handed to k6 as
# LT_FIXTURE_JSON.
set -eu

API="$LT_TARGET/api"
: "${LT_HIEVENTS_ORG_TOKEN:?set LT_HIEVENTS_ORG_TOKEN to the organizer JWT for this target}"
EVENT_ID="${LT_HIEVENTS_EVENT_ID:-1}"
AUTH="Authorization: Bearer $LT_HIEVENTS_ORG_TOKEN"

# A run-unique, lowercase code (the server lowercases codes anyway). <= 50 chars.
CODE="ltrun$(printf '%s' "$LT_RUN_ID" | tr -cd 'A-Za-z0-9' | tr 'A-Z' 'a-z' | cut -c1-30)"
[ "$CODE" != "ltrun" ] || CODE="ltrunpromo"

# The event's default "Tickets" category carries the product and its price.
# (GET /products returns 500 on v1.9.0-beta, so read the category instead.)
CATS=$(curl -sf -H "$AUTH" -H 'Accept: application/json' "$API/events/$EVENT_ID/product-categories")
PRODUCT_ID=$(printf '%s' "$CATS" | python3 -c "import sys,json;print(json.load(sys.stdin)['data'][0]['products'][0]['id'])")
PRICE_ID=$(printf '%s' "$CATS" | python3 -c "import sys,json;print(json.load(sys.stdin)['data'][0]['products'][0]['prices'][0]['id'])")

# Make sure the event is published, or the public order API rejects every order.
curl -sf -o /dev/null -X PUT -H "$AUTH" -H 'Content-Type: application/json' \
  "$API/events/$EVENT_ID/status" -d '{"status":"LIVE"}' >/dev/null 2>&1 || true

# Drop any leftover promo codes so the list holds exactly this run's code at /data/0.
EXISTING=$(curl -sf -H "$AUTH" -H 'Accept: application/json' "$API/events/$EVENT_ID/promo-codes" \
  | python3 -c "import sys,json;[print(p['id']) for p in json.load(sys.stdin)['data']]" 2>/dev/null || true)
for pid in $EXISTING; do
  curl -sf -o /dev/null -X DELETE -H "$AUTH" "$API/events/$EVENT_ID/promo-codes/$pid" >/dev/null 2>&1 || true
done

# The single-use, 100%-off promo. 100% avoids any Stripe interaction on completion.
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  "$API/events/$EVENT_ID/promo-codes" \
  -d "{\"code\":\"$CODE\",\"discount_type\":\"PERCENTAGE\",\"discount\":100,\"max_allowed_usages\":1,\"applicable_product_ids\":[]}")
[ "$code" = 201 ] || { echo "create promo: HTTP $code" >&2; exit 1; }

printf '{"event_id":%s,"product_id":%s,"price_id":%s,"promo_code":"%s"}\n' \
  "$EVENT_ID" "$PRODUCT_ID" "$PRICE_ID" "$CODE"
